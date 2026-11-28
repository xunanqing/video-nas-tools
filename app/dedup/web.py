import shutil
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from app.config import media_tools
from .cache import FeatureCache, VideoFile
from .cli import ScanOptions, default_cache, scan
from .media import DecodeMode
from .review import Review, describe_video, inspect_video, recommend

DISPLAY_LIMIT = 200


def scan_folder(
    root: Path, path: Path, threshold: float, exact_only: bool, decode_mode: DecodeMode = "auto",
    *, excluded_dirs: tuple[str, ...] = (), workers: int = 1,
) -> tuple[dict, Review]:
    if not exact_only and not all(shutil.which(binary) for binary in media_tools()):
        raise FileNotFoundError("内容查重需要 FFmpeg 和 FFprobe，也可以选择仅查找相同文件")

    matches = []
    summary = {}
    snapshots: dict[Path, VideoFile] = {}

    def collect(record: dict) -> None:
        if record["type"] == "summary":
            summary.update(record)
        elif len(matches) < DISPLAY_LIMIT:
            paths = record["paths"]
            if len(paths) > DISPLAY_LIMIT:
                paths = sorted(paths, key=lambda item: describe_video(snapshots[Path(item)], {})["file_time"])
            matches.append({
                **record,
                "path_count": len(paths),
                "paths": [Path(item).relative_to(path).as_posix() for item in paths[:DISPLAY_LIMIT]],
            })

    cache_path = default_cache()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(cache_path, timeout=10)) as db:
        options = ScanOptions([path], threshold=threshold, exact_only=exact_only, decode_mode=decode_mode,
                              excluded_dirs=excluded_dirs, workers=workers)
        scan(options, FeatureCache(db), collect, snapshots=snapshots)
    return _build_review(root, path, matches, summary, snapshots)


def _build_review(
    root: Path, folder: Path, matches: list[dict], summary: dict, snapshots: dict[Path, VideoFile],
) -> tuple[dict, Review]:
    files, descriptions, unavailable = {}, {}, set()
    can_probe = shutil.which(media_tools()[1]) is not None
    exact_metadata = {}
    groups, visible_matches = [], []
    memberships = defaultdict(list)
    for match in matches:
        digest = match.get("sha256")
        for relative in match["paths"]:
            if relative in files or relative in unavailable:
                continue
            video = snapshots[folder / relative]
            try:
                video.verify()
                metadata = exact_metadata.get(digest)
                if metadata is None:
                    metadata = inspect_video(video.path) if can_probe else {}
                    if digest and metadata:
                        exact_metadata[digest] = metadata
                video.verify()
            except OSError:
                unavailable.add(relative)
                continue
            files[relative] = video
            descriptions[relative] = {
                **describe_video(video, metadata), "path": video.path.relative_to(root).as_posix(),
                "thumbnail_version": f"{video.mtime_ns}-{video.ctime_ns}-{video.size}",
            }

        group = tuple(relative for relative in match["paths"] if relative in files)
        if len(group) < 2:
            continue
        for relative in group:
            memberships[relative].append(len(groups))
        groups.append(group)
        visible_matches.append({
            **match, "paths": list(group),
            "path_count": match["path_count"] - (len(match["paths"]) - len(group)),
        })
    visible_files = {key: descriptions[key] for key in memberships}
    return {
        "summary": summary, "matches": visible_matches, "files": visible_files,
        "selected": recommend(visible_files, memberships),
    }, Review(root, {key: files[key] for key in memberships}, groups, memberships)


def invalidate_features(path: Path) -> None:
    cache_path = default_cache()
    if cache_path.is_file():
        with closing(sqlite3.connect(cache_path, timeout=10)) as db:
            FeatureCache(db).invalidate(path)
