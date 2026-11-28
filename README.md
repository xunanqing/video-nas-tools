# video-nas-tools

批量管理 NAS / 本地目录视频文件的 Web 工具，交互体验参考 LosslessCut。后端基于 FastAPI，视频处理使用 ffmpeg，前端为原生 HTML / CSS / JavaScript，无需任何构建步骤。

## 功能特性

- **目录浏览**：列表与缩略图两种视图、按名称/时间/大小排序、框选与多选，支持 70:30 可拖拽分栏。
- **视频预览**：点击文件即勾选并预览，支持 HTTP Range 流式播放，可按需折叠预览区。
- **视频截取**：无损模式（`stream copy`，速度快）与精确模式（重新编码，起止时间更准确）。
- **视频旋转**：支持 90° / 180° / 270°；快速模式写 `display_rotation` 元数据，实际模式用 `transpose` 重编码。旋转直接替换原文件，文件名与修改时间保持不变，多次旋转可累计。
- **视频拼接**：按拖拽顺序把多个视频拼成一个；支持无损模式（要求编码参数一致）和兼容模式（统一画布、帧率、音轨后重编码）。**原视频全部保留**，输出名自动取第一个视频名加 `_拼接`。
- **智能检测**：分析视频中的黑帧、静音区间和场景切换点，结果可点击定位到对应时间点；检测走任务队列并缓存结果。
- **元数据与标签**：查看完整的容器/流/章节信息，编辑标题、备注、作者、语言等元数据（stream copy，不重编码）；标签存于本地标签库，与视频文件解耦，支持手动与自动来源。
- **批量删除**：移入系统回收站，不是永久删除，可恢复。
- **批量重命名**：按「基础名_序号」批量命名，基础名可自定义或默认取首个文件名。
- **任务队列**：默认 2 个处理任务并发，可在「配置」中调整，实时显示进度。
- **其他**：路径记忆、缩略图缓存、文件夹选择弹框置顶显示。

## 快速开始

环境要求：

- Python 3.12+
- 已安装 `ffmpeg` 与 `ffprobe`
- Windows 或 macOS 桌面可使用「浏览」按钮打开系统目录选择框。Linux／NAS 服务使用「输入路径」。

安装与启动：

```bash
pip install -r requirements.txt
python run.py
```

浏览器打开 <http://127.0.0.1:8899> 即可使用。

在顶栏「配置」中填写同时包含 FFmpeg 和 FFprobe 的目录，例如 Windows 下的 `C:\tools\ffmpeg\bin`。macOS 和 Windows 可在服务端桌面用「浏览」选择目录，NAS／Docker 使用服务端或容器内的绝对路径。保存后，后续执行的命令会使用该目录。

目录留空时，程序先读取 `FFMPEG_PATH` 和 `FFPROBE_PATH`，再通过 PATH 查找。Windows 命令提示符示例：

```bash
set FFMPEG_PATH=C:\tools\ffmpeg\bin\ffmpeg.exe
set FFPROBE_PATH=C:\tools\ffmpeg\bin\ffprobe.exe
```

顶栏「配置」还可以保存查重默认值（96%、自动解码、内容相似查重）、排除目录、预览列表的扩展名以及以下选项：

- **查重排除目录**：每行一个目录名（如 `#recycle`）或服务端绝对路径，匹配目录及其子树都会跳过。
- **并发上限**：查重默认同时抽帧 1 个视频，处理队列默认同时运行 2 个任务。修改后台并发上限后，正在运行的任务继续执行。
- **输出目录**：适用于截取和拼接。留空时输出到源视频目录。输出位于视频根目录外时，可从任务队列在新标签页预览。
- **缓存目录**：在所选目录下保存 `thumbnails/` 和 `dedup.sqlite3`，旧缓存保留在原位置。留空时使用原有默认位置。目录路径以服务端实际看到的文件系统为准。

## 使用指南

1. **设置根目录**：在顶部输入视频目录，或在 Windows／macOS 本机服务上点「浏览」用系统对话框选择。选择后自动保存，手动输入后点「保存」。
2. **选择文件**：点击列表项或缩略图即勾选并预览；按住 Shift / Ctrl 可追加选择，也可在列表区域拖拽框选。左侧工具栏提供视图切换、排序、全选和取消勾选。
3. **功能操作**：勾选文件后，右侧功能区可进行截取、旋转、删除、重命名、拼接、检测和元数据编辑。
4. **拼接排序**：在「拼接」标签页中直接拖拽行调整顺序，左侧序号即最终拼接顺序。
5. **查看任务**：右下角任务图标打开队列，可查看进度、错误信息和清除已完成任务。
6. **智能检测**：在「检测」标签页勾选黑帧/静音/场景切换并设置阈值，执行后点击结果行即可跳转到对应时间点。
7. **元数据与标签**：在「元数据」标签页编辑写入视频的元数据；标签添加后保存在本地，即使更换容器格式也不会丢失，便于后续接入智能标签。

