"""视频查重入口。以 JSON Lines 输出结果，扫描日志写入 stderr。"""

import argparse
from collections import defaultdict
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import sys

from app.config import VIDEO_EXTS

from .cache import FeatureCache, VideoFile
from .matching import VisualFeature, compare, duration_candidates
from .media import (FFMPEG_BIN, FFPROBE_BIN, FRAME_BYTES, DecodeMode,
                    MediaError, extract_gray_frames, probe_duration)

LOGGER = logging.getLogger(__name__)
EXACT_KIND = "sha256-v1"


@dataclass
class ScanOptions:
    roots: list[Path]
    samples: int = 5
    threshold: float = 0.96
    duration_ratio: float = 0.02
    duration_seconds: float = 0.5
    refresh: bool = False
    exact_only: bool = False
    decode_mode: DecodeMode = "auto"


def discover(roots: list[Path]) -> tuple[dict[int, list[VideoFile]], int]:
    by_size: dict[int, list[VideoFile]] = defaultdict(list)
    seen: set[Path] = set()
    errors = 0

    def on_error(exc: OSError) -> None:
        nonlocal errors
        errors += 1
        LOGGER.warning("目录或文件读取失败 %s", exc)

    for root in roots:
        for directory, dirs, names in os.walk(root, onerror=on_error):
            folder = Path(directory)
            dirs[:] = sorted(name for name in dirs if not (folder / name).is_junction())
            for name in sorted(names):
                path = folder / name
                if path.suffix.lower() not in VIDEO_EXTS or path in seen:
                    continue
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    video = VideoFile.read(path)
                    by_size[video.size].append(video)
                    seen.add(path)
                except OSError as exc:
                    on_error(exc)
    return by_size, errors


def full_digest(video: VideoFile, cache: FeatureCache, refresh: bool) -> tuple[bytes, bool]:
    video.verify()
    cached = None if refresh else cache.get(video, EXACT_KIND)
    if cached is not None:
        return cached[1], True
    with video.path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").digest()
    video.verify()
    cache.put(video, EXACT_KIND, digest)
    return digest, False


def visual_feature(
    video: VideoFile, cache: FeatureCache, samples: int, refresh: bool,
    decode_mode: DecodeMode = "auto",
) -> tuple[VisualFeature, bool]:
    kind = f"gray32-bicubic-display-v1:{samples}"
    video.verify()
    cached = None if refresh else cache.get(video, kind)
    if cached is not None:
        duration, data = cached
        if duration is not None and len(data) == samples * FRAME_BYTES:
            return VisualFeature.build(video, duration, data), True
    duration = probe_duration(video.path)
    data = extract_gray_frames(video.path, duration, samples, decode_mode)
    video.verify()
    cache.put(video, kind, data, duration)
    return VisualFeature.build(video, duration, data), False


def emit(record: dict) -> None:
    print(json.dumps(record))


def scan(args: ScanOptions, cache: FeatureCache, report: Callable[[dict], None] = emit) -> int:
    files_by_size, errors = discover(args.roots)
    total = sum(map(len, files_by_size.values()))
    LOGGER.info("发现 %d 个视频", total)
    digests: dict[Path, bytes] = {}
    exact_hits = exact_groups = 0
    for videos in files_by_size.values():
        if len(videos) < 2:
            continue
        by_digest: dict[bytes, list[Path]] = defaultdict(list)
        for video in videos:
            try:
                digest, hit = full_digest(video, cache, args.refresh)
            except OSError as exc:
                LOGGER.warning("%s 文件哈希失败，%s", video.path, exc)
                errors += 1
                continue
            exact_hits += hit
            digests[video.path] = digest
            by_digest[digest].append(video.path)
        for digest, paths in by_digest.items():
            if len(paths) > 1:
                report({"type": "exact", "sha256": digest.hex(), "paths": [str(path) for path in paths]})
                exact_groups += 1

    visual_hits = low_information = visual_pairs = candidates = 0
    if not args.exact_only:
        features = []
        for index, video in enumerate(
            (video for group in files_by_size.values() for video in group), start=1,
        ):
            try:
                feature, hit = visual_feature(video, cache, args.samples, args.refresh, args.decode_mode)
                visual_hits += hit
                if sum(feature.usable) >= (args.samples + 1) // 2:
                    features.append(feature)
                else:
                    low_information += 1
            except (OSError, MediaError, ValueError) as exc:
                LOGGER.warning("%s 视频抽帧失败，%s", video.path, exc)
                errors += 1
            if index % 100 == 0 or index == total:
                LOGGER.info("抽帧进度 %d/%d", index, total)

        for left, right in duration_candidates(features, args.duration_ratio, args.duration_seconds):
            if left.video.size == right.video.size:
                digest = digests.get(left.video.path)
                if digest is not None and digest == digests.get(right.video.path):
                    continue
            candidates += 1
            match = compare(left, right, args.threshold)
            if match is not None:
                similarity, count = match
                report({
                    "type": "visual",
                    "paths": [str(left.video.path), str(right.video.path)],
                    "durations": [left.duration, right.duration],
                    "similarity": similarity,
                    "compared_samples": count,
                })
                visual_pairs += 1
    report({
        "type": "summary", "files": total, "exact_groups": exact_groups, "visual_pairs": visual_pairs,
        "candidates": candidates, "exact_cache_hits": exact_hits, "visual_cache_hits": visual_hits,
        "low_information": low_information, "errors": errors,
    })
    return int(errors > 0)


