"""全局配置：根目录、ffmpeg 路径、持久化。"""
import json
import os
import shutil
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = BASE_DIR / "settings.json"

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".ts", ".m2ts", ".mts",
    ".wmv", ".webm", ".m4v", ".mpg", ".mpeg", ".3gp", ".vob", ".ogv",
}

MIME_MAP = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".webm": "video/webm",
    ".avi": "video/x-msvideo", ".flv": "video/x-flv",
    ".ts": "video/mp2t", ".m2ts": "video/mp2t", ".mts": "video/mp2t",
    ".wmv": "video/x-ms-wmv", ".mpg": "video/mpeg", ".mpeg": "video/mpeg",
    ".3gp": "video/3gpp", ".vob": "video/dvd", ".ogv": "video/ogg",
}

DEFAULT_ROOT = BASE_DIR / "videos"
DEFAULT_PREFERENCES = {
    "ffmpeg_dir": "",
    "dedup_threshold": 0.96,
    "dedup_decode_mode": "auto",
    "dedup_exact_only": False,
    "dedup_excluded_dirs": [],
    "dedup_workers": 1,
    "processing_workers": 2,
    "output_dir": "",
    "cache_dir": "",
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(settings, dict):
                return settings
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_settings(data: dict) -> None:
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=SETTINGS_FILE.parent, delete=False,
        ) as temp:
            temp_path = Path(temp.name)
            json.dump(data, temp, ensure_ascii=False, indent=2)
        temp_path.replace(SETTINGS_FILE)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def get_root() -> Path:
    root = load_settings().get("root")
    if root and Path(root).is_dir():
        return Path(root)
    DEFAULT_ROOT.mkdir(parents=True, exist_ok=True)
    return DEFAULT_ROOT


def get_preferences() -> dict:
    settings = load_settings()
    return {key: settings.get(key, default) for key, default in DEFAULT_PREFERENCES.items()}


def validate_local_directory(path: str, label: str, *, writable: bool = False) -> str:
    if not path.strip():
        return ""
    directory = Path(path.strip()).expanduser()
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError(f"请输入服务端上存在的{label}绝对路径")
    directory = directory.resolve()
    if writable and not os.access(directory, os.W_OK | os.X_OK):
        raise ValueError(f"{label}不可写，请检查服务端目录权限")
    return str(directory)


def set_root(path: str) -> Path:
    p = Path(path)
    if not p.is_dir():
        raise NotADirectoryError(f"目录不存在: {path}")
    resolved = p.resolve()
    save_settings({**load_settings(), "root": str(resolved)})
    return resolved


def _media_executable(directory: str | Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return Path(directory) / f"{name}{suffix}"


def media_tools() -> tuple[str, str]:
    directory = load_settings().get("ffmpeg_dir")
    if directory:
        return (
            str(_media_executable(directory, "ffmpeg")),
            str(_media_executable(directory, "ffprobe")),
        )
    return os.environ.get("FFMPEG_PATH") or "ffmpeg", os.environ.get("FFPROBE_PATH") or "ffprobe"


def validate_ffmpeg_dir(path: str) -> str:
    directory = validate_local_directory(path, " FFmpeg 目录")
    if directory:
        for name in ("ffmpeg", "ffprobe"):
            binary = _media_executable(directory, name)
            if not binary.is_file() or not shutil.which(str(binary)):
                raise ValueError(f"该目录缺少可执行的 {binary.name}，请选择同时包含 FFmpeg 和 FFprobe 的目录")
    return directory


def set_ffmpeg_dir(path: str) -> str:
    directory = validate_ffmpeg_dir(path)
    save_settings({**load_settings(), "ffmpeg_dir": directory})
    return directory
