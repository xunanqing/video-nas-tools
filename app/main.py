"""Video NAS Tools — NAS 视频批量管理 Web 应用（FastAPI + ffmpeg）。"""
import asyncio
import hashlib
import logging
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ffmpeg_service as ff
from .config import (DEFAULT_ROOT, MIME_MAP, VIDEO_EXTS, get_root,
                     load_settings, set_root)
from .dedup.web import invalidate_features, scan_folder
from .task_queue import TaskQueue

app = FastAPI(title="Video NAS Tools")
queue = TaskQueue(workers=2)
dedup_lock = asyncio.Lock()
picker_lock = asyncio.Lock()
CHUNK_SIZE = 1024 * 1024
LOGGER = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
THUMB_DIR = Path(__file__).resolve().parent.parent / ".thumbnails"
THUMB_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------- 工具 ----------

def rel_posix(p: Path) -> str:
    return p.resolve().relative_to(get_root().resolve()).as_posix()


def safe_resolve(rel: str) -> Path:
    root = get_root().resolve()
    p = (root / rel).resolve()
    if not p.is_relative_to(root):
        raise HTTPException(400, "非法路径")
    return p


def unique_output(p: Path) -> Path:
    """输出文件已存在时自动加序号，避免覆盖。"""
    if not p.exists():
        return p
    for i in range(1, 999):
        cand = p.with_name(f"{p.stem}_{i}{p.suffix}")
        if not cand.exists():
            return cand
    raise HTTPException(409, "输出文件冲突过多")


def require_video(p: Path) -> Path:
    if not p.is_file():
        raise HTTPException(404, "文件不存在")
    if p.suffix.lower() not in VIDEO_EXTS:
        raise HTTPException(400, f"不支持的视频格式: {p.suffix}")
    return p


# ---------- 页面 ----------

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


# ---------- 设置 ----------

@app.get("/api/settings")
async def read_settings():
    return {"root": str(get_root()), "native_folder_picker": sys.platform in {"win32", "darwin"}}


class SettingsReq(BaseModel):
    root: str


@app.post("/api/settings")
async def update_settings(req: SettingsReq):
    try:
        root = set_root(req.root)
    except NotADirectoryError as e:
        raise HTTPException(400, str(e))
    return {"root": str(root)}


# ---------- 原生目录选择 ----------

_PS_PICK = r"""
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Threading;
public class PickerTop {
  [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
  [DllImport("user32.dll")] static extern void SwitchToThisWindow(IntPtr h, bool altTab);
  [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int nCmdShow);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern IntPtr FindWindowEx(IntPtr parent, IntPtr childAfter, string cls, string name);
  static readonly IntPtr TOPMOST = new IntPtr(-1);
  static volatile bool running = false;
  static uint targetPid = 0;

  public static void Start(uint pid) {
    targetPid = pid;
    running = true;
    Thread t = new Thread(Run);
    t.IsBackground = true;
    t.Start();
  }
  public static void Stop() { running = false; }

  static IntPtr FindDialog() {
    IntPtr h = IntPtr.Zero;
    while (true) {
      h = FindWindowEx(IntPtr.Zero, h, "#32770", null);
      if (h == IntPtr.Zero) return IntPtr.Zero;
      uint pid;
      GetWindowThreadProcessId(h, out pid);
      if (pid == targetPid) return h;
    }
  }

  static void Run() {
    IntPtr dlg = IntPtr.Zero;
    while (running && dlg == IntPtr.Zero) {
      dlg = FindDialog();
      if (dlg == IntPtr.Zero) Thread.Sleep(60);
    }
    while (running && dlg != IntPtr.Zero) {
      try {
        SetWindowPos(dlg, TOPMOST, 0, 0, 0, 0, 0x0003);
        if (GetForegroundWindow() != dlg) {
          ShowWindow(dlg, 9);  // SW_RESTORE
          uint fgPid;
          uint fgThread = GetWindowThreadProcessId(GetForegroundWindow(), out fgPid);
          uint ourThread = GetCurrentThreadId();
          if (fgThread != ourThread && fgThread != 0) {
            if (AttachThreadInput(ourThread, fgThread, true)) {
              BringWindowToTop(dlg);
              SetForegroundWindow(dlg);
              SwitchToThisWindow(dlg, true);
              AttachThreadInput(ourThread, fgThread, false);
            }
          } else {
            SetForegroundWindow(dlg);
          }
        }
      } catch (Exception) { }
      Thread.Sleep(150);
    }
  }
}
"@
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.ShowNewFolderButton = $false;
$d.Description = '选择 NAS 视频根目录'
if ($env:VIDEO_NAS_PICKER_INITIAL) { $d.SelectedPath = $env:VIDEO_NAS_PICKER_INITIAL }
[PickerTop]::Start([uint32][System.Diagnostics.Process]::GetCurrentProcess().Id)
$result = $d.ShowDialog()
[PickerTop]::Stop()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath }
"""

