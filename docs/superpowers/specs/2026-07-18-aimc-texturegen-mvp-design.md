# AIMCTextureGen MVP 设计规格

- 日期：2026-07-18
- 状态：已通过交互式头脑风暴确认
- 首个版本目标：Windows 本地运行的 Java 版 Minecraft 普通方块单张 AI 材质生成闭环

## 1. 产品定位

AIMCTextureGen 是一个本地 WebUI，用于管理和配置 Minecraft 资源包材质生成。它不实现扩散模型，也不做 Java 版与基岩版之间或不同游戏版本之间的资源包转换。它负责把本地图片模型、Minecraft 资源包结构和确定性图片后处理组合成普通用户可以操作的流程。

首个 MVP 从用户主动导入的既有 Java 版资源包开始。应用读取 `pack.mcmeta`，选择对应的内置材质目录配置，按标准相对路径判断普通方块材质的覆盖状态。用户从未覆盖项中选择一个目标，选择包内 1–8 张已有贴图作为风格参考，可选上传一张结构参考图，生成四个候选并在后处理后采用一个结果。采用结果只写入项目工作副本，最终导出为新的 ZIP；原始资源包保持不变。

长期核心是沿用同一项目、目录和任务模型扩展到 Java 批量生成：用户从未覆盖清单中手动勾选一批材质，共享一组风格参考，并让应用按正确名称和路径逐项生成。

## 2. MVP 目标与非目标

### 2.1 目标

MVP 必须完成以下闭环：

1. 在 Windows 上通过启动脚本建立隔离环境，启动应用管理的 ComfyUI 和本地 WebUI。
2. 只读导入一个现有 Java 版资源包 ZIP 或目录，并建立完整项目工作副本。
3. 解析 `pack.mcmeta` 中的主 `pack_format`，保留兼容格式信息，并选择受支持的内置目录配置。
4. 根据内置路径元数据将普通方块标记为已覆盖、未覆盖或无法识别。
5. 从未覆盖的普通、单贴图、非透明、非动画方块中选择一个目标。
6. 从导入包中手动选择 1–8 张风格参考图，通过 IP-Adapter 直接作为图像条件。
7. 允许用户可选上传一张结构参考图；提供时使用 img2img，否则使用 text2img。
8. 支持 16×16、32×32、64×64 三种最终逻辑分辨率。
9. 每次固定生成四个候选；用户选择逐张、两张并行或四张并行。
10. 对每个候选执行网格吸附并生成放大预览、3×3 平铺预览和质量报告。
11. 只有用户点击“采用”时才将最终 PNG 写入工作副本。
12. 校验并导出新的 Java 版资源包 ZIP。
13. 重启应用后恢复项目、任务、候选和已采用结果。

### 2.2 非目标

MVP 不实现：

- Java/基岩版转换或任意跨版本转换；
- 基岩版资源包；
- 物品、实体、模型、PBR、CTM 或自定义命名空间资产；
- 透明、发光、动画或多面关联方块；
- 批量生成；
- 从 Minecraft 客户端 JAR 提取原版贴图；
- 扫描用户硬盘定位游戏安装；
- 内置或再分发 Mojang/Microsoft 原版 PNG、模型或完整 JSON 资产；
- 自动分析风格、自动推荐参考图或自动训练 LoRA；
- ControlNet、SDXL Refiner、FaceID；
- 桌面壳或安装包。

## 3. 平台与运行边界

### 3.1 支持平台

- 操作系统：Windows。
- GPU：NVIDIA CUDA。
- 目标最低显存：8 GB。
- CPU-only、AMD、Intel GPU 和 macOS/MPS 不属于 MVP 支持范围。

“8 GB”表示项目必须提供可在 8 GB 显卡上进行人工冒烟测试的逐张生成配置，而不是承诺所有并行模式都能在所有 8 GB 设备上成功。应用不根据显卡型号替用户选择并行度。

