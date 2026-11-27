import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from .cache import FeatureCache
from .cli import ScanOptions, default_cache, scan
from .media import FFMPEG_BIN, FFPROBE_BIN

DISPLAY_LIMIT = 200


def scan_folder(path: Path, threshold: float, exact_only: bool) -> dict:
    if not exact_only and not all(shutil.which(binary) for binary in (FFMPEG_BIN, FFPROBE_BIN)):
        raise FileNotFoundError("内容查重需要 FFmpeg 和 FFprobe，也可以选择仅查找相同文件")

    matches = []
    summary = {}

    def collect(record: dict) -> None:
        if record["type"] == "summary":
            summary.update(record)
        elif len(matches) < DISPLAY_LIMIT:
            paths = record["paths"]
            matches.append({
                **record,
                "path_count": len(paths),
                "paths": [Path(item).relative_to(path).as_posix() for item in paths[:DISPLAY_LIMIT]],
            })

    cache_path = default_cache()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(cache_path, timeout=10)) as db:
        scan(ScanOptions([path], threshold=threshold, exact_only=exact_only), FeatureCache(db), collect)
    return {"summary": summary, "matches": matches}


def invalidate_features(path: Path) -> None:
    cache_path = default_cache()
    if cache_path.is_file():
        with closing(sqlite3.connect(cache_path, timeout=10)) as db:
            FeatureCache(db).invalidate(path)
