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
| `Dockerfile`、`compose.yaml`、`.env.example` | Docker 构建、数据挂载及飞牛 NAS 部署配置 |
| `app/config.py` | 根目录配置读写、ffmpeg 路径、视频扩展名与 MIME 映射 |
| `app/ffmpeg_service.py` | ffmpeg/ffprobe 封装：探测、截取、旋转、拼接的参数构建与执行 |
| `app/task_queue.py` | 异步任务队列（2 并发）、Task 数据模型、进度与状态 |
| `app/tag_store.py` | 本地标签库：多来源、置信度、扩展 meta，智能标签扩展点 |
| `app/dedup/` | 只读查重 CLI、Web 扫描、自动硬解／CPU 抽帧、灰度匹配与 SQLite 缓存 |
| `dedup.bat` | Windows 查重 CLI 启动入口 |
| `tests/test_dedup.py` | 查重单元测试与合成视频集成测试 |
| `docs/video-dedup.md` | VDF 源码核查、查重路线及验证记录 |
| `app/main.py` | FastAPI 应用：全部 API、静态文件挂载、路径安全校验、缩略图缓存 |
| `static/index.html` | 单页应用结构 |
| `static/style.css` | 样式 |
| `static/app.js` | 前端全部交互逻辑与状态 |
| `settings.json` | 本地配置，包含根目录、工具路径、查重默认值与排除目录、并发设置、输出和缓存位置，被 `.gitignore` 忽略 |
| `videos/` | 本地测试视频，被忽略 |
| `.thumbnails/` | 缩略图缓存，被忽略 |
| `.workbuddy/memory/` | 迭代工作日志（历史决策与踩坑记录），被忽略 |

## 3. 环境与启动

```bash
pip install -r requirements.txt
ffmpeg -version   # ffmpeg / ffprobe 必须在 PATH
python run.py
```