### 3.2 启动体验

首版通过 PowerShell 启动脚本打开浏览器，不提供桌面壳。启动器负责：

- 检查项目专用 Python 环境；
- 检查 NVIDIA 驱动、CUDA 可用性和磁盘空间；
- 安装或校验固定版本的 ComfyUI 与所需节点；
- 显示模型名称、来源、许可、大小、版本或 commit、SHA-256 和安装位置；
- 仅在用户确认后下载缺失模型；
- 启动 ComfyUI API；
- 等待健康检查通过后启动 FastAPI 和前端，并打开浏览器。

项目不修改全局 Python、Miniconda 或用户既有 ComfyUI。所有运行时依赖保存在项目管理的独立目录中。

Phase 4 已确定使用经过版本和 SHA-256 校验的官方 Windows NVIDIA
portable 包。其内置 Python/PyTorch 与后端 `.venv` 完全分离；应用不扫描或复用
用户已有 ComfyUI。运行时安装方式、候选锁定值和版本化模型配置边界见
[`ADR-0002`](../../adr/0002-managed-comfyui-runtime-and-versioned-model-profiles.md)。

## 4. 总体架构

核心采用独立 WebUI、Python 服务和托管 ComfyUI 的三层结构：

```text
React + TypeScript + Vite WebUI
               |
               v
            FastAPI
   +-----------+------------+----------------+
   |           |            |                |
Project   JavaPackAdapter  TextureProcessor  GenerationService
Workspace      |                              |
               v                              v
        Catalog Registry                Managed ComfyUI
```

### 4.1 WebUI

WebUI 只通过 FastAPI 访问项目文件和 ComfyUI，不直接读取本地路径或调用 ComfyUI。主界面采用已确认的引导式五步流程，并提供折叠的高级设置。

### 4.2 FastAPI 应用核心

FastAPI 是产品业务边界，负责：

- 项目生命周期和工作副本；
- 资源包解析和目录匹配；
- 生成任务状态机；
- ComfyUI 请求、进度和取消；
- 后处理和质量报告；
- 候选采用；
- ZIP 导出与校验；
- 易懂错误、建议操作和原始技术详情。

### 4.3 托管 ComfyUI

ComfyUI 是独立受控子进程，只负责 GPU 推理。项目固定兼容 commit、节点版本、模型版本和 workflow JSON，不允许首次启动时静默更新。升级必须是用户主动执行的显式操作。

### 4.4 清晰的模块边界

- `JavaPackAdapter`：解析、扫描、覆盖匹配和 Java 版路径写入，不依赖 AI。
- `CatalogRegistry`：按资源格式提供路径元数据，不读取或包含原版图像。
- `GenerationService`：将产品参数转换成固定 ComfyUI workflow 请求。
- `TextureProcessor`：纯确定性图像处理，不依赖 ComfyUI。
- `ProjectWorkspace`：项目清单、任务产物、原始快照、工作副本和导出。
- `ComfyUIManager`：安装、健康检查、启动、停止、版本和日志。

## 5. 资源包识别与材质目录

### 5.1 `pack.mcmeta` 语义

应用使用 `pack.pack_format` 选择主目录配置。若存在 `supported_formats`，则保存并展示其兼容范围，但不以范围内最高版本替代主 `pack_format`。主格式缺失、类型错误或不受支持时拒绝导入，并给出受支持格式列表。

应用识别的是“资源格式配置”，不声称仅凭 `pack.mcmeta` 推断唯一 Minecraft 补丁版本。

### 5.2 内置元数据

仓库内置独立维护的最小目录元数据，不包含 Mojang/Microsoft PNG、模型 JSON 或完整原版资源文件。普通方块条目至少包含：

