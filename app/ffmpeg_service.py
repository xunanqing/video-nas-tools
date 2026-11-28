"""ffmpeg/ffprobe 封装：元信息探测、带进度执行、智能检测分析。"""
import asyncio
import json
import re
from pathlib import Path

from .config import media_tools


async def probe(path: Path, detailed: bool = False) -> dict:
    """返回视频元信息；detailed=True 时附带容器/流标签与章节。"""
    cmd = [media_tools()[1], "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams"]
    if detailed:
        cmd.append("-show_chapters")
    cmd.append(str(path))
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
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
    result = {
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
    if detailed:
        result["format_name"] = fmt.get("format_name")
        result["format_tags"] = fmt.get("tags") or {}
        result["streams"] = [{
            "index": s.get("index"),
            "codec_type": s.get("codec_type"),
            "codec_name": s.get("codec_name"),
            "width": s.get("width"),
            "height": s.get("height"),
            "channels": s.get("channels"),
            "tags": s.get("tags") or {},
        } for s in data.get("streams", [])]
        result["chapters"] = data.get("chapters") or []
    return result


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
        media_tools()[0], "-hide_banner", "-loglevel", "error", "-y",
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


# ---------- 元数据写入 ----------

def build_metadata_args(src: Path, dst: Path,
                        format_tags: dict | None = None,
                        stream_tags: dict | None = None) -> list:
    """仅重写容器/流元数据，stream copy 不重编码。
    stream_tags 形如 {"1": {"language": "eng"}}，键为 ffprobe 的流序号。
    """
    args = ["-i", str(src), "-map", "0", "-c", "copy"]
    for key, value in (format_tags or {}).items():
        key = str(key).strip()
        if key and value is not None:
            args += ["-metadata", f"{key}={value}"]
    for spec, tags in (stream_tags or {}).items():
        spec = str(spec).strip()
        if not re.fullmatch(r"(?:\d+(?::[avs](?::\d+)?)?|[avs]:\d+)", spec):
            raise ValueError(f"无效的流标识: {spec}")
        for key, value in (tags or {}).items():
            key = str(key).strip()
            if key and value is not None:
                args += ["-metadata:s:" + spec, f"{key}={value}"]
    args.append(str(dst))
    return args


# ---------- 智能检测（黑帧 / 静音 / 场景切换） ----------

_NUM = r"(?:-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)"

DETECT_DEFAULTS = {
    "black_min": 1.0,        # 黑帧最短持续秒数
    "black_pix_th": 0.1,     # 像素黑阈值
    "silence_noise": "-40dB",  # 静音噪声容限
    "silence_min": 1.0,      # 静音最短持续秒数
    "scene_threshold": 0.4,  # 场景切换分数阈值
}


async def _analyze(args: list) -> str:
    """执行纯分析型 ffmpeg（不产出文件），返回 stderr 文本。"""
    proc = await asyncio.create_subprocess_exec(
        media_tools()[0], "-hide_banner", "-nostats", "-loglevel", "info", "-y",
        *args, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE)
    err = (await proc.stderr.read()).decode(errors="replace")
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"分析失败: {err[-800:]}")
    return err


def _parse_black(err: str) -> list:
    pat = re.compile(
        rf"black_start:({_NUM})\s+black_end:({_NUM})\s+black_duration:({_NUM})")
    items = []
    for line in err.splitlines():
        for a, b, c in pat.findall(line):
            items.append({"start": float(a), "end": float(b),
                          "duration": float(c)})
    return items


def _parse_silence(err: str) -> list:
    start_re = re.compile(rf"silence_start:\s*({_NUM})")
    end_re = re.compile(
        rf"silence_end:\s*({_NUM}).*?silence_duration:\s*({_NUM})")
    items, pending = [], None
    for line in err.splitlines():
        m = start_re.search(line)
        if m:
            pending = float(m.group(1))
        m = end_re.search(line)
        if m and pending is not None:
            items.append({"start": pending, "end": float(m.group(1)),
                          "duration": float(m.group(2))})
            pending = None
    return items


def _parse_scene(err: str) -> list:
    pat = re.compile(rf"pts_time:({_NUM}).*?lavfi\.scene_score:({_NUM})")
    items = []
    for line in err.splitlines():
        for a, b in pat.findall(line):
            items.append({"time": float(a), "score": float(b)})
    return items


async def detect_media(path: Path, types: list, options: dict | None = None,
                       on_progress=None) -> dict:
    """按类型分析视频，返回区间/时间点列表。各类型独立执行，失败不影响已完成结果。"""
    opts = {**DETECT_DEFAULTS, **(options or {})}
    steps = [t for t in types if t in ("black", "silence", "scene")]
    result: dict = {"types": steps, "duration": 0, "errors": {}}
    result["duration"] = (await probe(path))["duration"]

    for idx, typ in enumerate(steps):
        if on_progress:
            on_progress(idx / max(1, len(steps)))
        try:
            if typ == "black":
                vf = (f"scale=160:-2,blackdetect=d={opts['black_min']}:"
                      f"pix_th={opts['black_pix_th']}")
                err = await _analyze(["-i", str(path), "-vf", vf,
                                      "-an", "-f", "null", "-"])
                result["black"] = _parse_black(err)
            elif typ == "silence":
                af = (f"silencedetect=noise={opts['silence_noise']}:"
                      f"d={opts['silence_min']}")
                err = await _analyze(["-i", str(path), "-af", af,
                                      "-f", "null", "-"])
                result["silence"] = _parse_silence(err)
            elif typ == "scene":
                vf = f"select='gt(scene,{opts['scene_threshold']})',showinfo"
                err = await _analyze(["-i", str(path), "-vf", vf,
                                      "-an", "-f", "null", "-"])
                result["scene"] = _parse_scene(err)
        except Exception as e:  # noqa: BLE001
            result["errors"][typ] = str(e)[:300]
    if on_progress:
        on_progress(1.0)
    return result
