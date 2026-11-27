# 视频查重源码调研与实施计划

调研日期为 2026-11-27。已从 VDF 官方仓库克隆默认分支 `master`，下文源码链接固定到提交 `e2e163ee53f4250b7e91daf2d9149dda2ee97e06`。官方最新固定版本是 2026-09-25 发布的 `v4.1.1`（`21ec967`）。`4.1.x` 是滚动构建，发布页当时显示最近一次构建使用 `e2e163e`。该源码快照的核心项目目标为 `net11.0`。本文的实现细节以克隆到的快照为准。 [官方发布页](https://github.com/0x90d/videoduplicatefinder/releases) · [核心项目配置](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/VDF.Core.csproj#L4)

## 1. 环境和仓库现状

| 环境 | 检查结果 |
| --- | --- |
| 当前开发机 | Intel macOS 15.7.9。默认 `python3` 为 3.10.4，另有已安装的 Python 3.12.14，SQLite 3.53.1。本次使用后者。 |
| 本地工具 | PATH 中无 FFmpeg、FFprobe、.NET SDK 或 NVIDIA 工具。已在临时目录下载 FFmpeg／FFprobe 6.0 Intel 构建，供本项目的合成测试使用。未编译或运行 VDF。 |
| 用户的目标 PC | Windows 10、双 RTX 2080 Ti，读取本机目录及飞牛 NAS 的 SMB 共享。Windows 启动脚本、UNC 命令、驱动和 GPU 性能待在该 PC 验证。 |
| 本项目 | 已有 FastAPI、原生 HTML/CSS/JS、FFmpeg 路径配置、文件预览与内存任务队列。Web 当前使用一个视频根目录。查重 CLI 使用同一套格式列表与 FFmpeg 路径配置，可单独运行。 |

## 2. VDF 源码结构

| 模块 | 职责与源码入口 |
| --- | --- |
| 扫描调度 | `ScanEngine.cs` 串起枚举、媒体信息和特征采集、全片匹配、分组与可选片段匹配。`Settings.cs` 保存扫描范围与匹配选项。 [扫描入口](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L337) · [比较入口](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L399) · [配置](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/Settings.cs#L25) |
| 媒体工具 | `FFProbeEngine.cs` 运行 ffprobe 并读取 JSON。`FfmpegEngine.cs` 提供 CLI 与原生 FFmpeg 绑定。`FFmpegNative/` 负责流解码、缩放及显示方向。 [探测](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/FFTools/FFProbeEngine.cs#L35) · [抽帧](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/FFTools/FfmpegEngine.cs#L941) · [解码器](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/FFTools/FFmpegNative/VideoStreamDecoder.cs#L32) |
| 特征与记录 | `GrayBytesUtils.cs`、`pHash/`、`AI/` 分别维护像素比较、感知哈希和图像向量。`FileEntry.cs` 保存媒体元数据与已计算特征。`DatabaseUtils.cs` 负责持久化与迁移。 [记录](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/FileEntry.cs#L25) · [数据库](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/Utils/DatabaseUtils.cs#L26) |
| 片段与分组 | `Chromaprint/` 和 `ChromaprintEngine.cs` 负责音频指纹，`ScanEngine_AiPartial.cs` 负责密集视觉片段匹配，`DaisyChainSplitter.cs` 校验全片结果的组内相似性。 [音频片段](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L2045) · [视觉片段](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine_AiPartial.cs#L37) · [拆组](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/Utils/DaisyChainSplitter.cs#L187) |
| 入口与验证 | `VDF.GUI` 为桌面界面，`VDF.CLI` 提供扫描、比对和数据库命令，`VDF.Web` 提供浏览器复核与文件操作，仓库也提供 Docker 部署方式。另有 Core、各前端、集成测试和 benchmarks。 [官方仓库结构](https://github.com/0x90d/videoduplicatefinder/tree/e2e163ee53f4250b7e91daf2d9149dda2ee97e06) · [Web 和 Docker](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/README.md#L217) |

## 3. 核心算法核查

| 核查项 | 源码行为 |
| --- | --- |
| 1. 抽帧时间点与数量 | 取 `i / (N+1)`（`i=1…N`）的相对位置，避开首末。可配置的 `ThumbnailCount` 核心默认值为 1，即中点。启用最大采样时长时，用 `min(视频时长, 上限)` 乘相对位置，长视频只采开头区间。 [位置计算](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L512) · [默认值](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/Settings.cs#L73) · [时长上限](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/FileEntry.cs#L171) |
| 2. 16×16 和 32×32 | 当前 CLI 使用 bicubic 缩放至 32×32 并输出 `gray` 原始字节，每帧 1,024 字节。原生路径缩放后处理显示方向。16×16 是旧缓存格式，检测到 256 字节旧帧时清除对应灰度和 pHash 后重采样，无法取得有效数据的条目会被排除。 [CLI 滤镜](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/FFTools/FfmpegEngine.cs#L640) · [原生路径](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/FFTools/FfmpegEngine.cs#L345) · [缓存修复](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L1385) |
| 3. 灰度差异 | 逐像素绝对差总和除以像素数及 **256**。可启用黑／白像素遮罩，任一帧对应位置 `<=32` 或 `>=240` 时按选项排除。抽样帧至少 80% 像素暗时判为过暗，本轮缺失位置全部抽出暗帧可标记文件 `TooDark`。 [像素比较](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/Utils/GrayBytesUtils.cs#L38) · [全暗标记](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/FFTools/FfmpegEngine.cs#L1039) |
| 4. 多帧灰度相似度 | 在相同采样序号比较，将每帧差异等权平均，相似度为 `1 - 平均差异`。累计差异超出 `N × (1 - 阈值)` 时提前淘汰。经典核心默认阈值 96%。 [灰度比较](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L1635) · [默认阈值](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/Settings.cs#L73) |
| 5. pHash | 在 32×32 灰度帧上计算可分离 DCT，只求两个维度的频率索引 1–8，共 64 个系数。以这些系数的中位数为界，大于中位数的系数置相应位。pHash 单独模式与灰度／pHash 并行联合模式均存在。联合模式任一算法通过即可，若同时通过则展示较高分数。 [哈希计算](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/pHash/PerceptualHash.cs#L22) · [联合判定](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L1543) |
| 6. 汉明距离与多帧 pHash | `popcount(a XOR b)` 为距离，单帧相似度为 `1 - 距离/64`。严格模式允许 `floor(64 × (1 - 阈值))` 个不同位，96% 时至多 2 位。整片默认要求至少 `ceil(N × 0.6)` 帧单独过线，展示分为所有对应帧的平均相似度。 [汉明判定](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/pHash/PHashCompare.cs#L26) · [多帧投票](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L1588) |
| 7. 时长筛选 | 每个视频先按自身时长计算容差，默认 20%，可设秒数下限和上限，关闭百分比后可用固定秒数。对比时取两边容差较小值。至少 5,000 条视频时走 1 秒时长桶，只查覆盖容差的桶；时长密集时仍可能出现大量候选。 [容差](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/Settings.cs#L195) · [比较入口](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L1836) · [分桶](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L1969) |
| 8. 镜像与方向 | 可开一次左右镜像比较，将一方每行反序，pHash 从翻转灰度重新计算；正向和镜像都通过时保留较高分数。原生 FFmpeg 路径另读取显示矩阵以还原元数据描述的方向，其内部转置与翻转用于解码显示方向。经典匹配没有对未知方向的实际画面枚举 90°、180°、270° 或上下翻转。 [翻转特征](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L1296) · [镜像匹配](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L1809) · [显示矩阵](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/FFTools/FFmpegNative/FrameOrientation.cs#L48) |
| 9. DINOv2 | 可选的 DINOv2-small INT8 ONNX 模型按需下载并校验 SHA-256。模型输入 224×224 RGB，转 NCHW、按 ImageNet 参数归一化。输出优先取 `image_embeds` 或 `pooler_output`，否则取首个输出的 CLS token。结果经 L2 归一化后保存为 384 维 int8，近似余弦用 `clamp(dot(qA,qB)/127²,-1,1)`。全片比较在经典匹配未通过后尝试 AI，至少半数位置具有共同的有效向量，对这些位置取平均，AI 默认阈值 94%。 [模型](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/AI/AiComponents.cs#L40) · [输入与推理](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/AI/OnnxEmbedder.cs#L29) · [相似度](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/AI/EmbeddingMath.cs#L27) · [全片判定](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L1487) |
| 10. 缓存和增量 | `ScannedFiles.db` 为 `VDFDB002` 流式 MemoryPack 二进制格式，写临时文件后替换，兼容旧版 MemoryPack 和 protobuf。按路径复用 `FileEntry`，依据尺寸和时间戳判断变化；时间变化、大小相同或疑似移动时可用首尾各 64 KiB 的 OsHash 辅助复用。灰度和 pHash 按采样秒数缓存，AI 全片与密集片段有独立旁路缓存。扫描核心默认每 5 分钟保存检查点。 [数据库格式与写入](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/Utils/DatabaseUtils.cs#L29) · [增量与重定位](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L761) · [检查点](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L287) · [OsHash](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/Utils/OsHashUtils.cs#L21) · [AI 全片](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/AI/UnionEmbeddingStore.cs#L44) · [AI 片段](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/AI/DenseEmbeddingStore.cs#L48) |
| 11. 重复分组 | 命中成对后建立／合并 GroupId，新成员或跨组合并有代表文件复核。对至少 3 成员的组，`DaisyChainSplitter` 建立位压缩三角相似矩阵，迭代移除与不足半数其他成员相似的最弱成员，对移除成员重新聚类，丢弃单个成员。矩阵超预算可跳过。代表校验与拆组直接用正向判定，镜像关系在这一阶段可能失去证据；组员间相似也无须形成全连接。 [组内合并](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L1744) · [拆组调用](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L2566) · [拆分算法](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/Utils/DaisyChainSplitter.cs#L187) |
| 12. 多线程和硬件加速 | 媒体读取并发与比较并发独立，慢盘／网络共享可限流。灰度 SAD 有 AVX2／SSE2 快速路径。FFmpeg 子进程可传 `-hwaccel`，原生绑定也支持 CUDA 等硬解设备并有回退路径；这属于解码。DINOv2 推理由单消费队列批处理（上限 16），ONNX Session 使用 CPU 线程配置，源码里没有 CUDA 推理 provider 或双卡调度。 [并发](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L1216) · [硬解](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/FFTools/FfmpegEngine.cs#L150) · [媒体工具](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/FFTools/FfmpegEngine.cs#L697) · [SIMD](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/Utils/GrayBytesUtils.cs#L156) · [批处理](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/AI/EmbeddingPipeline.cs#L81) · [ONNX Session](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/AI/OnnxEmbedder.cs#L29) |

### 已有能力与局限

- VDF 支持跨容器、编码和分辨率的内容比较、递归目录、缩略图、阈值调整、缓存复用与复核界面。Web 结果可导出 CSV、移动文件、移入回收站或经确认永久删除。经典重复检测仍依赖对齐的稀疏采样，遇到裁剪画面、水印、偏移、黑场或重复片头时可能误判。DINOv2 提供额外信号，阈值仍需按实际素材校准。 [Web 结果](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Web/Components/Pages/Results.razor#L63) · [经典与 AI 判定](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L1530)
- 短片功能已经存在。音频模式用 Chromaprint 风格指纹做滑窗汉明匹配，可用匹配偏移处的灰度／pHash 再验证，短片长度默认至少为源片的 10%。视觉模式适用于静音及换配音视频，按文件时长选择约 5–15 秒的采样间隔（最长片可能进一步放宽以满足 400 帧上限），先用向量符号位汉明预筛，再做余弦比较，要求至少 4 个命中落在同一中位偏移的 ±30 秒内。共享配乐、重复静态画面、极短片段或大量候选都需实测。 [音频片段](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L2045) · [滑窗](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L2414) · [视觉密采样](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine_AiPartial.cs#L28) · [偏移验证](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine_AiPartial.cs#L292)
- OsHash 是缓存复用的弱指纹，原始字节中段的改动可能躲过首尾校验。源码核查范围内没有为重复结果提供独立的全文件 SHA-256 查重模式。经典比较的时长桶、片段比较的时长比和符号位预筛都能减少工作量，但相似时长密集的库依然存在二次复杂度的路径。 [OsHash](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/Utils/OsHashUtils.cs#L21) · [片段候选](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/ScanEngine.cs#L2112) · [符号位预筛](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/VDF.Core/AI/EmbeddingMath.cs#L86)
- VDF 仓库使用 GNU AGPL v3，源码头允许 v3 或更新版本；其说明列出 AcoustID.NET 相关实现源自 LGPL 2.1 项目，ONNX Runtime 为 MIT，DINOv2 模型为 Apache 2.0。当前 Python CLI 按项目需求实现通用算法，没有移植 VDF 源码、测试、模型或缓存格式。若以后直接采用上游实现，需先审查相应组件许可、发布条件以及修改版通过网络交互时 AGPL 第 13 节的对应源代码提供义务。 [上游许可](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/LICENSE) · [上游第三方说明](https://github.com/0x90d/videoduplicatefinder/blob/e2e163ee53f4250b7e91daf2d9149dda2ee97e06/README.md#L361) · [GNU AGPL 正文](https://www.gnu.org/licenses/agpl-3.0.en.html#section13)

## 4. 本项目架构

1. **文件发现与精确匹配**。输入多个明确选择的目录，递归枚举既有扩展名列表。路径去除重叠目录造成的重复条目。先按字节数分桶，对同大小文件计算全文件 SHA-256。
2. **媒体与视觉特征**。FFprobe 选第一条实际视频流，优先使用流时长，取不到时回退到容器时长。FFmpeg 软件解码，对每个采样点提取 32×32 灰度。以同一采样序号做归一化平均绝对误差。后续加入可选的方向变换、pHash、音频和向量提取。 [FFmpeg 文档](https://ffmpeg.org/ffmpeg.html)
3. **缓存与扫描任务**。SQLite 位于运行机本地。按文件路径、大小、纳秒 mtime、ctime 与特征版本复用；完成单项特征后提交。后续保存扫描任务自身的阶段与状态，区分已缺失文件和共享暂时离线，恢复时重试未完成项。旋转替换会主动恢复 mtime，Web 集成时要在旋转成功后显式失效查重缓存，也要识别扫描与文件替换的并发。
4. **候选匹配与证据**。第一阶段按时长排序并在有界范围内逐对精算。加入 pHash 分桶后，在有标注的小库上用穷举结果测量候选召回率。保存成对证据（算法、分数、有效帧数量、方向、片段偏移），分组时校验与代表文件的直接关系。质量推荐可综合时长、分辨率、编码和码率，选择权留给用户。
5. **Web 集成**。现有 FastAPI 加独立查重路由，前端沿用原生 JS。服务端登记多根目录，文件操作参数用根目录 ID 与相对路径，扩展现有的路径边界校验。扫描与日志进度通过专用调度器，避免长期占用编辑队列。默认只读，隔离移动和回收站操作均需人工确认并重新校验文件身份，跨卷移动核验结果后再处理源文件。后续 Docker 部署先以只读媒体挂载和独立 SQLite 卷验证，远程 Web 使用前需要认证。

### 推荐目录布局

```text
app/
  config.py               FFmpeg 路径、视频扩展名及现有应用配置
  dedup/
    cli.py                第一阶段扫描入口、日志及 JSON Lines
    media.py              视频流探测与 CPU 灰度抽帧
    matching.py           时长候选与灰度评分
    cache.py              文件状态与 SQLite 特征缓存
    phash.py              第二阶段哈希、方向特征与候选索引
    scanner.py            第三阶段提取并发、任务状态和续扫
    routes.py             第四阶段查重 API
    audio.py              第五阶段可选音频匹配
    embeddings.py         第五阶段可选 AI 特征与设备调度
dedup.bat                 Windows 启动入口
tests/test_dedup.py       CPU 查重测试及隔离的合成视频测试
docs/video-dedup.md       源码核查、计划及每阶段验收记录
```

## 5. 分阶段交付与验收

| 阶段 | 交付 | 验收 |
| --- | --- | --- |
| 1. CPU 基线 | 只读 CLI、全文件 SHA-256、均匀取样、灰度相似度、SQLite、Windows BAT、测试 | 合成的相同文件、重编码正例和近时长负例分类正确。第二次扫描命中缓存，修改后重算。PC 完成本地和 SMB 小目录验证。 |
| 2. 方向与 pHash | 多帧 pHash、90°／180°／270°、左右／上下镜像、元数据方向、可复现标注集 | 覆盖物理旋转、仅改元数据、镜像、水印和轻裁剪样本。记录各类型 precision、recall，量化候选漏检并确定默认阈值。 |
| 3. 大库与 NAS | 本地 SQLite 持久任务、可取消和恢复、SQLite 分批写入、可复用扫描配置文件、NAS 分共享限流、NumPy 批量距离、候选索引与性能统计 | 用标注子集与基线对照。报告冷／热扫描耗时、吞吐、CPU、峰值 RAM、SMB 读量、候选压缩率和召回率。在 1k／10k／100k 规模测量，并验证断网及进程退出后重试。 |
| 4. Web 管理 | 持久任务进度和日志、可复核分组、相似度与缩略图对照、视频详情、保留推荐、批量选择、手动隔离与回收站 | 用测试目录复核分组和回放，验证边界校验、文件变化、NAS 回收站失败、跨卷中断与恢复；禁止自动删除。 |
| 5. 高级匹配 | 可选 NVDEC、音频指纹、DINOv2、双 GPU 分设备队列、时间偏移与片段匹配，评估 FAISS | 对照纯 CPU、单卡、双卡的吞吐和质量，记录设备利用率和失败回退。用静音、换配音、裁剪、水印和片段样本验收。 |

## 6. 第一阶段快速运行

Windows CMD 进入仓库目录，确保 `python` 指向 Python 3.12+。查重 CLI 只用 Python 标准库。视觉模式还需 FFmpeg 和 FFprobe，已有 `FFMPEG_PATH`／`FFPROBE_PATH` 环境变量可覆盖 PATH。本地验证使用了 FFmpeg 6.0。

```bat
python --version
ffmpeg -version
ffprobe -version
dedup.bat "D:\dedup-test" "\\fnos\media\dedup-test" --samples 5 --threshold 0.96 > "%TEMP%\dedup-results.jsonl"
dedup.bat "D:\dedup-test" --exact-only
python -m unittest discover -s tests -v
```

把示例目录换成实际的测试目录。默认缓存为 Windows `%LOCALAPPDATA%\.video-nas-tools\dedup.sqlite3` 或其他平台 `~/.video-nas-tools/dedup.sqlite3`，也可用 `--cache` 指定本地 SQLite 路径。用 `--refresh` 在本次扫描重新计算，适用于更换 FFmpeg 构建或外部工具改动了文件、但文件大小和可见时间戳都保留的情况。

CLI 输出 `exact` 组、`visual` 成对证据与 `summary` 计数，日志去 stderr。默认 5 帧，时长容差为 `max(0.5 秒, 较短片时长 × 2%)`，至少需要 3 个双方均不过暗的位置。灰度差用 255 归一化，相似度默认 0.96，这一分数是待实测的初始阈值。更改阈值可复用原特征。本阶段每帧起一个 FFmpeg 进程并在内存中保留当次扫描的摘要与灰度特征，时长密集时仍会出现大量候选。扫描错误会记录并返回退出码 1，已产出的结果仍可读取。Ctrl+C 后重新运行会重新枚举和比较，已提交的特征可以复用。

### 第一阶段测试记录与 PC 验收

本地使用 macOS、Python 3.12.14、FFmpeg／FFprobe 6.0。执行 `python -m unittest discover -s tests -v`，4 项全部通过。覆盖采样位置、边界阈值与暗帧、时长候选、重叠目录、SHA-256 查重与失效、跨容器／编解码／分辨率查重、近时长负样本、损坏文件、缓存复用，以及扫描前后逐字节核对测试素材。

| PC 测试 | 操作及预期 |
| --- | --- |
| 本地与共享 | 将一组可丢弃样片放到两个测试目录，含逐字节副本、调整分辨率并转码的副本、近似时长的无关视频和损坏样本。分别运行本地、UNC 和合并目录，核对 `exact`、`visual`、`summary` 及中文／空格文件名。可补测 MOV、WMV 和映射盘。 |
| 缓存与中断 | 同命令运行两次，第二次 `exact_cache_hits`、`visual_cache_hits` 应增加，命中关系一致。中途 Ctrl+C 后重跑，验证已完成文件的缓存可复用；将一份副本改为不同内容后重跑，确认只重算变更项。 |
| 准确率 | 先对标注素材分别用 3、5、9 帧跑一遍，再对同批特征试 0.92、0.94、0.96、0.98。记录误报与漏报的文件对、时长和变换方式。分开统计多次转码、黑场、相似片头、画面裁剪与片段截取，作为第二阶段基线。 |
| 回归 | 启动现有 Web 工具，浏览测试根目录并检查播放和文件操作入口。扫描前后可用 PowerShell `Get-FileHash -Algorithm SHA256` 对比测试文件哈希。 |

Windows BAT、SMB 断连行为、真实视频库准确率及双 GPU 性能均待目标 PC 验证。

### Web 基线接入

文件列表工具栏增加「视频查重」。面板扫描当前目录及其子目录，允许调整阈值或只跑字节查重，最多展示 200 条匹配。浏览器请求期间显示扫描状态，结果复核与持久化任务按后续计划推进。服务中的旋转替换完成后会清理该文件的查重特征。

macOS 桌面使用原生目录选择器。飞牛 NAS 等 Linux 系统使用服务端路径，容器部署时使用容器内的挂载路径。已在本机 Chrome 验证查重入口及扫描反馈，并用合成视频验证 Web 后端返回 1 组精确重复和 2 对内容相似结果。Linux／NAS 分支通过模拟平台验证，实际 NAS 部署留待目标设备验收。