```json
{
  "semantic_id": "minecraft:stone",
  "display_name": "Stone",
  "category": "block",
  "texture_role": "all",
  "relative_path": "assets/minecraft/textures/block/stone.png",
  "resource_formats": [34, 35],
  "prompt_terms": ["stone block texture", "uniform natural stone"],
  "mvp_eligible": true
}
```

`semantic_id` 是产品内部稳定标识；`relative_path` 才是工作副本中的实际写入位置。未来多面方块使用多个带角色的条目，而不是把 `top/side/bottom` 塞进单一字符串约定。

### 5.3 覆盖匹配

- 路径分隔符统一为 `/` 后按精确标准相对路径匹配。
- Java 资源路径必须使用规范小写；非规范大小写文件显示为警告或未知，不静默视为标准覆盖。
- 标准路径存在有效 PNG 时为“已覆盖”。
- 标准路径不存在时为“未覆盖”。
- 不在当前目录配置中的文件为“未知/自定义”，应用保留但不将其列为生成目标或删除；其中可正常解码的方形 PNG 仍可由用户选作风格参考。
- 重复、冲突或不安全路径使导入失败或产生明确阻断错误。

## 6. 项目与持久化

每个项目是可独立备份和恢复的本地目录：

```text
projects/<project-id>/
├─ project.json
├─ source/
│  └─ imported-pack.zip
├─ pack/
│  ├─ pack.mcmeta
│  └─ assets/...
├─ uploads/
│  └─ structure-references/
├─ jobs/
│  └─ <job-id>/
│     ├─ request.json
│     ├─ raw/
│     ├─ processed/
│     ├─ previews/
│     └─ report.json
└─ exports/
   └─ <pack-name>-<timestamp>.zip
```

- `source/` 是导入时的只读快照。目录导入会在本地创建等价 ZIP 快照。
- `pack/` 是唯一允许被“采用候选”修改的工作副本。
- `jobs/` 保存完整可复现参数、工作流和候选产物。
- `exports/` 只由用户主动导出创建。
- 风格参考通过工作副本内相对路径引用；上传的结构参考复制到 `uploads/`。

`project.json` 记录 schema 版本、项目名、Java 资源格式、目录配置版本、导入快照哈希、默认分辨率、默认并行方式、当前风格参考集合及时间戳。

应用使用一个轻量 SQLite 数据库索引项目和任务以支持 UI 查询，但项目目录中的 JSON 和图片是可迁移的数据源。SQLite 索引可以从项目目录重建。

运行期间的项目内部写入边界采用
[`ADR-0001`](../../adr/0001-running-project-mutation-boundary.md)：单个应用进程拥有其项目根，应用写入必须经过服务/仓库边界并使用验证后的同目录原子替换；手工或外部进程在应用运行时强制修改项目内部文件不受支持。应用会报告可观察到的冲突或后续损坏，但不使用已弃用的 TxF，也不承诺抵御最终系统调用窗口中的敌对外部 POSIX rename。

## 7. 生成工作流

### 7.1 输入条件

每个任务包含：

- 一个目录条目目标；
- 16、32 或 64 的最终逻辑分辨率；
- 1–8 张工作副本内的风格参考；
- 用户补充提示词；
- 可选结构参考；
- 固定模型配置；
- 四个确定并持久化的 seed；
- 并行方式 1、2 或 4；
- 可折叠高级参数。

基础提示词由目标 `display_name`、`prompt_terms`、像素贴片约束和用户补充描述组合。MVP 不调用额外视觉模型自动把参考图转换成文字。

### 7.2 风格与结构

- 风格参考通过 IP-Adapter 直接提供图像条件。
- 多张参考图 embeddings 默认使用 `average` 合并，以降低低配置 GPU 的额外压力。
- IP-Adapter 使用 SDXL style-transfer 权重类型，减少参考图内容布局向目标泄漏。
- 没有结构参考时运行 text2img。
- 有结构参考时运行 img2img；高级设置允许调整 denoise，但默认值由固定 workflow profile 提供。

### 7.3 四候选并行语义