def default_cache() -> Path:
    configured = os.environ.get("VIDEO_NAS_DEDUP_CACHE")
    if configured:
        return Path(configured).expanduser()
    state = Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else Path.home()
    return state / ".video-nas-tools" / "dedup.sqlite3"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", type=Path, nargs="+", metavar="目录")
    parser.add_argument("--samples", type=int, default=5, help="均匀抽帧数量，默认 5")
    parser.add_argument("--threshold", type=float, default=0.96, help="视觉相似度阈值，默认 0.96")
    parser.add_argument("--duration-ratio", type=float, default=0.02, help="时长比例容差，默认 0.02")
    parser.add_argument("--duration-seconds", type=float, default=0.5, help="时长容差下限秒数，默认 0.5")
    parser.add_argument("--cache", type=Path, default=default_cache(), help="本地 SQLite 缓存路径")
    parser.add_argument("--refresh", action="store_true", help="本次扫描重新计算特征")
    parser.add_argument("--exact-only", action="store_true", help="仅查找 SHA-256 相同的文件")
    parser.add_argument("--decode", choices=("auto", "cpu"), default="auto",
                        help="抽帧解码模式。auto 自动选择硬件解码并回退到 CPU，cpu 仅用 CPU")
    args = parser.parse_args(argv)
    if not 1 <= args.samples <= 64:
        parser.error("--samples 必须在 1 到 64 之间")
    if not 0 <= args.threshold <= 1 or not 0 <= args.duration_ratio <= 1:
        parser.error("--threshold 与 --duration-ratio 必须在 0 到 1 之间")
    if not 0 <= args.duration_seconds < float("inf"):
        parser.error("--duration-seconds 必须是有限非负数")
    try:
        args.roots = [root.resolve(strict=True) for root in args.roots]
        args.cache = args.cache.resolve()
    except (OSError, RuntimeError) as exc:
        parser.error(str(exc))
    for root in args.roots:
        if not root.is_dir():
            parser.error(f"目录不可访问 {root}")
    if args.cache.suffix.lower() != ".sqlite3":
        parser.error("--cache 必须指向 .sqlite3 文件")
    if not args.exact_only:
        for binary in (FFMPEG_BIN, FFPROBE_BIN):
            if not shutil.which(binary):
                parser.error(f"找不到可执行程序 {binary}，请检查 PATH、FFMPEG_PATH 或 FFPROBE_PATH")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(args.cache, timeout=10)) as db:
            options = ScanOptions(
                args.roots, args.samples, args.threshold, args.duration_ratio,
                args.duration_seconds, args.refresh, args.exact_only, decode_mode=args.decode,
            )
            return scan(options, FeatureCache(db))
    except (OSError, sqlite3.Error) as exc:
        LOGGER.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        LOGGER.info("扫描已中断，已提交的缓存可在下次扫描复用")
        return 130


if __name__ == "__main__":
    sys.exit(main())
