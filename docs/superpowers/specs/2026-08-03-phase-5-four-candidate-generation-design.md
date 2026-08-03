# AIMCTextureGen Phase 5 四候选生成流程设计

- 日期：2026-08-03
- 状态：已通过交互式头脑风暴确认，等待书面规格复核
- 适用阶段：Phase 5
- 基线提交：`af23000`
- 依赖阶段：Phase 1–4 已完成
- 上位规格：[`2026-07-18-aimc-texturegen-mvp-design.md`](2026-07-18-aimc-texturegen-mvp-design.md)
- 架构决定：[`ADR-0003`](../../adr/0003-native-batch-seeds-and-generation-coordinator.md)

## 1. 目的与交付边界

Phase 5 完成 Java 版普通、单贴图、不透明、非动画方块的真实生成闭环：

```text
打开已导入项目
  -> 选择一个未覆盖目标
  -> 选择 0–8 张风格参考
  -> 可选一张结构参考
  -> 配置分辨率和原生批量大小
  -> 固定生成四个候选
  -> 对每个候选执行确定性后处理
  -> 持久化并逐步展示候选与质量报告
```

Phase 5 不采用候选、不修改 `pack/`、不导出资源包。候选采用、覆盖刷新、
ZIP 导出、启动脚本和最终 UI polish 属于 Phase 6。

本阶段保持以下既有边界：

- v0.1 只支持 Windows、NVIDIA CUDA 和 Java 版普通方块；
- WebUI 只调用 FastAPI，不读取本地路径或直接调用 ComfyUI；
- FastAPI 是业务边界，受管 ComfyUI 只负责受控 GPU 推理；
- `source/` 和原始导入包不可修改；
- OOM 或其他失败不自动改参数、降并行度、采用候选或修改工作副本；
- 普通自动化测试不下载模型、不启动真实 ComfyUI、不使用第三方游戏素材。

## 2. 本阶段确认的产品变更

本设计有意修订最初 MVP 规格中的两项旧结论：

1. 风格参考从“必须 1–8 张”改为“允许 0–8 张”。
2. 四个候选不再各自拥有可跨并行方式稳定的独立 seed；任务持久化的是
   ComfyUI 原生执行批次及每批一个 base seed。

原因是固定的 ComfyUI `KSampler` 接受一个 seed。`batch_size=2/4` 时，
该 seed 初始化一个批量噪声流，批次内候选由其位置区分。若仍声称四个候选各自
拥有稳定 seed，就不能同时提供真实的 ComfyUI 原生批量语义。

具体决定及被拒绝方案见
[`ADR-0003`](../../adr/0003-native-batch-seeds-and-generation-coordinator.md)。

## 3. 固定产品语义

### 3.1 四候选与批次计划

候选数始终为四。用户选择的并行度只允许 1、2 或 4，它决定执行批次计划：

| 并行度 | 批次数 | 每批候选序号 | 每批 seed 数 |
|---:|---:|---|---:|
| 1 | 4 | `[0]`、`[1]`、`[2]`、`[3]` | 4 |
| 2 | 2 | `[0,1]`、`[2,3]` | 2 |
| 4 | 1 | `[0,1,2,3]` | 1 |

任务创建时自动生成并持久化每个批次的 base seed。seed 在创建后只读：

- 新任务生成新的批次 seed；
- 重试保留父任务的批次计划和 seed；
- 用户不在 Phase 5 手工编辑 seed；
- 候选身份是
  `(candidate_index, batch_index, position_in_batch, batch_seed)`；
- 改变并行度可能改变结果，不承诺同一候选序号跨并行方式稳定。

应用不根据设备自动选择并行度，也不在 OOM 后自动降低并行度。UI 只展示与
当前 verified profile 对应的实测显存、系统内存和耗时参考。

### 3.2 单活动任务

整个本地应用同时只允许一个非终态生成任务，不是每个项目各允许一个：