候选总数固定为四。用户选择：

- 逐张：`batch_size=1`，执行四轮；
- 两张：`batch_size=2`，执行两轮；
- 四张：`batch_size=4`，执行一轮。

应用不根据设备自动改变选择，也不在 OOM 后自动降低并行度。界面展示与固定 workflow 版本对应的实测参考值，并明确实际显存会受驱动、后台程序和节点影响。

### 7.4 模型配置

默认配置：

- SDXL Base 1.0，CreativeML Open RAIL++-M；Base 独立使用，不安装 Refiner：<https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0>
- `kokuren/mapchipLora`，Apache-2.0：<https://huggingface.co/kokuren/mapchipLora>
- IP-Adapter SDXL ViT-H 权重，Apache-2.0：<https://huggingface.co/h94/IP-Adapter>
- ComfyUI IP-Adapter 节点以固定版本作为独立托管组件：<https://github.com/cubiq/ComfyUI_IPAdapter_plus>

默认模型只是第一个 `ModelProfile`，不是写死在业务逻辑中的唯一实现。运行时清单与模型配置清单相互独立；模型专属节点 ID、参数和 workflow 映射只能存在于对应配置的绑定层。新任务必须持久化配置 ID、版本、清单摘要及 workflow 摘要，旧任务不能被静默解释为新默认配置。未来 FLUX.2 Klein 4B 等实现通过新增配置接入，而不是原地替换 SDXL 配置。16、32 和 64 的 LoRA 触发方式及权重必须通过固定评测集校准；不得假设 mapchip 的 48×48 训练触发词天然等于 64×64 输出。

`pixel-art-xl` 不作为自动下载依赖。用户可在高级模型设置中添加自己合法取得的本地权重，项目只保存路径和哈希。原因是 Hugging Face 的 CreativeML OpenRAIL-M 标注与公开讨论中提到的其他平台限制不一致：<https://huggingface.co/nerijs/pixel-art-xl>、<https://huggingface.co/nerijs/pixel-art-xl/discussions/7>。

模型、ComfyUI 和节点不提交到 Git 仓库。依赖清单必须保存来源、许可、固定版本和 SHA-256；首次下载前展示给用户。发布桌面安装包之前必须重新完成第三方许可审查。

LoRA 或适配器页面标注的许可不消除基础模型许可义务。首次设置页必须同时展示当前 `ModelProfile` 组合中所有模型与代码组件的许可，并随依赖清单保存副本或许可链接。

## 8. 后处理

MVP 后处理是可独立测试的确定性管线：

1. 验证 ComfyUI 输出可解码、为方形图像且具有受支持通道。
2. 统一转换到内部 RGB 表示；普通方块 MVP 不处理 Alpha。
3. 将高清画布划分为目标逻辑网格，每格对 sRGB 的 R、G、B 三通道分别取中位数，组合成一个确定的代表色。
4. 输出严格的 16×16、32×32 或 64×64 PNG。
5. 默认不限制调色板、不自动删除孤立像素，避免不可逆地破坏矿点和纹理细节；高级设置可启用调色板限制。
6. 生成最近邻放大预览。
7. 生成 3×3 平铺预览。
8. 计算左右边缘和上下边缘的颜色差异并生成数值 seam score。单边分数为对应边缘像素 RGB 欧氏距离的平均值，再除以 `sqrt(3) * 255` 归一化到 0–1；报告同时保存水平、垂直和二者平均分。分数只用于提示，不阻止采用。
9. 保存原始高清图、最终 PNG、预览和报告。

MVP 不自动修复接缝。接缝修复会改变生成内容，应在后续通过单独可预览、可撤销的步骤设计。

## 9. WebUI 信息架构

### 9.1 全局页面