_MAC_PICK = """
on run argv
    try
        activate
        set folderPrompt to "选择 NAS 视频根目录"
        if (count of argv) > 0 then
            set initialFolder to POSIX file (item 1 of argv) as alias
            set selectedFolder to choose folder with prompt folderPrompt default location initialFolder
        else
            set selectedFolder to choose folder with prompt folderPrompt
        end if
        return POSIX path of selectedFolder
    on error number -128
        return ""
    end try
end run
"""


class PickReq(BaseModel):
    initial: str = ""
    timeout: float = Field(default=300, ge=2, le=600)


@app.post("/api/pick-folder")
async def pick_folder(req: PickReq):
    """在运行服务的桌面上打开原生目录选择器。"""
    if sys.platform not in {"win32", "darwin"}:
        raise HTTPException(501, "请填写服务端可访问的目录路径，例如 /media/videos，然后保存")
    if picker_lock.locked():
        raise HTTPException(409, "已有目录选择窗口打开")
    async with picker_lock:
        try:
            path = await asyncio.to_thread(_run_picker, req)
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(408, "目录选择已超时") from exc
        except (subprocess.CalledProcessError, OSError) as exc:
            LOGGER.warning("目录选择器启动或运行失败 %s", getattr(exc, "stderr", None) or exc)
            raise HTTPException(503, "无法打开服务端的目录选择器，请直接填写目录路径") from exc
    return {"path": path}


def _run_picker(req: PickReq) -> str | None:
    initial = Path(req.initial) if req.initial else None
    location = str(initial.absolute()) if initial and initial.is_dir() else ""
    if sys.platform == "win32":
        command = ["powershell", "-NoProfile", "-STA", "-Command", _PS_PICK]
        platform_options = {
            "creationflags": subprocess.CREATE_NO_WINDOW,
            "env": {**os.environ, "VIDEO_NAS_PICKER_INITIAL": location},
        }
    else:
        command = ["/usr/bin/osascript", "-e", _MAC_PICK, "--"]
        if location:
            command.append(location)
        platform_options = {}
    completed = subprocess.run(
        command, capture_output=True, check=True, timeout=req.timeout,
        encoding="utf-8", errors="replace", **platform_options,
    )
    return completed.stdout.rstrip("\r\n") or None


# ---------- 视频查重 ----------

class DedupReq(BaseModel):
    path: str = ""
    threshold: float = Field(default=0.96, ge=0, le=1)
    exact_only: bool = False


@app.post("/api/dedup/scan")
async def dedup_scan(req: DedupReq):
    folder = safe_resolve(req.path)
    if not folder.is_dir():
        raise HTTPException(404, "扫描目录不存在")
    if dedup_lock.locked():
        raise HTTPException(409, "已有查重扫描正在运行")
    async with dedup_lock:
        try:
            return await asyncio.to_thread(scan_folder, folder, req.threshold, req.exact_only)
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(503, str(exc)) from exc


# ---------- 批量删除（移入回收站） ----------

class DeleteReq(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=200)