- `queued`、`generating` 和 `postprocessing` 均占用唯一任务槽；
- 创建第二个任务返回可读冲突，并指向当前任务；
- 用户可以取消当前任务，不必等待四个候选全部完成；
- 只有取消已被确认并持久化为终态后，才释放任务槽；
- Phase 5 不提供多任务排队。

该限制匹配 v0.1 的单用户、单受管 GPU 产品，并使全局 ComfyUI interrupt
可以安全对应唯一产品任务。未来批量材质生成可在同一个产品任务内部增加目标
队列，不需要先允许多个互相竞争的顶层任务。

### 3.3 参数展示

默认生成界面保持简洁。始终展示：

- 目标逻辑分辨率 16、32 或 64；
- 并行度 1、2 或 4；
- 固定四候选说明；
- 当前 profile 的实测资源占用参考。

折叠高级区展示：

- 用户负面提示词；
- 有结构参考时的 denoise；
- 有风格参考时的风格强度；
- 只读模型配置身份。

任务创建后在详情中只读展示批次 seed。Phase 5 不暴露采样器、步数、CFG、
LoRA 权重或 workflow 节点。高级区最终采用侧栏、弹窗还是折叠面板，由
Phase 6 UI polish 决定；本阶段只固定信息层级和行为。

## 4. 架构

### 4.1 组件关系

```text
React 五步向导
      |
      | HTTP commands + WebSocket snapshots
      v
FastAPI routes
      |
      +--> ReferenceService
      |      上传、列举、删除、任务输入冻结
      |
      +--> GenerationService
      |      schema、提示词、批次计划、workflow、产物和状态
      |
      +--> GenerationCoordinator
             单活动任务、开始、继续、取消、恢复
                    |
                    +--> ComfyUIManager
                    |      受管进程启动、停止、健康和日志
                    |
                    +--> ComfyUIClient
                    |      上传、prompt、WS、history、interrupt
                    |
                    +--> TextureProcessor
                           Phase 2 确定性后处理
```

`GenerationCoordinator` 是 FastAPI 应用拥有的长生命周期后台协调器。它不把
内存当成恢复依据；任务 JSON、候选状态和原子发布的产物才是事实来源。

不引入独立 worker、Redis、Celery 或外部队列。也不把长任务生命周期绑定到
单个 HTTP 请求。

### 4.2 模块职责

`ReferenceService`：

- 列出工作包内可作为参考的 PNG；
- 接收浏览器上传内容而不是本地路径；
- 验证、保存和删除项目级参考；
- 创建任务时复制并冻结实际输入；
- 不编译提示词或 workflow。

`GenerationService`：

- 创建和验证 schema 3 请求；
- 生成批次计划和 seed；
- 构建并冻结提示词；
- 解析 profile/workflow 绑定；
- 编译单批 ComfyUI prompt；
- 校验、映射和发布候选产物；
- 调用 Phase 2 后处理；
- 管理重试 lineage 和状态转换；
- 不拥有 ComfyUI 子进程。

`GenerationCoordinator`：

- 强制全局单活动任务；
- 开始或继续 queued 任务；
- 在每个持久化边界重新检查取消；
- 串行执行任务内批次；
- 协调 ComfyUI 自动启动、interrupt 和孤立 prompt 清理；
- 不包含 SDXL 节点 ID、图像算法或 ZIP 逻辑。

API 路由只做请求/响应映射、服务调用和错误转换。

## 5. 持久化模型

### 5.1 schema 3 请求

Phase 5 新建任务使用不可变的请求 schema 3。概念结构如下：

