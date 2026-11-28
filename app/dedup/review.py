import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import media_tools
from .cache import VideoFile
from .media import MediaError, run_tool


def _number(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return number if math.isfinite(number) and number > 0 else 0


def _created(tags: dict) -> float | None:
    try:
        value = datetime.fromisoformat(tags.get("creation_time", ""))
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def inspect_video(path: Path) -> dict:
    """Only displayed matches need quality and creation metadata."""
    try:
        info = json.loads(run_tool(media_tools()[1], [
            "-protocol_whitelist", "file", "-select_streams", "V:0",
            "-show_entries", "stream=width,height,duration,bit_rate,codec_name:stream_tags=creation_time:"
            "format=duration:format_tags=creation_time", "-of", "json", str(path),
        ], timeout=10))
    except (MediaError, OSError, ValueError):
        return {}
    stream = next(iter(info.get("streams", [])), {})
    container = info.get("format", {})
    created = _created(container.get("tags", {}))
    if created is None:
        created = _created(stream.get("tags", {}))
    return {
        "width": int(_number(stream.get("width"))),
        "height": int(_number(stream.get("height"))),
        "duration": _number(stream.get("duration")) or _number(container.get("duration")),
        "bitrate": int(_number(stream.get("bit_rate"))),
        "codec": stream.get("codec_name"),
        "created": created,
    }


def describe_video(video: VideoFile, metadata: dict) -> dict:
    modified = video.mtime_ns / 1_000_000_000
    original, source = modified, "modified"
    if video.birthtime is not None and video.birthtime < modified:
        original, source = video.birthtime, "birth"
    elif video.birthtime == modified:
        source = "birth"
    return {
        **metadata, "size": video.size, "file_time": original,
        "original_time": metadata.get("created") if metadata.get("created") is not None else original,
        "time_source": "metadata" if metadata.get("created") is not None else source,
    }


def recommend(files: dict[str, dict], memberships: dict[str, list[int]]) -> list[str]:
    """Mark a file only when a better-ranked, directly matching file is kept."""
    def rank(path: str) -> tuple:
        info = files[path]
        return (
            -info.get("width", 0) * info.get("height", 0),
            -round(info.get("duration", 0), 1),
            info["original_time"], info["file_time"], len(Path(path).name), path,
        )

    kept_groups: set[int] = set()
    selected = []
    for path in sorted(files, key=rank):
        groups = memberships[path]
        if kept_groups.intersection(groups):
            selected.append(path)
        else:
            kept_groups.update(groups)
    return selected


@dataclass
class Review:
    root: Path
    files: dict[str, VideoFile]
    groups: list[tuple[str, ...]]
    memberships: dict[str, list[int]]

    def _verify(self, path: str) -> None:
        video = self.files[path]
        if video.path.resolve(strict=True) != video.path or not video.path.is_relative_to(self.root):
            raise OSError("文件位置已改变")
        video.verify()

    def move_to_trash(self, paths: list[str], trash: Callable[[str], None]) -> dict:
        selected = set(paths)
        if not selected.issubset(self.files):
            raise ValueError("所选文件已不在这次查重结果中，请重新扫描")

        keeper_by_group = [
            next((p for p in group if p in self.files and p not in selected), None)
            for group in self.groups
        ]
        keepers = {}
        for path in dict.fromkeys(paths):
            keeper = next((keeper_by_group[g] for g in self.memberships[path] if keeper_by_group[g]), None)
            if keeper is None:
                raise ValueError(f"请为 {path} 保留至少一个直接匹配的视频")
            keepers[path] = keeper

        for path in {*selected, *keepers.values()}:
            try:
                self._verify(path)
            except OSError as exc:
                raise ValueError(f"文件已变化或无法访问：{path}。请重新扫描") from exc

        deleted, errors = [], []
        for path, keeper in keepers.items():
            try:
                self._verify(path)
                self._verify(keeper)
                trash(str(self.files[path].path))
            except Exception as exc:  # send2trash also reports platform-specific failures
                errors.append(f"{path}: {exc}")
            else:
                deleted.append(path)
                del self.files[path]
        return {"deleted_paths": deleted, "errors": errors}
