"""ffmpeg/ffprobe 封装：元信息探测、带进度执行。"""
import asyncio
import json
from pathlib import Path

from .config import FFMPEG_BIN, FFPROBE_BIN


async def probe(path: Path) -> dict:
    """返回精简的视频元信息：时长/分辨率/旋转等。"""
    proc = await asyncio.create_subprocess_exec(
        FFPROBE_BIN, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {err.decode(errors='replace')[:500]}")
    data = json.loads(out.decode(errors="replace"))

    v_stream = next((s for s in data.get("streams", [])
                     if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in data.get("streams", [])
                     if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    rotation = 0
    if v_stream:
        for sd in v_stream.get("side_data_list", []):
            if "rotation" in sd:
                rotation = int(sd["rotation"]) % 360
    fps = None
    if v_stream and v_stream.get("avg_frame_rate"):
        try:
            num, den = v_stream["avg_frame_rate"].split("/", 1)
            fps = round(float(num) / float(den), 3) if float(den) else None
        except (ValueError, ZeroDivisionError):
            fps = None
    return {
        "duration": float(fmt.get("duration") or v_stream.get("duration") or 0),
        "width": v_stream.get("width") if v_stream else None,
        "height": v_stream.get("height") if v_stream else None,
        "codec": v_stream.get("codec_name") if v_stream else None,
        "rotation": rotation,
        "size": int(fmt.get("size") or 0),
        "bitrate": int(fmt.get("bit_rate") or 0) if fmt.get("bit_rate") else None,
        # 拼接用：音轨、帧率（无损模式预检 / 兼容模式归一化）
        "has_audio": a_stream is not None,
        "audio_codec": a_stream.get("codec_name") if a_stream else None,
        "fps": fps,
    }


def build_cut_args(src: Path, dst: Path, start: float, end: float,
                   lossless: bool) -> list:
    """构建截取参数。lossless 参考 LosslessCut: -ss 前置 + stream copy。"""
    dur = max(0.0, end - start)
    if lossless:
        return ["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
                "-map", "0", "-c", "copy",
                "-avoid_negative_ts", "make_zero", str(dst)]
    # 转码模式（精确到帧）
    return ["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k", str(dst)]


# 顺时针角度 -> 转码 filter
_ROTATE_VF = {90: "transpose=1", 180: "hflip,vflip", 270: "transpose=2"}


def build_rotate_args(src: Path, dst: Path, degree: int,
                      lossless: bool, cur_ccw: int = 0) -> list:
    """degree 为顺时针角度(90/180/270)。cur_ccw 为文件当前逆时针旋转(0~359)。
    lossless: 覆盖式写入 display matrix 元数据，需在当前角度上累加;
    转码: 实际像素旋转（ffmpeg autorotate 已自动补偿输入元数据，天然累加）。"""
    if degree not in (90, 180, 270):
        raise ValueError(f"无效角度: {degree}")
    if lossless:
        # -display_rotation 覆盖输入值，故写入累计后的最终角度（逆时针）
        ccw = (cur_ccw - degree) % 360
        return ["-display_rotation", str(ccw), "-i", str(src),
                "-map", "0", "-c", "copy", str(dst)]
    return ["-i", str(src), "-vf", _ROTATE_VF[degree],
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "copy", str(dst)]


def concat_signature(info: dict) -> tuple:
    """无损拼接兼容性签名：编码/分辨率/帧率/音轨需一致才能 -c copy 拼接。"""
    return (info.get("codec"), info.get("width"), info.get("height"),
            info.get("fps"), bool(info.get("has_audio")),
            info.get("audio_codec") if info.get("has_audio") else None)


def build_concat_copy_args(list_file: Path, dst: Path) -> list:
    """concat demuxer 无损拼接：要求所有片段编码参数一致。"""
    return ["-f", "concat", "-safe", "0", "-i", str(list_file),
            "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", str(dst)]


def build_concat_filter_args(sources: list, infos: list, dst: Path) -> list:
    """兼容拼接：各输入统一缩放到同一画布/帧率/音轨后 concat 滤镜重编码。
    分辨率取所有片段最大值，短边补黑边，保证任意参数视频均可拼接。"""
    if not sources:
        raise ValueError("拼接源不能为空")
    width = max((i.get("width") or 0) for i in infos) or 1920
    height = max((i.get("height") or 0) for i in infos) or 1080
    width -= width % 2  # H.264 需偶数尺寸
    height -= height % 2
    fps = infos[0].get("fps") or 30

    args = []
    for src in sources:
        args += ["-i", str(src)]
    filters = []
    for idx, info in enumerate(infos):
        filters.append(
            f"[{idx}:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"fps={fps},setpts=PTS-STARTPTS[v{idx}]")
        if info.get("has_audio"):
            filters.append(
                f"[{idx}:a:0]aresample=48000,"
                f"aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a{idx}]")
        else:
            # anullsrc 是无限长音源，必须按片段时长截断，否则 concat 永远不结束
            dur = max(0.1, float(info.get("duration") or 0))
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={dur:.3f}[a{idx}]")
    concat_in = "".join(f"[v{i}][a{i}]" for i in range(len(sources)))
    filters.append(f"{concat_in}concat=n={len(sources)}:v=1:a=1[outv][outa]")
    args += ["-filter_complex", ";".join(filters),
             "-map", "[outv]", "-map", "[outa]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-c:a", "aac", "-b:a", "192k", str(dst)]
    return args


def concat_list_text(sources: list) -> str:
    """生成 ffmpeg concat demuxer 清单（单引号转义 + 正斜杠路径）。"""
    lines = ["ffconcat version 1.0"]
    for src in sources:
        escaped = str(src.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


async def run_ffmpeg(args: list, total_duration: float,
                     on_progress=None) -> None:
    """执行 ffmpeg 并解析 -progress 输出，回调进度(0~1)。"""
    proc = await asyncio.create_subprocess_exec(
        FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
        *args, "-progress", "pipe:1", "-nostats",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    async def read_stderr():
        return (await proc.stderr.read()).decode(errors="replace")

    stderr_task = asyncio.create_task(read_stderr())
    last_pct = -1.0
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        text = line.decode(errors="replace").strip()
        if text.startswith("out_time_us=") and total_duration > 0:
            try:
                t = int(text.split("=", 1)[1]) / 1e6
                pct = min(1.0, max(0.0, t / total_duration))
                if pct > last_pct and on_progress:
                    last_pct = pct
                    on_progress(pct)
            except ValueError:
                pass
    await proc.wait()
    err = await stderr_task
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {err[-800:] if err else '未知错误'}")
    if on_progress:
        on_progress(1.0)