```json
{
  "schema_version": 3,
  "project_id": "<uuid>",
  "job_id": "<uuid>",
  "target": {
    "semantic_id": "minecraft:deepslate",
    "relative_path": "assets/minecraft/textures/block/deepslate.png",
    "catalog_id": "java-dev-format-34",
    "catalog_schema_version": 1
  },
  "prompt": {
    "template_id": "java-block-prompt",
    "template_version": 1,
    "user_description": "cold blue-gray stone",
    "user_negative_prompt": "",
    "compiled_positive": "...",
    "compiled_negative": "..."
  },
  "resolution": 16,
  "parallelism": 2,
  "execution_batches": [
    {
      "batch_index": 0,
      "candidate_indices": [0, 1],
      "seed": 123
    },
    {
      "batch_index": 1,
      "candidate_indices": [2, 3],
      "seed": 456
    }
  ],
  "references": {
    "style": [],
    "structure": null
  },
  "advanced": {
    "denoise": null,
    "style_weight": null
  },
  "model_profile": {
    "profile_id": "sdxl-mapchip-ipadapter",
    "profile_version": "2",
    "profile_manifest_digest": "...",
    "runtime_id": "comfyui-windows-nvidia",
    "runtime_version": "0.29.2",
    "workflow_variant": "text2img-no-style",
    "workflow_digest": "..."
  },
  "created_at": "..."
}
```

正式类型必须拒绝未知字段、非法候选分组、重复/遗漏候选序号、与并行度不符的
批次数量、越界 seed、能力不匹配的参考图和未锁定 profile/workflow。

### 5.2 参考快照

每个冻结参考记录：

- 任务内稳定 reference ID；
- 类型：style 或 structure；
- 来源：工作包相对路径或项目上传 ID；
- 原来源的显示标签；
- 任务内副本相对路径；
- 字节数、宽高、颜色模式和 SHA-256。

任务创建完成后不再从 `pack/` 或项目图库读取推理输入。删除项目级上传或
Phase 6 采用新候选都不会改变旧任务。

### 5.3 候选和批次状态

四个候选继续使用稳定序号 0–3。候选状态至少能区分：

- 尚未开始；
- 当前批次推理中；
- 原始图已验证；
- 后处理中；
- 完成；
- 失败；
- 取消；
- 从父任务继承。

每个候选记录批次身份、原始图、最终 PNG、最近邻预览、3×3 预览、报告、
各产物 SHA-256、错误和可选 lineage 来源。

任务状态继续使用现有顶层状态：

```text
queued -> generating -> postprocessing -> completed
                    \-> failed
                    \-> canceled
```

取消中的显示由持久化 `cancel_requested_at` 和当前阶段表达，不增加一个无法由
旧状态机理解的顶层临时状态。

所有状态 revision 单调递增。候选/批次边界立即提交；采样进度限频提交。
WebSocket 只在提交后发送同一份持久化快照。

### 5.4 旧 schema

- schema 1/2 请求保持原始字节和语义可读；
- 不给旧任务静默补上 profile v2 或新批次计划；
- 无法安全执行的旧任务显示为 legacy；
- 用户可以基于其可兼容表单值创建新的 schema 3 任务；
- 旧任务不会占用活动任务槽，除非它本来就是 Phase 3 恢复规则下的非终态
  记录；这类记录必须先显式取消或转为既定中断终态。

## 6. 参考图管理

### 6.1 来源

风格参考合计允许 0–8 张：

- 工作包内已覆盖贴图；
- 工作包内未知/自定义但可安全解码的合格贴图；
- 浏览器上传的外部风格 PNG。

结构参考允许 0 或 1 张浏览器上传 PNG。Phase 5 不从 Minecraft 安装、JAR
或任意本地路径读取参考图。

### 6.2 验证

项目级上传和包内候选使用同一图像验证器：

- 仅静态 PNG；
- 编码大小最多 16 MiB；
- 宽高各为 16–4096；
- 总像素数不超过 16,777,216；
- 必须为方形；
- 完整解码后为 RGB 或 RGBA；
- 不接受动画/APNG、多帧、截断或解压炸弹；
- 服务端生成存储 ID，原文件名不参与路径；
- 临时文件完成解码、元数据和哈希校验后原子发布。

项目级目录：

```text
uploads/
├─ style-references/
│  └─ <reference-id>/
│     ├─ original.png
│     └─ metadata.json
└─ structure-references/
   └─ <reference-id>/
      ├─ original.png
      └─ metadata.json
```