## 技术架构

| 层次 | 技术 |
| --- | --- |
| Web 框架 | FastAPI + uvicorn，单端口本机服务 |
| 视频处理 | ffmpeg / ffprobe，支持 stream copy 与重编码两种策略 |
| 任务调度 | `asyncio` 任务队列，默认 2 并发，同一文件的旋转操作加锁互斥 |
| 前端 | 原生 HTML / CSS / JavaScript，单页应用，无构建依赖 |
| 配置与缓存 | `settings.json` 保存根目录和服务端偏好；`.thumbnails/` 是默认缩略图缓存目录；`.workbuddy/tags.json` 与 `detections.json` 保存标签与检测结果 |
| 智能检测 | ffmpeg `blackdetect` / `silencedetect` / `select+showinfo` 分析流 + 解析 stderr |
| 标签系统 | 独立 JSON 标签库，支持 `manual` / `auto` / `detected` / `imported` 来源与置信度 |

主要源码：

- `app/main.py` — API 路由与业务逻辑
- `app/ffmpeg_service.py` — ffmpeg 参数构建、执行与检测分析
- `app/config.py` — 配置与路径安全
- `app/tag_store.py` — 本地标签库（智能标签扩展点）
- `app/task_queue.py` — 异步任务队列
- `static/` — 前端页面、样式与交互

## 视频查重

在左侧「显示／隐藏预览」同一行点击「视频查重」，可对当前目录及子目录执行只读扫描。结果显示在左侧，点击缩略图或文件名可在右侧播放器预览，复选框用于选择清理项。结果展示视频规格和时间来源。保留建议优先考虑分辨率和时长，质量指标相同时优先保留时间较早的文件。时间优先取视频的创建元数据，缺失时参考文件的创建和修改时间。默认勾选多余项，可调整后确认批量移入回收站。每个待清理文件都必须有至少一个直接匹配的保留项。

独立 CLI 支持扫描多个目录、输出 JSON Lines，共用 Web 保存的查重默认值、排除目录和缓存目录。命令行的 `--threshold`、`--decode`、`--exact-only`／`--no-exact-only`、`--workers`、`--cache` 可覆盖对应设置。缓存目录留空时，可用 `VIDEO_NAS_DEDUP_CACHE` 指定本地数据库文件。

```bat
dedup.bat "D:\dedup-test" "\\fnos\media\dedup-test" --threshold 0.96 > "%TEMP%\dedup-results.jsonl"
python -m unittest discover -s tests -v
```

将示例目录换成实际的测试目录。其他平台用 `python -m app.dedup.cli` 代替 `dedup.bat`。视觉模式需要 FFmpeg 与 FFprobe，`--exact-only` 可只运行字节查重。源码分析、缓存参数和 Windows／SMB 验收见[视频查重源码调研与实施计划](docs/video-dedup.md)。

抽帧默认由 FFmpeg 优先使用运行机可用的硬件解码。设备、驱动或视频不支持时会回退到 CPU。可在 Web 的「解码」选择「仅 CPU」，或给 CLI 加 `--decode cpu`。灰度缩放、相似度计算和 SHA-256 仍使用 CPU。已有特征会复用，用 CLI 的 `--refresh` 可强制重新抽帧。

## 飞牛 NAS（fnOS）Docker 部署

### 准备目录

把项目源码放到 NAS 上的一个独立目录，并准备视频目录和应用数据目录。数据目录放在 NAS 本地存储上，与视频目录分开。用 `id` 查看运行用户的 UID 和 GID，确保这个用户能读取视频、进入视频目录，并能写入数据目录。

```bash
cp .env.example .env
```

根据 NAS 的实际配置编辑 `.env`。

