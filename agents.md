# AGENTS.md — video-nas-tools 协作指南

> 面向后续参与本项目的 AI Agent / 开发者。记录项目现状、架构、约束与协作约定。
> 最后更新：2026-11-27

## 1. 项目概览

- **目标**：在 Web 浏览器中批量管理 NAS/本地目录的视频文件，参考 LosslessCut 提供预览、截取、旋转、拼接等能力。
- **技术栈**：Python 3.12+ / FastAPI / uvicorn / ffmpeg + ffprobe；前端为原生 HTML + CSS + JavaScript（**无构建工具、无前端框架**）。
- **运行方式**：单端口本机服务，默认 `http://127.0.0.1:8899`，绑定 `127.0.0.1`。
- **Git**：https://github.com/xunanqing/video-nas-tools.git ，分支 `main`。

## 2. 目录结构

| 路径 | 职责 |
| --- | --- |
| `run.py` | 启动入口：`uvicorn.run("app.main:app", host="127.0.0.1", port=8899, reload=False)` |
| `requirements.txt` | Python 依赖：fastapi、uvicorn、send2trash |
| `app/config.py` | 根目录配置读写、ffmpeg 路径、视频扩展名与 MIME 映射 |
| `app/ffmpeg_service.py` | ffmpeg/ffprobe 封装：探测、截取、旋转、拼接的参数构建与执行 |
| `app/task_queue.py` | 异步任务队列（2 并发）、Task 数据模型、进度与状态 |
| `app/main.py` | FastAPI 应用：全部 API、静态文件挂载、路径安全校验、缩略图缓存 |
| `static/index.html` | 单页应用结构 |
| `static/style.css` | 样式 |
| `static/app.js` | 前端全部交互逻辑与状态 |
| `settings.json` | 本地配置（仅 `root` 字段），被 `.gitignore` 忽略 |
| `videos/` | 本地测试视频，被忽略 |
| `.thumbnails/` | 缩略图缓存，被忽略 |
| `.workbuddy/memory/` | 迭代工作日志（历史决策与踩坑记录），被忽略 |

## 3. 环境与启动

```bash
pip install -r requirements.txt
ffmpeg -version   # ffmpeg / ffprobe 必须在 PATH
python run.py
```

- ffmpeg 路径可由环境变量 `FFMPEG_PATH` / `FFPROBE_PATH` 覆盖。
- 目录选择弹框依赖 Windows（`PowerShell` + `System.Windows.Forms`）；Python 环境无 tkinter，勿改回 tkinter 方案。
- 服务绑定 `127.0.0.1`，**无用户认证**，不要暴露到公网。

## 4. API 清单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 单页应用 |
| GET / POST | `/api/settings` | 读取 / 设置视频根目录 |
| POST | `/api/pick-folder` | 弹出系统文件夹选择框（Windows） |
| GET | `/api/files?path=` | 列出目录内容（相对根目录路径） |
| GET | `/api/stream?path=` | 视频流，支持 HTTP Range / 206 |
| GET | `/api/probe?path=` | ffprobe 元信息（时长、尺寸、旋转角、音轨等） |
| GET | `/api/thumbnail?path=` | 缩略图（带缓存） |
| POST | `/api/thumbnail/invalidate` | 失效指定文件的缩略图缓存 |
| POST | `/api/cut` | 截取任务 |
| POST | `/api/rotate` | 旋转任务（**替换原文件**） |
| POST | `/api/concat` | 拼接任务（多文件、保留原文件） |
| POST | `/api/batch` | 批量截取 / 旋转 |
| DELETE | `/api/delete` | 批量移入回收站 |
| POST | `/api/rename` | 批量重命名（`基础名_序号.扩展名`） |
| GET / DELETE | `/api/tasks` | 任务列表 / 清除已完成任务 |

## 5. 关键约束与设计决策（踩坑记录）