任务内目录：

```text
jobs/<job-id>/inputs/
├─ style/
│  ├─ 00.png
│  └─ ...
├─ structure.png
└─ references.json
```

没有结构参考时不得创建伪结构文件。没有风格参考时 style 目录可以不存在。

### 6.3 删除

删除项目图库参考只删除项目级记录。已创建任务使用独立副本，不受影响。
删除必须经过项目根安全解析和原子元数据更新，不接受磁盘路径参数。

## 7. 提示词

### 7.1 版本化纯函数

`java-block-prompt` version 1 按固定顺序组合：

```text
resolution prefix
-> "pixel art, seamless tileable square block texture"
-> "Minecraft Java Edition resource-pack texture"
-> "flat albedo, uniform material covering the full canvas"
-> "edge-to-edge continuous texture, crisp hard-edged pixel clusters"
-> "no border, no centered subject"
-> catalog display_name
-> catalog prompt_terms
-> normalized user description
```

默认负面提示词为：

```text
item icon, isolated object, centered composition, empty margin,
white background, border, frame, visible seam, perspective, 3d render,
scene, text, watermark, drop shadow, soft focus, anti-aliasing,
blurry gradient, lighting vignette
```

用户负面提示词规范化后追加到默认负面提示词。编译结果非空且不超过
profile 通用文本上限。提示词函数不调用额外 LLM 或视觉模型，也不把参考图
自动转换成文字。

### 7.2 分辨率前缀

- 16 使用 mapchip 已知的 `1616` 触发方式；
- 32 使用 `3232`；
- 64 使用明确的逻辑 64×64 像素网格描述；
- 不把 mapchip 的 `4848` 训练触发词声称为 64。

64 的精确措辞和权重只有在固定评测集真实验证后才能写入 verified profile。
该校准是 v2 profile 提升门禁，不改变目标输出仍由 Phase 2 确定性缩放到
64×64 的事实。

### 7.3 被拒绝的物品提示词

Phase 5 不使用“item icon、主体四边留白、白色背景、最多四种颜色、所有色块
至少 2×2”等物品图标提示。它们与方块的满画布和无缝平铺目标冲突。未来物品
支持需要独立提示词、透明通道和轮廓设计。

## 8. Model Profile v2

### 8.1 不修改 v1

已验证的 `sdxl-mapchip-ipadapter` version 1、两个 workflow、manifest 摘要和
真实证据保持不变。

Phase 5 新增同一 profile ID 的 version 2。它复用 v1 已校验的模型和自定义
节点 artifact 哈希，不重复下载大文件，但拥有新的 manifest schema、能力、
workflow 变体和摘要。安装器按内容哈希复用已安装 artifact。

### 8.2 四个显式 workflow 变体

v2 固定四个 API workflow：

1. `text2img-no-style`
2. `text2img-style`
3. `img2img-no-style`
4. `img2img-style`

每个文件独立跟踪并锁定 SHA-256。无风格变体不加载或执行
CLIP Vision/IP-Adapter 图；有风格变体继续使用固定的
`STANDARD (medium strength)` preset、`style transfer` weight type 和
`average` multi-reference 合并。

业务层只提交通用输入。SDXL 节点 ID、`EmptyLatentImage`、
`RepeatLatentBatch`、`KSampler` 和 `SaveImage` 映射只存在于 v2 绑定层。

### 8.3 原生批量

- text2img 设置 `EmptyLatentImage.batch_size` 为 1、2 或 4；
- img2img 编码单张结构参考后，用受验证的 latent batch 节点重复为 1、2 或 4；
- `KSampler.seed` 使用当前批次 base seed；
- 一个任务批次只提交一个 ComfyUI prompt；
- `SaveImage` 返回数组顺序映射到该批 `candidate_indices`；
- 返回数量必须精确等于 batch size；
- 任一输出在数量、格式、尺寸或完整性上不合格时，不发布半批原始候选。

### 8.4 verified 门禁