| 配置 | 填写方式 |
| --- | --- |
| `NAS_MEDIA_DIR` | 视频目录在 NAS 上的绝对路径，容器内对应 `/media/videos` |
| `NAS_DATA_DIR` | 已存在的应用数据目录绝对路径，容器内对应 `/data` |
| `NAS_UID` / `NAS_GID` | 拥有以上权限的用户和用户组的数字 ID |
| `NAS_BIND_IP` | 从其他电脑访问时填 NAS 的局域网 IP |
| `NAS_PORT` | NAS 对外的应用端口，默认 `8899` |
| `NAS_MEDIA_READ_ONLY` | 初次使用保留 `true`。要旋转、重命名、修改元数据或清理文件时改成 `false` |

应用没有登录认证。只在可信局域网内开放端口，避免设置公网转发。

### 构建和启动

在飞牛安装并启用 Docker，SSH 登录 NAS 后进入项目根目录，运行以下命令。飞牛 Docker 界面可查看生成的项目、容器状态和日志。

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=50
```

容器显示 `healthy` 后，在同一局域网的电脑访问 `http://NAS局域网IP:8899`。项目默认打开 `/media/videos`。如需切换浏览目录，在「输入路径」中填写容器路径，例如 `/media/videos/测试`。

FFmpeg 和 FFprobe 已随镜像安装，顶栏「配置」中的 FFmpeg 目录留空即可。当前 Compose 适用于 CPU 模式，自动解码会在没有可用硬件设备时使用软件解码。

只读挂载下也可以截取和拼接。先在 NAS 的应用数据目录下创建 `outputs`，并在「配置」里将输出目录设为 `/data/outputs`。生成文件可从 NAS 的应用数据目录中查看。启用可写媒体挂载后，先用可丢弃的副本确认权限和各操作。Linux 容器的回收站取决于文件系统和挂载权限，应用会报告失败。用测试副本验证恢复位置后再清理实际素材。

### 更新和持久化

更新源码后，在同一项目目录再次运行 `docker compose up -d --build`。若只修改了 `.env`，运行 `docker compose up -d`。

重建容器时，`/data` 中的 `settings.json`、缩略图、标签、检测结果和 SQLite 查重缓存会保留。任务队列和最近一次查重的清理凭证保存在内存里，重建后要重新扫描才能清理。浏览器的视图筛选配置仍由浏览器保存。

如果 NAS 不方便拉取基础镜像或依赖，可以在另一台支持 Docker 的电脑构建并导入。先用 `uname -m` 确认 NAS 的架构，下面以 `x86_64` 为例：

```bash
# 构建机，在源码根目录运行
docker build --platform linux/amd64 -t video-nas-tools:local .
docker save -o video-nas-tools.tar video-nas-tools:local

# 将镜像文件、compose.yaml 和编辑好的 .env 传到 NAS 后运行
docker load -i video-nas-tools.tar
docker compose up -d --no-build --pull never
```

### 部署验收

使用一个单独的测试目录，放入两个相同的小视频。检查文件列表、缩略图、播放和完全相同文件查重。保存一个设置后用 `docker compose up -d --force-recreate` 重建容器，检查设置与查重缓存仍在。只读模式下保留两个原测试文件。

2026-11-28 在 macOS 使用 Compose 2.39.4 校验了只读和可写配置，以及带中文和空格的宿主路径。隔离的本机服务通过了合成视频的列表、缩略图、Range 播放和查重验证，重启后仍能读取设置、标签和 SQLite 缓存。实际镜像构建、fnOS 启动和回收站行为待在有 Docker 引擎的环境验收。

### Mac 访问 NAS 共享目录

若程序运行在 Mac 且已通过 Finder 挂载 SMB 共享，可在「浏览」对话框选择相应卷，也可手填 `/Volumes/共享名`。

## 注意事项

- 本机启动绑定 `127.0.0.1`。Docker 可绑定 NAS 的局域网 IP。应用没有用户认证，**请勿暴露到公网**。
- 删除操作使用系统回收站。容器内的回收站受挂载方式和文件权限影响，失败时应用会报告错误。
- 旋转会替换原文件（文件名和修改时间保持不变），建议对重要素材提前备份。
- 拼接生成新文件，不会修改或删除原视频。
- 任务队列保存在内存中，服务重启后任务记录会清空。
- 拼接的无损模式要求各片段编码、分辨率、帧率和音轨一致，不一致时会提示改用兼容模式。

## 协作

后续参与项目的开发者与 AI Agent 请先阅读 [`agents.md`](agents.md)，其中包含架构说明、API 清单、关键约束与踩坑记录。迭代历史见 `.workbuddy/memory/`。