- Web 顶栏「配置」可保存 FFmpeg 和 FFprobe 所在的本地目录，Web 与查重 CLI 共用。留空时按 `FFMPEG_PATH` / `FFPROBE_PATH`、PATH 的顺序查找。
- 原生目录选择在服务端桌面打开。Windows 用 PowerShell／Windows Forms，macOS 用系统自带的 `osascript`。Linux／NAS 无桌面环境时手填服务端挂载目录，容器内使用容器挂载路径。
- 服务绑定 `127.0.0.1`，**无用户认证**，不要暴露到公网。
- 容器内监听 `0.0.0.0:8899`，Compose 宿主端口默认只绑定回环地址，可改成 NAS 局域网 IP。视频默认只读挂载到 `/media/videos`，持久数据挂载到 `/data`。详见 [飞牛 NAS 部署指南](README.md#飞牛-nasfnosdocker-部署)。

## 4. API 清单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 单页应用 |
| GET / POST | `/api/settings` | 读取 / 设置视频根目录 |
| POST | `/api/settings/ffmpeg` | 设置 FFmpeg 与 FFprobe 所在目录，空字符串恢复自动查找 |
| POST | `/api/settings/preferences` | 保存查重默认值与排除目录、并发、输出及缓存等设置 |
| POST | `/api/pick-folder` | 在 Windows／macOS 服务端弹出系统文件夹选择框 |
| POST | `/api/dedup/scan` | 只读扫描当前目录及子目录并返回匹配结果 |
| POST | `/api/dedup/trash` | 按查重结果和显式选择批量移入回收站，校验文件状态与保留项 |
| GET | `/api/files?path=` | 列出目录内容（相对根目录路径） |
| GET | `/api/stream?path=` | 视频流，支持 HTTP Range / 206 |
| GET | `/api/probe?path=` | ffprobe 元信息（时长、尺寸、旋转角、音轨等） |
| GET | `/api/thumbnail?path=` | 缩略图（带缓存） |
| POST | `/api/thumbnail/invalidate` | 失效指定文件的缩略图缓存 |
| POST | `/api/cut` | 截取任务 |
| POST | `/api/rotate` | 旋转任务（**替换原文件**） |
| POST | `/api/concat` | 拼接任务（多文件、保留原文件） |
| POST | `/api/batch` | 批量截取 / 旋转 |
| GET / POST | `/api/metadata` | 完整元数据（容器/流标签、章节）/ 编辑元数据（走队列） |
| GET / POST / DELETE | `/api/tags` | 标签读取（含建议）/ 新增 / 删除 |
| GET / POST | `/api/detect` | 检测结果缓存读取 / 提交检测任务（黑帧/静音/场景） |
| POST | `/api/delete` | 批量移入回收站 |
| POST | `/api/rename` | 批量重命名（`基础名_序号.扩展名`） |
| GET / DELETE | `/api/tasks` | 任务列表 / 清除已完成任务 |

## 5. 关键约束与设计决策（踩坑记录）

1. **路径安全**：所有 API 的 `path` 参数都是相对根目录的路径，必须经 `safe_resolve()` 校验，禁止 `../` 越界；删除时禁止删除根目录本身。
2. **删除**：用 `send2trash` 移入回收站，不是永久删除。该依赖曾遗漏于 `requirements.txt`，现已补齐。
3. **Windows 文件占用**：`/api/stream` 采用“读一块关一次句柄”的方式，避免 `FileResponse` 长时间占用导致旋转替换原文件失败（`WinError 5`）。旋转使用临时文件 + `Path.replace()` 原子替换 + 失败重试 6 次。
4. **mtime 保持**：旋转替换后用 `os.utime()` 写回原 atime/mtime，避免文件排序被打乱。
5. **旋转累计**：每次旋转先 probe 当前 `display_rotation` 再累加；同一文件加 `asyncio.Lock`，防止快速连点导致只有第一次生效。
6. **缩略图缓存**：缓存 key 含路径哈希、mtime、ctime、size。旋转与元数据编辑仍显式清除当前目录里的旧缓存。切换缓存目录后在新目录生成缓存。
7. **拼接**：
   - `copy` 模式先用 `concat_signature()` 预检编码/分辨率/帧率/音轨一致性，不一致返回 400 并建议兼容模式；
   - `reencode` 模式统一画布（补黑边）、帧率、音轨后重编码；
   - 无音轨片段用 `anullsrc` 时必须用 `atrim=duration=<片段时长>` 截断，否则 concat 滤镜永久挂起。
8. **目录弹框置顶**：PowerShell `FolderBrowserDialog` + C# 后台线程（`FindWindowEx("#32770")` → `SetWindowPos(HWND_TOPMOST)` → `AttachThreadInput` + `SetForegroundWindow`）。所有 P/Invoke 必须包 try-catch，否则异常会触发 Windows 错误报告（`wermgr.exe`）抢走焦点，导致弹框被浏览器遮挡。
9. **任务队列**：内存态，默认 2 并发，可在配置中即时调整。缩减上限时已有任务继续运行。根目录外的截取和拼接结果可通过已完成的任务 ID 访问。服务重启后任务记录丢失，`clear()` 只清除已完成/失败任务。
10. **前端**：原生 JS；`localStorage` 保存 `lastDir` 与 `showPreview`；静态资源 URL 带 `?v=` 破缓存，**修改前端后必须 bump `index.html` 中的版本号**；点击文件项即勾选并预览；拼接列表仅支持拖拽排序（无上下按钮）。
11. **标签与智能标签扩展**：标签存于 `.workbuddy/tags.json`，按相对路径索引，与容器元数据解耦；每条标签含 `source`（manual/auto/detected/imported）、`confidence`、`meta`。智能标签后续只需在 `tag_store.suggest()` 接入规则/模型并返回同结构建议，前端与 API 无需改动。添加新来源时同步更新 `VALID_SOURCES`。
12. **智能检测**：`blackdetect` / `silencedetect` 的输出在 **stderr**，`run_ffmpeg()` 的 `-loglevel error` 会压制，故检测走独立 `_analyze()`（`-loglevel info`）+ 正则解析；正则中的 `_NUM` 必须是**非捕获组**，否则嵌套捕获会导致解包失败。检测结果按「路径+mtime+size」缓存于 `.workbuddy/detections.json`。

## 6. 当前进展

已完成功能：

- 目录浏览、视频预览与 Range 流播放
- 无损（stream copy）/ 精确（重编码）截取
- 快速（`display_rotation`）/ 实际（`transpose` 重编码）旋转，直接替换原文件
- 批量删除、批量重命名、任务队列与进度
- 缩略图 / 列表视图、排序、框选、70:30 可拖拽分栏
- 视频拼接：拖拽排序、`copy` / `reencode` 双模式、保留原文件、输出名自动取首文件名 + `_拼接`
- 智能检测：黑帧 / 静音 / 场景切换，结果可点击定位
- 元数据读取与编辑、本地标签库（智能标签扩展点）
- 路径记忆、预览显隐、文件夹选择弹框置顶
- 顶栏「配置」可筛选文件列表显示的视频扩展名，默认全选，设置保存在当前浏览器
- 功能区 tabs：截取 / 旋转 / 删除 / 重命名 / 拼接 / 检测 / 元数据

已验证结论：

- 无损拼接 2s + 2s → 4.04s，编码与音轨保留；不兼容素材 `copy` 模式正确返回 400
- 兼容拼接 2s + 1s（无音轨、不同分辨率）→ 3s / 640×360 / 含音轨
- 旋转后 mtime 保持、缩略图可失效刷新
- 弹框实测 `TOPMOST=True` 且 `FOREGROUND=True`

## 7. 待办与风险

- 已有中文 README 与查重自动化测试。现有 Web 功能仍需手动回归验证。
- 无认证机制，仅限本机使用。
- 任务队列为内存态，重启清空。
- 修改前端后若忘记 bump 版本号，用户浏览器可能加载旧缓存。

## 8. 协作约定

- 修改后端 Python 代码后需**重启服务**；静态文件修改只需刷新浏览器（并 bump 版本号）。
- 本地验证可使用 `videos/testdir/` 中的样片。
- 提交前确认 `settings.json`、`videos/`、`.thumbnails/`、`.workbuddy/` 未被纳入版本控制。
- 详细迭代历史见 `.workbuddy/memory/2026-11-27.md`。

## 9. 视频查重协作交接

- 第一阶段提供 `python -m app.dedup.cli` 和 `dedup.bat`。支持多目录递归、SHA-256 全文件查重、CPU 32×32 灰度比较与本地 SQLite 特征复用。扫描只读视频并输出 JSON Lines。
- Web 和 CLI 抽帧默认由 FFmpeg 自动选择可用的硬解后端。自动模式无法完成抽帧时，整段视频以软件解码重试。CLI 用 `--decode cpu` 强制软件解码，用 `--refresh` 跳过已有特征。
- 查重结果在左侧，点击缩略图或文件名使用右侧播放器，清理复选框独立操作。推荐保留优先比较分辨率和时长，质量指标相同时优先考虑视频创建时间，缺失时参考文件时间。清理需要人工确认，后端核对最近一次扫描的文件状态和直接匹配的保留项后才调用回收站。
- 「视频查重」在左侧工具栏，与「显示／隐藏预览」同一行。Web 基线扫描当前目录及子目录，显示结果和读取失败数量。大型目录、多根目录界面和持久化任务仍按阶段计划推进。无桌面 NAS 上通过「输入路径」设置挂载目录。
- 在 [视频查重源码调研与实施计划](docs/video-dedup.md) 查看固定提交的 VDF 源码定位、各阶段范围、验收方法和结果。后续每阶段更新此文档，写明实现、测试环境、复现命令、结果与待验证项，供各 Agent 和 PC 测试同步。
- 当前开发机是 Intel macOS。用户的 Windows 10 PC 配有双 RTX 2080 Ti，通过 SMB 读取飞牛 NAS。第一阶段合成测试在 macOS 已通过。Windows BAT、实际共享目录及真实素材准确率待在 PC 验收，再进入 pHash、旋转和镜像阶段。
- Web 扫描延续相对路径边界校验。旋转和元数据编辑在替换文件后保留 mtime，并失效查重缓存。扫描和相似度结果本身不触发任何视频写入或删除。验证使用合成文件或可丢弃测试副本。