@app.post("/api/delete")
async def delete_files(req: DeleteReq):
    from send2trash import send2trash

    root = get_root().resolve()
    targets = []
    for rel in req.paths:
        p = safe_resolve(rel)
        if p.resolve() == root:
            raise HTTPException(400, "不能删除根目录本身")
        if not p.is_file() and not p.is_dir():
            raise HTTPException(404, f"路径不存在: {rel}")
        targets.append(p)

    def do():
        errors = []
        for p in targets:
            try:
                send2trash(str(p))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{p.name}: {e}")
        return errors

    errors = await asyncio.to_thread(do)
    if errors and len(errors) == len(targets):
        raise HTTPException(500, "全部删除失败: " + "; ".join(errors[:3]))
    return {"deleted": len(targets) - len(errors),
            "failed": len(errors), "errors": errors}


# ---------- 批量重命名 ----------

class RenameReq(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)
    base: str = ""        # 自定义基础名；为空则用第一个文件的文件名（不含扩展名）
    start: int = 1        # 起始序号
    digits: int = 0       # 序号补零位数，0 表示不补零


@app.post("/api/rename")
async def rename_files(req: RenameReq):
    """按 基础名_序号.原扩展名 批量重命名；全部冲突预检通过后才执行。"""
    if req.start < 0:
        raise HTTPException(400, "起始序号不能为负数")
    if req.digits < 0 or req.digits > 6:
        raise HTTPException(400, "补零位数需在 0~6 之间")

    base = req.base.strip()
    if not base:
        base = safe_resolve(req.paths[0]).stem
    # 清理 Windows 文件名非法字符，防止路径穿越
    base = re.sub(r'[\\/:*?"<>|]', "_", base).strip().rstrip(".")
    if not base:
        raise HTTPException(400, "基础文件名无效")

    root = get_root().resolve()
    plan = []
    seen = {}
    for i, rel in enumerate(req.paths):
        src = require_video(safe_resolve(rel))
        num = str(req.start + i).zfill(req.digits) if req.digits else str(req.start + i)
        dst = src.with_name(f"{base}_{num}{src.suffix}")
        if not dst.resolve().is_relative_to(root):
            raise HTTPException(400, f"非法目标路径: {dst.name}")
        key = str(dst).lower()   # Windows 文件系统大小写不敏感
        if key in seen:
            raise HTTPException(409, f"目标文件名重复: {dst.name}")
        seen[key] = i
        plan.append((src, dst))

    # 磁盘冲突预检：目标已存在且不是源文件自身，直接拒绝，避免覆盖
    for src, dst in plan:
        if dst.exists() and dst.resolve() != src.resolve():
            raise HTTPException(409, f"目标文件已存在: {dst.name}（请先处理冲突）")

    def do():
        items, errors = [], []
        for src, dst in plan:
            if src.resolve() == dst.resolve():
                items.append({"from": rel_posix(src), "to": rel_posix(dst), "skipped": True})
                continue
            try:
                src.rename(dst)
                items.append({"from": rel_posix(src), "to": rel_posix(dst), "skipped": False})
            except Exception as e:  # noqa: BLE001
                errors.append(f"{src.name}: {e}")
        return items, errors

    items, errors = await asyncio.to_thread(do)
    return {"renamed": len(items) - sum(1 for x in items if x["skipped"]),
            "skipped": sum(1 for x in items if x["skipped"]),
            "failed": len(errors), "errors": errors, "items": items}


# ---------- 文件浏览 ----------

@app.get("/api/files")
async def list_files(path: str = Query("")):
    rel = "" if path in ("", ".") else path
    d = safe_resolve(rel)
    if not d.is_dir():
        raise HTTPException(404, "目录不存在")
    dirs, files = [], []
    try:
        entries = sorted(d.iterdir(), key=lambda x: x.name.lower())
    except OSError as e:
        raise HTTPException(500, f"读取目录失败: {e}")
    for item in entries:
        try:
            if item.is_dir():
                dirs.append({"name": item.name, "path": rel_posix(item)})
            elif item.suffix.lower() in VIDEO_EXTS and item.is_file():
                if ".rot_tmp_" in item.name:
                    continue  # 旋转任务运行中的临时文件
                st = item.stat()
                files.append({
                    "name": item.name,
                    "path": rel_posix(item),
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                })
        except OSError:
            continue  # 无权限等，跳过
    if d.resolve() == get_root().resolve():
        parent = None
    else:
        parent_rel = d.parent.resolve().relative_to(get_root().resolve())
        parent = "" if str(parent_rel) == "." else parent_rel.as_posix()
    return {"path": rel, "parent": parent,
            "dirs": dirs, "files": files}