- 首次设置：环境、ComfyUI、节点、模型下载和许可确认。
- 项目列表：新建、打开、删除项目记录和定位本地目录。
- 项目概览：导入快照、资源格式、覆盖统计、最近任务、导出历史。
- 设置：环境日志、模型配置、磁盘位置和显存基准说明。

### 9.2 五步生成向导

1. 导入与识别：建立项目、验证包、展示覆盖摘要。
2. 选择目标：搜索和筛选普通方块；默认只显示未覆盖。
3. 参考与描述：选择 1–8 张风格参考，上传可选结构参考，编辑补充描述。
4. 生成候选：选择分辨率、并行方式；高级区包含 seed、denoise、LoRA 权重、IP-Adapter 权重和模型配置。
5. 采用与导出：逐张出现候选，展示最终贴图、最近邻预览、3×3 平铺、seam score 和参数；显式采用后才写入工作副本。

向导允许返回前一步修改配置。开始新任务不会删除旧候选。取消运行只取消未完成部分，已生成候选继续保留。

## 10. 状态、错误和恢复

任务状态：

```text
queued -> generating -> postprocessing -> completed
                    \-> failed
                    \-> canceled
```

任一步失败都不自动改变参数、不采用候选、不修改工作副本。重试创建新任务并引用原任务，确保失败历史可追踪。

统一错误至少包含：

```json
{
  "code": "GPU_OUT_OF_MEMORY",
  "stage": "generating",
  "user_message": "生成失败：显卡显存不足",
  "recommended_actions": [
    "降低候选并行数",
    "关闭占用显存的后台应用",
    "关闭其他 ComfyUI 实例"
  ],
  "technical_details": "...",
  "log_reference": "..."
}
```

主要错误类别：资源包损坏或不安全、`pack.mcmeta` 无效、目录格式不支持、CUDA 不可用、模型缺失或哈希不符、ComfyUI 启动或连接失败、OOM、用户取消、图片解码失败、磁盘空间不足、权限错误和导出校验失败。

## 11. 文件安全、隐私与许可边界

- 应用只访问用户主动选择的导入包、项目目录和模型目录。
- 不扫描硬盘查找 Minecraft JAR 或游戏安装。
- ZIP 解压必须防止 path traversal、绝对路径和设备路径写入。
- 原始导入文件只读；导入后记录哈希。
- 采用结果先写同目录临时文件并完成校验，再原子替换工作副本目标。
- 导出先写临时 ZIP，重新打开验证后再移动到最终路径。
- 导出 ZIP 不包含项目 JSON、日志、原始高清图、参考上传或候选预览。
- 仓库内只包含独立整理的最小路径元数据，不包含原版图像或完整游戏资产。
- 项目名称和 UI 不使用 Minecraft Logo，也不暗示 Mojang/Microsoft 官方认可；README 必须包含非官方声明。

Minecraft 官方使用指南将纹理和图像列为其资产，并限制重新分发游戏内容，因此不内置原版 PNG 是明确的保守边界：<https://www.minecraft.net/en-us/usage-guidelines>、<https://www.minecraft.net/en-us/eula>。这不是正式法律意见；发布和商业化前仍需进行许可审查。

## 12. 测试策略

### 12.1 单元测试

- `pack.mcmeta` 有效、无效、兼容范围和不支持格式；
- ZIP 根目录、嵌套根目录、冲突路径和 path traversal；
- 路径大小写、分隔符、已覆盖/未覆盖/未知分类；
- 项目清单 schema 和迁移；
- 任务状态转换、取消和重试；
- 错误翻译。

### 12.2 图像测试

使用纯色、棋盘格、渐变、结构线、随机噪声和人工接缝图：

- 输出尺寸严格为 16、32、64；
- 中位数网格吸附结果稳定；
- RGB 模式和 PNG 编码正确；
- 最近邻预览不引入插值颜色；
- 3×3 平铺布局正确；
- seam score 对人工接缝图高于无缝图。

### 12.3 集成与 E2E

