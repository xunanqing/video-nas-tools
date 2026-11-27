"""全局配置：根目录、ffmpeg 路径、持久化。"""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = BASE_DIR / "settings.json"

# 可用环境变量覆盖 ffmpeg/ffprobe 路径
FFMPEG_BIN = os.environ.get("FFMPEG_PATH", "ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_PATH", "ffprobe")

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


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_settings(data: dict) -> None:
    SETTINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_root() -> Path:
    root = load_settings().get("root")
    if root and Path(root).is_dir():
        return Path(root)
    DEFAULT_ROOT.mkdir(parents=True, exist_ok=True)
    return DEFAULT_ROOT


def set_root(path: str) -> Path:
    p = Path(path)
    if not p.is_dir():
        raise NotADirectoryError(f"目录不存在: {path}")
    save_settings({"root": str(p.resolve())})
    return p.resolve()