def thumb_prefix(p: Path) -> str:
    """缓存文件名前缀只由路径决定，便于旋转后按文件批量失效。"""
    return hashlib.md5(str(p.resolve()).encode()).hexdigest()


def thumb_file(p: Path) -> Path:
    st = p.stat()
    return THUMB_DIR / f"{thumb_prefix(p)}_{int(st.st_mtime * 1000)}_{st.st_size}.jpg"


def invalidate_thumbnail(p: Path) -> None:
    """删除该视频的所有缓存缩略图。

    旋转会保留原 mtime（排序稳定），单靠 mtime 做缓存 key 无法感知内容变化，
    因此必须在替换完成后主动失效。
    """
    prefix = thumb_prefix(p) + "_"
    for cached in THUMB_DIR.glob(prefix + "*.jpg"):
        try:
            cached.unlink()
        except OSError:
            pass


@app.get("/api/thumbnail")
async def thumbnail(path: str):
    """视频缩略图：ffmpeg 取第1秒帧，按 路径+mtime+大小 缓存到 .thumbnails/。"""
    p = require_video(safe_resolve(path))
    out = thumb_file(p)
    if not out.exists():
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", "1", "-i", str(p), "-frames:v", "1",
            "-vf", "scale=240:-2", "-q:v", "5", str(out),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0 or not out.exists():
            raise HTTPException(500, f"缩略图生成失败: {err.decode(errors='replace')[:200]}")
    return FileResponse(out, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


class ThumbInvalidateReq(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=200)


@app.post("/api/thumbnail/invalidate")
async def invalidate_thumbs(req: ThumbInvalidateReq):
    """主动失效指定文件的缩略图缓存（任务完成后由前端调用）。"""
    count = 0
    for rel in req.paths:
        try:
            p = safe_resolve(rel)
            if p.is_file():
                invalidate_thumbnail(p)
                count += 1
        except HTTPException:
            continue
    return {"invalidated": count}


@app.get("/api/probe")
async def probe_file(path: str):
    p = require_video(safe_resolve(path))
    try:
        info = await ff.probe(p)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e))
    info["name"] = p.name
    return info


# ---------- 视频流（支持 Range，供 <video> 播放） ----------

@app.get("/api/stream")
async def stream(path: str, range_: Optional[str] = Header(None, alias="Range")):
    p = require_video(safe_resolve(path))
    mime = MIME_MAP.get(p.suffix.lower(), "application/octet-stream")
    size = p.stat().st_size

    if range_:
        m = re.match(r"bytes=(\d*)-(\d*)", range_.strip())
        if m:
            start = int(m.group(1) or 0)
            end = int(m.group(2)) if m.group(2) else size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                return StreamingResponse(iter([]), status_code=416, headers={
                    "Content-Range": f"bytes */{size}"})
            length = end - start + 1

            def iter_file():
                # 每块单独 open/close：长时间暂停播放时不会一直占用文件句柄，
                # 否则 Windows 上旋转任务无法覆盖该文件（WinError 5）
                offset, remaining = start, length
                while remaining > 0:
                    with open(p, "rb") as f:
                        f.seek(offset)
                        chunk = f.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    offset += len(chunk)
                    remaining -= len(chunk)
                    yield chunk

            return StreamingResponse(iter_file(), status_code=206,
                media_type=mime,
                headers={"Content-Range": f"bytes {start}-{end}/{size}",
                         "Accept-Ranges": "bytes"})

        def iter_full():
            offset = 0
            while offset < size:
                with open(p, "rb") as f:
                    f.seek(offset)
                    chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                offset += len(chunk)
                yield chunk

        return StreamingResponse(iter_full(), media_type=mime,
                                 headers={"Accept-Ranges": "bytes"})


