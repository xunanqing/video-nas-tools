import json
import logging
import math
import subprocess
from pathlib import Path
from typing import Literal

from app.config import FFMPEG_BIN, FFPROBE_BIN

SIDE = 32
FRAME_BYTES = SIDE * SIDE
DecodeMode = Literal["auto", "cpu"]
LOGGER = logging.getLogger(__name__)


class MediaError(Exception):
    pass


def run_tool(binary: str, args: list[str], timeout: float = 30) -> bytes:
    try:
        result = subprocess.run(
            [binary, "-v", "error", *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{binary} 超过 {timeout:g} 秒") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode(errors="replace").strip()[-500:]
        raise MediaError(f"{binary} 退出码 {exc.returncode}，{message}") from exc
    return result.stdout


def probe_duration(path: Path) -> float:
    output = run_tool(FFPROBE_BIN, [
        "-protocol_whitelist", "file", "-select_streams", "V:0",
        "-show_entries", "stream=duration:format=duration", "-of", "json", str(path),
    ])
    info = json.loads(output)
    if not info.get("streams"):
        raise MediaError("没有可用的视频流")
    for metadata in (info["streams"][0], info.get("format", {})):
        try:
            duration = float(metadata.get("duration", "nan"))
        except (TypeError, ValueError):
            continue
        if duration > 0 and math.isfinite(duration):
            return duration
    raise MediaError("无法获取有效的视频时长")


def sample_times(duration: float, samples: int) -> list[float]:
    return [duration * (index / (samples + 1)) for index in range(1, samples + 1)]


def extract_gray_frames(
    path: Path, duration: float, samples: int, decode_mode: DecodeMode = "auto",
) -> bytes:
    positions = sample_times(duration, samples)
    if decode_mode == "auto":
        try:
            return _extract_gray_frames(path, positions, "auto")
        except MediaError as exc:
            LOGGER.info("%s 自动解码未成功，改用 CPU 重新抽帧：%s", path, exc)
    return _extract_gray_frames(path, positions, "none")


def _extract_gray_frames(path: Path, positions: list[float], hwaccel: str) -> bytes:
    frames = bytearray()
    for position in positions:
        frame = run_tool(FFMPEG_BIN, [
            "-nostdin", "-filter_threads", "1", "-protocol_whitelist", "file",
            "-ss", f"{position:.9f}", "-threads", "1",
            "-hwaccel:V:0", hwaccel, "-i", str(path),
            "-map", "0:V:0", "-frames:v", "1",
            "-vf", f"scale={SIDE}:{SIDE}:flags=bicubic,format=gray",
            "-threads", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
        ])
        if len(frame) != FRAME_BYTES:
            raise MediaError(f"{position:.3f} 秒处未取得完整灰度帧")
        frames.extend(frame)
    return bytes(frames)