- 使用假的 ComfyUI HTTP/WebSocket 服务验证排队、四候选进度、取消、断线和错误。
- 导入固定测试包，匹配缺失项，生成模拟候选，采用并导出。
- 校验原始快照哈希不变、工作副本仅目标文件变化、导出 ZIP 根结构正确且无项目内部文件。
- 重启 FastAPI 后恢复项目、任务和候选。

### 12.4 真实 GPU 验证

真实模型验证不进入普通 CI。固定 ComfyUI、workflow、驱动记录和模型哈希后，在 8 GB NVIDIA 显卡上至少验证逐张模式，并记录 1、2、4 三种批量大小的峰值显存、系统内存、总耗时和成功/失败状态。界面只展示对应这套固定配置的参考结果，不宣称自动判断用户设备适配性。

## 13. MVP 验收标准

v0.1 只有在以下条件全部满足时才算完成：

1. Windows 启动脚本能建立隔离环境并启动健康的 ComfyUI、FastAPI 和 WebUI。
2. 原始导入资源包在所有操作后哈希不变。
3. 固定测试包的覆盖状态与目录配置完全一致。
4. 一个未覆盖普通方块能完成无结构参考的 text2img + IP-Adapter 流程。
5. 同一目标能完成带用户上传结构参考的 img2img + IP-Adapter 流程。
6. 四候选能按 1、2、4 三种批量配置构建任务；逐张模式在目标 8 GB 环境完成真实冒烟测试。
7. 每个成功候选都有原始图、严格尺寸最终 PNG、放大预览、3×3 平铺和报告。
8. 用户中途取消后，已完成候选保留，未完成候选停止。
9. 点击采用后只修改工作副本中的标准目标路径，并更新覆盖状态。
10. 导出 ZIP 能重新打开，包含有效 `pack.mcmeta` 和正确目标 PNG，不包含项目内部产物。
11. 重启后项目、任务、候选和采用结果仍可访问。
12. OOM、模型缺失和 ComfyUI 断线能显示易懂说明、推荐操作和原始技术详情。
13. 单元测试、图像测试、模拟 ComfyUI 集成测试和资源包 E2E 全部通过。

## 14. 后续阶段

### v0.2：Java 批量普通方块

- 从未覆盖清单多选目标；
- 共享风格参考集合；
- 每目标独立提示词覆盖；
- 可暂停、恢复和取消的队列；
- 逐目标候选、采用和失败重试；
- 批量导出清单和质量摘要。

### v0.3：Java 物品与复杂方块

- 物品 Alpha 与轮廓策略；
- 透明方块；
- top/side/bottom 多面关联；
- 类别专用提示词和后处理；
- 动画仍作为独立后续规格，不混入本阶段。

### v0.4：基岩版适配器

- 读取 `manifest.json` 和原生资源格式；
- 基岩版目录元数据；
- 基岩版工作副本、索引维护和 `.mcpack` 导出；
- 不做 Java/基岩版转换。

### v1.0 方向

- Tauri/Electron 桌面壳；
- 可安装模型配置；
- 自动参考推荐；
- 风格一致性评测；
- 更完整的资源包质量报告。

## 15. 关键设计结论

1. 产品核心是 Minecraft-aware 项目管理、路径目录、生成编排和后处理，而不是某个单一模型。
2. 原始包永不直接修改；项目工作副本和显式采用是唯一写入路径。
3. 内置目录只保存路径元数据，不保存原版素材，也不读取用户 Minecraft 安装。
4. 风格参考直接进入 IP-Adapter；结构参考可选并决定 text2img 或 img2img。
5. 固定四候选；并行度由用户决定，程序只说明成本，不自动替用户降级。
6. 默认模型采用许可来源较清晰的 mapchipLora，同时保留用户自有 pixel-art-xl 权重支持。
7. v0.1 只证明 Java 普通方块单张闭环；v0.2 立即沿同一数据模型扩展批量。