# ---------- 任务提交 ----------

class CutReq(BaseModel):
    path: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    lossless: bool = True


class RotateReq(BaseModel):
    path: str
    degree: int = Field(ge=0, le=359)
    lossless: bool = True


class BatchReq(BaseModel):
    action: str  # cut | rotate
    paths: list[str] = Field(min_length=1, max_length=200)
    start: float = 0
    end: float = 0
    degree: int = 90
    lossless: bool = True


async def submit_cut(rel: str, start: float, end: float, lossless: bool):
    src = require_video(safe_resolve(rel))
    if end <= start:
        raise HTTPException(400, "结束时间必须大于开始时间")
    out = unique_output(src.with_name(
        f"{src.stem}_cut_{start:.1f}-{end:.1f}{src.suffix}"))
    duration = (await ff.probe(src))["duration"]

    async def job(on_progress):
        await ff.run_ffmpeg(
            ff.build_cut_args(src, out, start, end, lossless),
            duration, on_progress)

    return await queue.submit(
        "cut", f"截取 {src.name}", rel, rel_posix(out), job)


_file_locks: dict = {}


def file_lock(path: str) -> asyncio.Lock:
    """同一文件的互斥锁：防止并发任务对同一文件 probe/替换交错。"""
    if path not in _file_locks:
        _file_locks[path] = asyncio.Lock()
    return _file_locks[path]


async def submit_rotate(rel: str, degree: int, lossless: bool):
    """旋转直接替换原文件：先输出到同目录临时文件，成功后原子替换，失败自动清理。
    probe 在任务执行时（锁内）读取当前真实角度后累加，避免连续提交时读到旧状态。"""
    src = require_video(safe_resolve(rel))
    if degree not in (90, 180, 270):
        raise HTTPException(400, "角度必须是 90/180/270")
    tmp = src.with_name(f"{src.stem}.rot_tmp_{uuid.uuid4().hex[:8]}{src.suffix}")
    lock = file_lock(str(src))

    async def job(on_progress):
        async with lock:
            st = src.stat()
            orig_times = (st.st_atime, st.st_mtime)  # 保留原修改时间，排序不变
            info = await ff.probe(src)
            cur_ccw = info["rotation"]  # 执行时刻的当前逆时针角度
            try:
                await ff.run_ffmpeg(
                    ff.build_rotate_args(src, tmp, degree, lossless, cur_ccw),
                    info["duration"], on_progress)
            except Exception:
                tmp.unlink(missing_ok=True)  # ffmpeg 失败：清理半成品，原文件无损
                raise

            # 替换阶段失败时保留已处理好的临时文件（用户可手动恢复），不删除
            for attempt in range(6):
                try:
                    tmp.replace(src)  # 同卷 rename，原子替换
                    break
                except PermissionError:
                    if attempt == 5:
                        raise RuntimeError(
                            f"原文件被占用（可能正在播放或被其他程序打开），请关闭后重试。"
                            f"已处理好的文件保留为: {tmp.name}")
                    await asyncio.sleep(0.5)

            # 写回原时间戳：旋转后修改日期不变，前端按时间排序保持稳定
            import os
            try:
                os.utime(src, orig_times)
            except OSError:
                pass  # 时间戳写回失败不影响旋转结果

            # mtime 未变，缓存 key 无法感知内容变化 → 主动失效旧缩略图
            invalidate_thumbnail(src)
            try:
                await asyncio.to_thread(invalidate_features, src)
            except (OSError, sqlite3.Error) as exc:
                LOGGER.warning("清除查重特征失败 %s", exc)

    return await queue.submit(
        "rotate", f"旋转{degree}°并替换 {src.name}", rel, rel, job)


@app.post("/api/cut")
async def cut(req: CutReq):
    task = await submit_cut(req.path, req.start, req.end, req.lossless)
    return {"taskId": task.id, "status": task.status, "output": task.output}


