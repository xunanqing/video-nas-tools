"""视频查重入口。以 JSON Lines 输出结果，扫描日志写入 stderr。"""

import argparse
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
import hashlib
from itertools import batched
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import sys

from app.config import VIDEO_EXTS, get_preferences, load_settings, media_tools

from .cache import FeatureCache, VideoFile
from .matching import VisualFeature, compare, duration_candidates
from .media import (FRAME_BYTES, DecodeMode,
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
    excluded_dirs: tuple[str, ...] = ()
    workers: int = 1


def discover(roots: list[Path], excluded_dirs: tuple[str, ...] = ()) -> tuple[dict[int, list[VideoFile]], int]:
    by_size: dict[int, list[VideoFile]] = defaultdict(list)
    seen: set[Path] = set()
    errors = 0
    patterns = [Path(rule) for rule in excluded_dirs]
    absolute = {rule for rule in patterns if rule.is_absolute()}
    excluded_names = {rule.name.casefold() for rule in patterns if not rule.is_absolute()}

    def excluded(path: Path) -> bool:
        return any(part.casefold() in excluded_names for part in path.parts) or any(
            path.is_relative_to(rule) for rule in absolute)

    def on_error(exc: OSError) -> None:
        nonlocal errors
        errors += 1
        LOGGER.warning("目录或文件读取失败 %s", exc)

    for root in roots:
        if excluded(root):
            continue
        for directory, dirs, names in os.walk(root, onerror=on_error):
            folder = Path(directory)
            dirs[:] = sorted(name for name in dirs
                             if name.casefold() not in excluded_names
                             and folder / name not in absolute
                             and not (folder / name).is_junction())
            for name in sorted(names):
                path = folder / name
                if name.startswith(".output_tmp_") or path.suffix.lower() not in VIDEO_EXTS or path in seen:
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


def extract_visual_feature(video: VideoFile, samples: int, decode_mode: DecodeMode) -> tuple[float, bytes]:
    video.verify()
    duration = probe_duration(video.path)
    data = extract_gray_frames(video.path, duration, samples, decode_mode)
    video.verify()
    return duration, data


def visual_features(videos, cache: FeatureCache, args: ScanOptions):
    kind = f"gray32-bicubic-display-v1:{args.samples}"
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for batch in batched(videos, args.workers):
            pending = []
            for video in batch:
                cached = None if args.refresh else cache.get(video, kind)
                if cached is not None and cached[0] is not None and len(cached[1]) == args.samples * FRAME_BYTES:
                    pending.append((video, cached, None))
                else:
                    pending.append((video, None, executor.submit(
                        extract_visual_feature, video, args.samples, args.decode_mode)))
            for video, cached, future in pending:
                try:
                    if future is not None:
                        duration, data = future.result()
                        cache.put(video, kind, data, duration)
                    else:
                        video.verify()
                        duration, data = cached
                    yield VisualFeature.build(video, duration, data), future is None
                except (OSError, MediaError, ValueError) as exc:
                    LOGGER.warning("%s 视频抽帧失败，%s", video.path, exc)
                    yield None, False


def emit(record: dict) -> None:
    print(json.dumps(record))


def scan(
    args: ScanOptions, cache: FeatureCache, report: Callable[[dict], None] = emit,
    snapshots: dict[Path, VideoFile] | None = None,
) -> int:
    files_by_size, errors = discover(args.roots, args.excluded_dirs)
    if snapshots is not None:
        snapshots.update((video.path, video) for videos in files_by_size.values() for video in videos)
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
        videos = (video for group in files_by_size.values() for video in group)
        for index, (feature, hit) in enumerate(visual_features(videos, cache, args), start=1):
            visual_hits += hit
            if feature is None:
                errors += 1
            elif sum(feature.usable) >= (args.samples + 1) // 2:
                features.append(feature)
            else:
                low_information += 1
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
    directory = load_settings().get("cache_dir")
    if directory:
        return Path(directory) / "dedup.sqlite3"
    configured = os.environ.get("VIDEO_NAS_DEDUP_CACHE")
    if configured:
        return Path(configured).expanduser()
    state = Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else Path.home()
    return state / ".video-nas-tools" / "dedup.sqlite3"


def main(argv: list[str] | None = None) -> int:
    prefs = get_preferences()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", type=Path, nargs="+", metavar="目录")
    parser.add_argument("--samples", type=int, default=5, help="均匀抽帧数量，默认 5")
    parser.add_argument("--threshold", type=float, default=prefs["dedup_threshold"], help="视觉相似度阈值")
    parser.add_argument("--duration-ratio", type=float, default=0.02, help="时长比例容差，默认 0.02")
    parser.add_argument("--duration-seconds", type=float, default=0.5, help="时长容差下限秒数，默认 0.5")
    parser.add_argument("--cache", type=Path, default=default_cache(), help="本地 SQLite 缓存路径")
    parser.add_argument("--refresh", action="store_true", help="本次扫描重新计算特征")
    parser.add_argument("--exact-only", action=argparse.BooleanOptionalAction,
                        default=prefs["dedup_exact_only"], help="仅查找 SHA-256 相同的文件")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=prefs["dedup_workers"],
                        help="同时抽帧的视频数")
    parser.add_argument("--decode", choices=("auto", "cpu"), default=prefs["dedup_decode_mode"],
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
        for binary in media_tools():
            if not shutil.which(binary):
                parser.error(f"找不到可执行程序 {binary}，请检查 FFmpeg 目录配置、环境变量或 PATH")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(args.cache, timeout=10)) as db:
            options = ScanOptions(
                args.roots, args.samples, args.threshold, args.duration_ratio,
                args.duration_seconds, args.refresh, args.exact_only, decode_mode=args.decode,
                excluded_dirs=tuple(prefs["dedup_excluded_dirs"]), workers=args.workers,
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