v2 初始状态是 candidate。只有以下真实验证全部完成后才能标记 verified：

- 四种条件组合都完成真实推理；
- batch 1、2、4 的输出数量和顺序得到验证；
- 真实输出全部通过 Phase 2 后处理；
- 记录固定机器、驱动、runtime/profile/workflow 摘要；
- 记录每种批量的峰值显存、系统内存、耗时和成功/失败；
- 脱敏证据不包含参考图、生成图、绝对路径或受版权保护的测试素材。

## 9. 执行流程

### 9.1 创建与开始分离

创建任务和开始任务是两个持久化动作：

1. 创建请求校验项目、目标、参考、参数和 profile；
2. 冻结参考副本、提示词、批次计划和模型绑定；
3. 原子发布 revision 0 的 queued 任务；
4. 开始命令交给协调器执行。

WebUI 的“生成”按钮顺序调用创建和开始。若浏览器在两步之间关闭，queued
任务仍可在下次打开时显式继续。

### 9.2 自动启动受管 ComfyUI

开始任务时：

- runtime/profile 已安装且 ready、进程 stopped：自动启动并等待健康；
- 进程 ready：直接使用；
- 安装缺失、哈希不符或 profile 未 verified：不下载、不修复；
- 启动或前置校验失败：任务进入 failed，保存易懂错误和建议。

### 9.3 每批执行

每批严格执行：

1. 从请求读取已冻结批次和 workflow 绑定；
2. 上传任务内参考副本并生成安全远端名；
3. 编译该批 prompt；
4. 提交一个 ComfyUI prompt；
5. 监听只属于该 prompt 的进度和完成历史；
6. 获取声明输出节点的有序图片列表；
7. 验证列表数量、每张图片和输出契约；
8. 逐张写入同目录临时文件，复算哈希后原子发布到 `raw/`；
9. 逐候选执行 Phase 2 后处理；
10. 发布 final、nearest preview、3×3 preview 和 report；
11. 每完成一个候选即提交状态并通知 UI；
12. 进入下一批前重新检查取消。

ComfyUI 推理批次是原子单元。取消或失败时未形成完整、已验证输出列表的批次
不发布部分 raw 候选。CPU 后处理以单个候选为原子单元。

## 10. 进度、取消、重试和恢复

### 10.1 进度

- 采样节点/步数进度限频写入任务状态；
- 候选和批次边界立即写入；
- 浏览器断线不取消任务；
- WebSocket 重连先获得最新持久化快照；
- 旧 revision 不覆盖新状态。

### 10.2 取消

取消命令幂等：

1. 持久化 `cancel_requested_at`；
2. 当前 prompt 存在时调用 ComfyUI interrupt；
3. 未开始批次标记为取消；
4. 已完成候选保持完成；
5. 后处理在候选边界停止，不中断原子文件发布；
6. 等待 ComfyUI 确认当前 prompt 不再执行；
7. 超时或失联时停止应用自己管理且身份仍匹配的 ComfyUI 子进程；
8. 只有 GPU 工作已确认停止后，任务才进入 canceled 并释放全局槽。

应用不终止未知外部进程。

### 10.3 失败和重试

批次失败后不继续后续批次。之前完成的候选和已验证 raw 继续保留。

重试创建新 job 并记录 `parent_job_id`：

- 请求参数、profile、workflow、并行度和批次 seed 不变；
- 已完成候选通过同项目 lineage 引用继承；
- lineage 保存来源 job/candidate、产物相对标识和 SHA-256；
- 解析继承产物时重新验证同项目边界和哈希；
- 若某批全部 raw 已验证，只补缺失的确定性后处理；
- 若某批 raw 不完整，使用原 seed 重跑整个原生批次；
- 父任务保持不可修改。

Phase 5 不提供 job 删除。未来若增加删除，必须阻止删除仍被 lineage 引用的
父任务，或先安全物化依赖产物。

### 10.4 重启