1. **路径安全**：所有 API 的 `path` 参数都是相对根目录的路径，必须经 `safe_resolve()` 校验，禁止 `../` 越界；删除时禁止删除根目录本身。
2. **删除**：用 `send2trash` 移入回收站，不是永久删除。该依赖曾遗漏于 `requirements.txt`，现已补齐。
3. **Windows 文件占用**：`/api/stream` 采用“读一块关一次句柄”的方式，避免 `FileResponse` 长时间占用导致旋转替换原文件失败（`WinError 5`）。旋转使用临时文件 + `Path.replace()` 原子替换 + 失败重试 6 次。
4. **mtime 保持**：旋转替换后用 `os.utime()` 写回原 atime/mtime，避免文件排序被打乱。
5. **旋转累计**：每次旋转先 probe 当前 `display_rotation` 再累加；同一文件加 `asyncio.Lock`，防止快速连点导致只有第一次生效。
6. **缩略图缓存**：缓存 key 含路径哈希 + mtime + size；由于旋转写回原 mtime，缓存不会自动变化，**必须显式调用 `/api/thumbnail/invalidate`** 才能在任务完成后刷新。
7. **拼接**：
   - `copy` 模式先用 `concat_signature()` 预检编码/分辨率/帧率/音轨一致性，不一致返回 400 并建议兼容模式；
   - `reencode` 模式统一画布（补黑边）、帧率、音轨后重编码；
   - 无音轨片段用 `anullsrc` 时必须用 `atrim=duration=<片段时长>` 截断，否则 concat 滤镜永久挂起。
8. **目录弹框置顶**：PowerShell `FolderBrowserDialog` + C# 后台线程（`FindWindowEx("#32770")` → `SetWindowPos(HWND_TOPMOST)` → `AttachThreadInput` + `SetForegroundWindow`）。所有 P/Invoke 必须包 try-catch，否则异常会触发 Windows 错误报告（`wermgr.exe`）抢走焦点，导致弹框被浏览器遮挡。
9. **任务队列**：内存态、2 并发，服务重启后任务记录丢失（设计如此）；`clear()` 只清除已完成/失败任务。
10. **前端**：原生 JS；`localStorage` 保存 `lastDir` 与 `showPreview`；静态资源 URL 带 `?v=` 破缓存，**修改前端后必须 bump `index.html` 中的版本号**；点击文件项即勾选并预览；拼接列表仅支持拖拽排序（无上下按钮）。

## 6. 当前进展

已完成功能：

- 目录浏览、视频预览与 Range 流播放
- 无损（stream copy）/ 精确（重编码）截取
- 快速（`display_rotation`）/ 实际（`transpose` 重编码）旋转，直接替换原文件
- 批量删除、批量重命名、任务队列与进度
- 缩略图 / 列表视图、排序、框选、70:30 可拖拽分栏
- 视频拼接：拖拽排序、`copy` / `reencode` 双模式、保留原文件、输出名自动取首文件名 + `_拼接`
- 路径记忆、预览显隐、文件夹选择弹框置顶
- 功能区 tabs：截取 / 旋转 / 删除 / 重命名 / 拼接

已验证结论：

- 无损拼接 2s + 2s → 4.04s，编码与音轨保留；不兼容素材 `copy` 模式正确返回 400
- 兼容拼接 2s + 1s（无音轨、不同分辨率）→ 3s / 640×360 / 含音轨
- 旋转后 mtime 保持、缩略图可失效刷新
- 弹框实测 `TOPMOST=True` 且 `FOREGROUND=True`

## 7. 待办与风险

- 项目暂无自动化测试和 README（如需面向人类阅读可补充）。
- 无认证机制，仅限本机使用。
- 任务队列为内存态，重启清空。
- 修改前端后若忘记 bump 版本号，用户浏览器可能加载旧缓存。

## 8. 协作约定

- 修改后端 Python 代码后需**重启服务**；静态文件修改只需刷新浏览器（并 bump 版本号）。
- 本地验证可使用 `videos/testdir/` 中的样片。
- 提交前确认 `settings.json`、`videos/`、`.thumbnails/`、`.workbuddy/` 未被纳入版本控制。
- 详细迭代历史见 `.workbuddy/memory/2026-11-27.md`。