@app.post("/api/rotate")
async def rotate(req: RotateReq):
    task = await submit_rotate(req.path, req.degree, req.lossless)
    return {"taskId": task.id, "status": task.status, "output": task.output}


@app.post("/api/batch")
async def batch(req: BatchReq):
    tasks = []
    for rel in req.paths:
        if req.action == "cut":
            t = await submit_cut(rel, req.start, req.end, req.lossless)
        elif req.action == "rotate":
            t = await submit_rotate(rel, req.degree, req.lossless)
        else:
            raise HTTPException(400, "action 必须是 cut 或 rotate")
        tasks.append({"taskId": t.id, "output": t.output})
    return {"count": len(tasks), "tasks": tasks}


# ---------- 视频拼接 ----------

class ConcatReq(BaseModel):
    paths: list[str] = Field(min_length=2, max_length=100)  # 按用户指定顺序
    mode: str = "copy"       # copy=无损(编码一致) | reencode=兼容(重编码)
    name: str = ""           # 输出文件名（不含扩展名），为空则自动生成


@app.post("/api/concat")
async def concat_videos(req: ConcatReq):
    """按顺序拼接成一个新文件，原视频保留。提交为单个队列任务。"""
    if req.mode not in ("copy", "reencode"):
        raise HTTPException(400, "mode 必须是 copy 或 reencode")

    # 按顺序解析、校验所有源文件
    sources = [require_video(safe_resolve(rel)) for rel in req.paths]
    if len({str(p).lower() for p in sources}) != len(sources):
        raise HTTPException(400, "拼接列表中存在重复文件")
    first = sources[0]
    infos = [await ff.probe(p) for p in sources]

    if req.mode == "copy":
        sigs = {ff.concat_signature(i) for i in infos}
        if len(sigs) != 1:
            raise HTTPException(
                400, "所选视频的编码/分辨率/帧率/音轨不一致，无法无损拼接；"
                     "请改用「兼容拼接」模式")

    # 输出名：用户指定 > 自动生成，沿用第一个文件的扩展名
    name = req.name.strip()
    if name:
        name = re.sub(r'[\\/:*?"<>|]', "_", name).strip().rstrip(".")
        if not name:
            raise HTTPException(400, "输出文件名无效")
    else:
        name = f"{first.stem}_拼接"
    out = unique_output(first.with_name(name + first.suffix.lower()))
    total_duration = sum(i["duration"] for i in infos)

    list_path = first.with_name(f".concat_{uuid.uuid4().hex[:8]}.txt")

    async def job(on_progress):
        try:
            if req.mode == "copy":
                list_path.write_text(ff.concat_list_text(sources), encoding="utf-8")
                args = ff.build_concat_copy_args(list_path, out)
            else:
                args = ff.build_concat_filter_args(sources, infos, out)
            await ff.run_ffmpeg(args, total_duration, on_progress)
        finally:
            list_path.unlink(missing_ok=True)

    task = await queue.submit(
        "concat", f"拼接 {len(sources)} 个视频 → {out.name}",
        req.paths[0], rel_posix(out), job, sources=req.paths)
    return {"taskId": task.id, "status": task.status, "output": task.output}


@app.get("/api/tasks")
async def tasks():
    return {"tasks": [
        {"id": t.id, "kind": t.kind, "title": t.title, "source": t.source,
         "output": t.output, "sources": t.sources, "status": t.status,
         "progress": round(t.progress, 3), "error": t.error}
        for t in queue.list()]}


@app.delete("/api/tasks")
async def clear_tasks():
    """清除已结束的任务记录，运行中的任务不受影响。"""
    return {"cleared": queue.clear_finished()}


# ---------- 提示默认目录 ----------

@app.on_event("startup")
async def on_startup():
    root = get_root()
    if not load_settings().get("root"):
        print(f"[i] 未配置根目录，默认使用: {root}（可通过设置页修改）")
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError
    except Exception:  # noqa: BLE001
        print("[!] 警告: ffmpeg 不在 PATH 中，视频处理将不可用。"
              "可通过环境变量 FFMPEG_PATH 指定路径。")