- queued 保持 queued，占用唯一任务槽，但不自动启动 GPU；
- UI 显示“继续任务”，由用户显式开始；
- generating/postprocessing/取消中的任务按 Phase 3 恢复规则转为
  `JOB_INTERRUPTED` failed；
- 已完成候选和已验证 raw 保留；
- 不静默恢复扩散推理；
- 允许新任务前，协调器清理受管 ComfyUI 中属于中断任务的孤立 prompt；
- interrupt 无法确认时停止身份匹配的受管子进程。

## 11. API 与 WebSocket

Phase 5 在现有项目/任务 API 上增加能力，不接受绝对磁盘路径。

服务能力包括：

- 列出可生成目标；
- 列出包内合格参考；
- 上传、列出和删除项目级风格/结构参考；
- 创建 schema 3 queued job；
- 开始或继续 queued job；
- 取消 job；
- 创建 lineage retry；
- 获取 job/candidate；
- 读取受控候选产物；
- 订阅单 job 状态快照。

候选产物由
`project_id + job_id + candidate_id + artifact_kind`
寻址。服务端映射并验证实际路径，URL 不包含项目绝对路径。

WebSocket 契约：

- 连接后第一条业务消息是最新持久化 snapshot；
- 后续消息只发送更高 revision；
- 支持心跳和前端自动重连；
- WebSocket 只通知，不执行开始、取消或重试；
- 前端发现断线或 revision 异常时通过 HTTP 重新读取当前 job。

## 12. 五步引导式 WebUI

Phase 5 复用并完成既定信息架构：

1. **导入与识别**：复用现有项目导入和覆盖摘要。
2. **选择目标**：默认只显示 `mvp_eligible=true` 的未覆盖普通方块，支持搜索。
3. **参考与描述**：选择 0–8 张包内/上传风格参考，可选结构参考，填写补充描述。
4. **生成候选**：选择分辨率和并行度；高级区按条件显示参数。
5. **候选结果**：四张逐步出现，展示 final、nearest、3×3、seam score 和参数。

Phase 5 不显示无功能的“采用”或“导出”按钮。Phase 6 在第五步补齐实际操作。

任务创建前可以返回前一步修改。创建后参数冻结；修改配置时创建新任务，旧任务
和候选保留。存在非终态任务时，UI 提供查看和取消当前任务，不允许创建第二个。

## 13. 错误模型

错误至少持久化：

```text
error_code
user_message
recommended_actions[]
technical_details
retryable
occurred_at
```

至少区分：

- `GENERATION_JOB_CONFLICT`
- `PROFILE_NOT_READY`
- `COMFY_START_FAILED`
- `COMFY_QUEUE_REJECTED`
- `COMFY_DISCONNECTED`
- `COMFY_TIMEOUT`
- `GPU_OUT_OF_MEMORY`
- `COMFY_EXECUTION_FAILED`
- `OUTPUT_CONTRACT_VIOLATION`
- `REFERENCE_INVALID`
- `POSTPROCESSING_FAILED`
- `CANCEL_CONFIRMATION_FAILED`
- `JOB_INTERRUPTED`

错误翻译优先使用受控异常类型和 ComfyUI execution history，不依赖易变的完整
英文日志字符串。OOM 建议降低并行数、关闭显存占用应用或停止其他 ComfyUI，
但不自动执行这些操作。

技术详情和受管日志默认折叠。持久化前移除访问令牌、请求头和不必要的绝对路径。

## 14. 测试策略

### 14.1 自动化

普通 CI 使用合成图片、临时项目和假 ComfyUI，证明：

- schema 1/2 兼容读取和 schema 3 严格验证；
- 1/2/4 批次计划、seed、候选顺序和重试继承；
- 提示词精确输出和版本冻结；
- 0–8 风格参考、可选结构参考和输入快照；
- 四种 workflow 选择；
- batch 1/2/4 数量和有序映射；
- 输出缺失、多余、损坏或尺寸错误时整批拒绝；
- 进度、断线、OOM、超时、取消和重试；
- queued/active 重启恢复和孤立 prompt 清理；
- 全流程后 `source/`、原始 ZIP 和 `pack/` 哈希映射不变。

前端自动化证明向导导航、默认目标过滤、条件高级字段、单任务冲突、自动启动、
进度、增量候选、取消、继续、重试和 legacy 显示。

### 14.2 真实 GPU

本地 Phase 5 smoke 使用 v2 profile 验证：

- text2img 无风格；
- text2img 有风格；
- img2img 无风格；
- img2img 有风格；
- 原生 batch 1、2、4；
- 输出顺序、数量、后处理和持久化；
- 峰值显存、系统内存、耗时和状态。

原始输出、参考图和生成结果只保留在忽略目录。跟踪证据只记录脱敏机器/profile
身份、指标、状态和产物哈希。

### 14.3 本地真实资源包

用户提供的三个真实包只位于：

```text
runtime/manual-test-packs/phase-5/
```

该目录已由仓库级 `runtime/` 忽略。测试前必须运行 `git check-ignore`，测试
前后复算原 ZIP 哈希。

用途：

- `third-party.zip`：主 `pack_format=34`，带 overlay，作为真实主验收输入；
- 在忽略目录创建派生副本，仅移除基础
  `assets/minecraft/textures/block/deepslate.png`；
- 保留 `stone.png` 作为包内风格参考；
- overlay 原样保留，不在 Phase 5 合并或修改；
- `legacy-converted.zip`：`pack_format=32`，验证不支持格式的明确拒绝；
- `vanilla-latest.zip`：缺少主 `pack_format`，验证不猜测 `min/max` 的明确拒绝。

不得提交能识别具体真实包的产品名、作者、原包哈希，也不得提交 ZIP、PNG、
预览、原始生成图或含素材截图。自动化夹具继续全部由项目代码生成。

### 14.4 人工验收

人工只检查自动化不能证明的本阶段风险：

- 正常 Windows 桌面宽度下完整向导可操作；
- 真实进度和候选逐步出现；
- 取消后完成候选保留；
- OOM、冲突和中断说明易懂；
- 四个候选的视觉质量和参考图效果；
- 浏览器控制台没有应用来源错误。

不机械重复旧阶段的 400/600/900 px 全套检查。只有 Phase 5 实际修改相关
响应式规则时才做针对性抽查；最终多尺寸、控件对齐、间距、禁用态和视觉层级
属于 Phase 6。

## 15. 文档与变更控制

本设计批准后：

- 更新最初 MVP 规格中的参考图、seed、提示词、向导和状态语义；
- 以 ADR-0003 记录原生批次和协调器决定；
- 更新 `AGENTS.md` 的产品不变量、真实素材和风险化测试规则；
- 更新路线图 Phase 5 入口；
- 实际测试命令存在后再更新 `docs/TESTING.md`；
- v2 真实验证完成后再更新 `docs/MODEL_PROFILES.md`；
- `ONBOARDING.md` 始终指向唯一当前阶段和真实状态。

## 16. Phase 5 完成标准

Phase 5 只有同时满足以下条件才完成：

1. 五步向导能从已导入项目创建并执行 schema 3 任务。
2. 0–8 张风格参考和可选结构参考四种组合可执行。
3. batch 1、2、4 都严格产生四个有序候选。
4. 每个成功候选具有 raw、严格尺寸 final、nearest、3×3 和 report。
5. 单活动任务、继续、取消、失败和 lineage retry 符合本规格。
6. 重启不静默恢复 GPU，已完成候选不丢失。
7. OOM 和其他错误不自动改参数或修改 `pack/`。
8. v2 profile 完成真实 GPU 门禁并记录脱敏证据。
9. 三个本地真实包完成各自的正/负验收角色。
10. 后端、前端、集成和文档门禁实际通过。
11. 原始 ZIP、`source/` 和 `pack/` 在本阶段前后保持不变。
12. `ONBOARDING.md`、阶段计划、测试文档和模型文档反映真实结果。
