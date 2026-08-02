# AIMCTextureGen 当前交接

最后核对日期：2026-08-02

## 当前状态

Phase 3 已通过合并提交 `ae424b1` 进入 `master`/`origin/master`。当前 checkout
位于新阶段分支 `codex/phase-4-managed-comfyui`。Phase 4 的设计和实施计划已
落盘。Task 1–10 的实现与真实 GPU 冒烟已完成；本轮收尾还修正了 Windows
环境规范化、后台安装执行、端口占用错误映射、重建 manager 的安全停止、
profile 哈希/receipt 就绪判定，以及取消/完成状态竞态：

收尾后又修正了一个运行时性能回归：profile 完整性校验改为有界内存的流式
哈希并按文件元数据缓存；旧版已验证安装记录不会因 WebUI 轮询反复读取多 GB
模型。

受管 ComfyUI 的 API 冷启动就绪窗口已调整为 60 秒；启动超时会提示等待冷启动
或查看受管 ComfyUI 日志，不再把该类错误笼统提示为端口问题。

- 后端依赖已锁定：`httpx==0.28.1` 转入生产依赖，新增 `websockets==16.1.1`
  与 `py7zr==1.1.3`，仓库 `.venv` 已按文档命令重建并通过 `pip check`。
- 自定义节点归档已在仓库外独立下载锁定：`ComfyUI_IPAdapter_plus`
  commit `a0f451a5…dec0ef`，306,422 字节，SHA-256
  `c6c49c82aa65cb96b93bdf9f9b547f9c95310a2668a7a9aaa0285cccf4590347`，
  归档唯一根目录与 commit 一致。
- 新增严格 manifest 契约：`comfy/manifests.py`（冻结 Pydantic 模型、
  未知字段拒绝、SHA/路径/大小/可变版本校验、canonical 序列化）与
  `comfy/registry.py`（只读、按文件名确定性排序、runtime/profile 兼容
  校验、workflow 根目录逃逸拒绝）。
- 两个 tracked manifest 已提交；runtime 使用固定版本与摘要，profile 状态为
  `verified`：
  `manifests/runtimes/comfyui-windows-nvidia-v0.29.2.json` 与
  `manifests/model-profiles/sdxl-mapchip-ipadapter-v1.json`。
  规范摘要：runtime `89f05bd7…70ee86`，profile `b4e10bd7…a40239`；
  workflow 文件与其 SHA-256 由 Task 8 写入。
- 聚焦门禁 82/82 通过；全量后端回归 698/698 通过（89% 覆盖率，
  `-W error` 零警告）；`git diff --check` 通过。

Phase 4 Task 2–7 已于 2026-08-02 完成（提交 `dcf6bb5`、`b54444a`、
`986abf0`、`698ee81`、`5104ac1`、`39ea930`）：

- Task 2：只读环境检查（`environment.py`）、consent 绑定安装计划
  （`installer.py`）与原子安装操作记录（`install_state.py`）；
  检查与计划构建不创建任何目录，stale digest/缺失 acceptance/阻塞环境
  在创建 `runtime/` 之前失败。
- Task 3：有界流式下载器（`downloads.py`）支持 `.part`+sidecar 断点续传、
  Range/忽略 Range、重定向限额与 allowed-host、大小/哈希发布前校验、
  取消仅留有效 partial；配套本地假 artifact server。
- Task 4：7z 成员预检（traversal/设备名/大小写冲突/符号链接/膨胀炸弹）与
  提取后树审计（`archives.py`）；`RuntimeInstaller` 以版本+manifest 摘要
  目录原子发布、receipt+selection 记录、损坏运行时显式重建、中断恢复
  清理 staging。
- Task 5：`ProfileInstaller` 按哈希安装模型与自定义节点（ZIP 根/成员安全
  校验）、相同哈希跨 profile 复用不重复下载、`extra_model_paths.yaml`
  确定性生成；`model_profiles/` 提供与 profile 实现无关的通用 registry，
  第二 fake profile 无需改安装器。
- Task 6：`process.py` 使用 Windows 进程创建时间+可执行路径身份、隐藏窗口、
  端口占用预检、日志轮转、仅终止仍属自己的子进程；`manager.py` 做就绪
  校验（`/system_stats` 版本 + `/object_info` 必需节点 + 稳定期）与
  原子 process 记录、stale 恢复、单飞 start。
- Task 7：`client.py` 通用 HTTP/WebSocket 传输（上传安全名/大小、深拷贝
  提交、prompt 过滤的 WS 进度、history 一致性、仅取声明输出、interrupt），
  不导入 jobs/projects/processing/sdxl；假 ComfyUI 服务覆盖队列拒绝、
  执行错误、断线、超时与畸形响应。
- 各任务聚焦门禁 23/19/26/12/16/18 全部通过；全量后端回归
  **812/812 通过（`-W error` 零警告）**；`git diff --check` 通过。

Phase 4 Task 8–9 已于 2026-08-02 完成（提交 `946221a`、`277f476`）：

- Task 8：两个固定 API workflow 已落盘
  （`workflows/sdxl-mapchip-ipadapter-v1/text2img.api.json` 与
  `img2img.api.json`）并写入 manifest SHA-256（text2img
  `ca5b61fe…`、img2img `44c38979…`）；`model_profiles/workflows.py`
  提供 `GenericWorkflowInputs`、`WorkflowBinding`（深拷贝、模板校验、
  服务器必需节点校验、tracked digest 校验）与
  `build_model_profile_binding`；`sdxl.py` 只含 SDXL 数值节点 ID 与
  语义槽编译（text2img 拒绝结构参考、img2img 必须一张结构参考、
  style refs 走 average 组合、advanced 白名单 denoise/style_weight/
  lora_weight）。任务模型新增 schema-2 `ModelProfileBinding`：
  schema-1 请求保持字节不变并暴露 `model_profile=None`、
  `execution_eligibility=legacy_unbound`；新任务 API 必须携带
  `profile_id`，按结构参考是否存在解析 text2img/img2img 绑定，
  未知 profile/能力不匹配/digest 未锁定在创建任务目录前失败。
- Task 9：新增 `/api/system/inference/*` 设置面（状态、安装计划、
  安装操作 202/详情/取消、受管进程启停、有界日志 tail）；确认后由受控
  后台线程执行下载、解压、模型安装并持久化状态，启动时把中断操作标记为
  `INSTALL_INTERRUPTED`；前端新增 `InferenceSetup` 面板
  （默认折叠、展开才轮询，AbortController 清理，逐组件许可确认、
  GB/GiB 精确总量、启停/取消/日志控件，无生成按钮），App 集成后
  项目导入/历史流程不受影响。
- 门禁：Task 8 聚焦 147/147，Task 9 后端 10/10、前端 132/132（7 个
  测试文件）与 Vite 20 模块生产构建通过；全量后端回归
  **859/859 通过（`-W error` 零警告）**；`git diff --check` 通过。
- manifest 规范摘要已更新：runtime `89f05bd7…70ee86`（不变），
  profile `4c50a99e…01a381`（workflow digest 锁定后变化）。

Phase 4 Task 10 已于 2026-08-02 完成（提交 `ed31d7b`）：真实便携安装 +
GPU 双冒烟全部通过，profile 已提升为 `verified`。

- 实测修正：官方 7z 根目录为 `ComfyUI_windows_portable`（manifest 已更新）；
  官方归档使用 BCJ2 压缩，py7zr 无法解压，清单预检仍用 py7zr、解压改用
  Windows 内置 bsdtar（`tar.exe`，固定参数无 shell，先通过全部安全预检）；
  自定义节点装入受管运行时内部 `ComfyUI/custom_nodes/`；子进程 CWD 为
  解压根目录、启动参数去掉可执行文件重复项、就绪轮询容忍启动期连接失败。
- 实测节点契约：IPAdapter preset 用 `STANDARD (medium strength)`
  （对应 `ip-adapter_sdxl_vit-h.safetensors`），`CLIPTextEncode` 接
  LoraLoader 的 CLIP 输出，`IPAdapterAdvanced.image` 只接受单个节点链接
  （多图用 LoadImage + ImageBatch 链），`CLIPVisionEncode` 需要
  `crop=center`，img2img 结构参考先经 `ImageScale` 放大到 1024。
- 真实冒烟（RTX 5080 Laptop，16 GB VRAM，驱动 610.88）：text2img
  completed 11.1 秒、img2img completed 6.0 秒，输出均为 1024×1024；
  两次 text2img 输出 SHA-256 一致（确定性）；受管进程 stop→start→stop
  重启审计通过。证据：`docs/evidence/phase-4/evidence.json`（已脱敏）；
  文档：`docs/MODEL_PROFILES.md`。
- 新摘要：runtime `5d5fe88a…4e10`；profile 摘要以 manifest canonical
  SHA-256 为准，并随本轮限制文字修订更新。
- 门禁：Task 10 原始全量后端 865/865（`-W error` 零警告），前端 132/132 +
  20 模块构建通过；本轮收尾后全量后端 **875/875**（86% 覆盖率）和前端
  **134/134** + 20 模块构建通过，`pip check`、`git diff --check` 通过。
- 本轮重新执行受管已安装配置的真实 GPU 冒烟：text2img 11.8 秒、img2img
  5.9 秒，均 completed、1024×1024；profile 摘要为
  `8835cdb7…e58af3`，证据已同步到 `docs/evidence/phase-4/evidence.json`。

本地忽略的 `runtime/` 中已存在与 manifest 锁完全一致的受管运行时、模型和
安装记录；运行时发布成功后归档按设计删除，安装计划会把已验证组件显示为
`ready`，无需重新下载。真实 text2img/img2img 冒烟证据已脱敏提交到
`docs/evidence/phase-4/evidence.json`。

已确认的 Phase 4 决策：

- v0.1 首个模型配置仍为 SDXL Base 1.0 + mapchipLora + IP-Adapter SDXL
  ViT-H；以后 FLUX.2 Klein 4B 通过新增版本化配置接入，不原地替换 SDXL。
- ComfyUI 使用官方 Windows NVIDIA portable 包，项目下载并校验固定版本和
  SHA-256；其内置 Python/PyTorch 与后端 `.venv`、全局 Python、Conda 和用户
  既有 ComfyUI 隔离。
- 首个 runtime 锁定为 ComfyUI `v0.29.2`、commit
  `322122449c9d2ba8b8df1bb517364527dd0615f1`、官方 NVIDIA archive SHA-256
  `e7a39a817002d85b4fb2d4f6bd176c10d104a0d04031f99b9d8b7b1fd920c6fc`。
- 上述 runtime/profile 已完成真实下载复算、安装记录、text2img/img2img GPU
  冒烟和重启审计；profile manifest 当前标记为 `verified`。
- 安装仍要求用户在 WebUI 中逐组件确认许可；已安装且哈希匹配的组件不会重复下载。

权威入口：

- Phase 4 设计：
  [`docs/superpowers/specs/2026-08-01-phase-4-managed-comfyui-and-model-profiles-design.md`](docs/superpowers/specs/2026-08-01-phase-4-managed-comfyui-and-model-profiles-design.md)
- Phase 4 计划：
  [`docs/superpowers/plans/2026-08-01-phase-4-managed-comfyui-and-model-profiles.md`](docs/superpowers/plans/2026-08-01-phase-4-managed-comfyui-and-model-profiles.md)
- 架构决定：
  [`docs/adr/0002-managed-comfyui-runtime-and-versioned-model-profiles.md`](docs/adr/0002-managed-comfyui-runtime-and-versioned-model-profiles.md)

## 已验证的最终 Phase 3 门禁

Phase 3 在合并前于 2026-08-01 实际运行：

```powershell
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\tools\Generate-SyntheticPack.ps1
.\.venv\Scripts\python -W error -m pytest backend\tests\tools\test_synthetic_pack_generator.py -vv
.\.venv\Scripts\python -W error -m pytest backend\tests --cov=aimctexturegen --cov-report=term-missing
Push-Location frontend
try {
    ..\runtime\node-v24.18.0-win-x64\npm.cmd test
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    ..\runtime\node-v24.18.0-win-x64\npm.cmd run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
.\.venv\Scripts\python -W error -m pytest backend\tests\integration\test_restart_recovery.py -vv
git diff --check
git status --short
```

- 合成包生成器：独立测试 1/1 通过；默认生成
  `.generated\phase-3-synthetic-pack.zip`，SHA-256 为
  `8ec378c876fe12b17e784c2d03ee59e7ea8a6c1601d7bf00e0a36980e2d24478`，
  分类为资源格式 34、1 covered、1 missing、0 unknown。ZIP 只含仓库自有的
  `pack.mcmeta` 与纯合成 2×2 RGB PNG，不含 Mojang/Microsoft 资产。
- 后端：Python 3.12.10、pytest 9.1.1，616/616 通过，3483 语句、392 未覆盖、总覆盖率 89%。
- 前端：Vitest 4.1.10，6 个测试文件、113/113 通过；TypeScript 与
  Vite 8.1.5 生产构建通过（19 个模块）。
- 独立重启审计：1/1 通过；它在真实临时导入项目中删除 `index.sqlite3`、写入严格 schema-1 清单并启动第二个 repository/store/index/recovery 服务图，确认迁移回 schema 2、queued/active/completed 任务可见、active 变为 `JOB_INTERRUPTED`，以及 `source/`/`pack/` 的完整路径—SHA-256 映射完全相等。
- `git diff --check` 通过，最终 tracked 工作树干净；`.generated/`、
  `.superpowers/`、覆盖率与构建缓存保持忽略且未提交。

用户于 2026-08-01 确认：合成 Java 资源包导入、FastAPI 创建 queued Deepslate 任务、FastAPI/Vite 重启后的“已有项目”恢复均成功；格式 34、1 个 covered、1 个 missing、queued 行和 4 个 pending 候选仍可见。正常桌面及 400、600、900 px 窗口均无横向溢出或控件裁切；最终复验也确认桌面标题不再孤字换行、应用声明的 favicon 不再产生应用来源的 404。此前名为 `1` 的项目是早期失败命令留下的独立有效项目，不是成功流程造成的重复导入。Phase 3 验收范围内未发现未解决缺陷；移动端、真实 GPU/模型/ComfyUI、生产目录、候选采用与导出均未验证也未实现。

最终评审新增的重试顺序、错误恢复、路径别名、时间戳与 SVG 语义均由确定性
自动化覆盖，不需要重复 UI 密集型人工测试。今后从干净 checkout 复现人工恢复
门禁时，先运行受跟踪的合成包生成器，不再依赖 `.superpowers/` 中的本地工件。

## 下一入口

Phase 4 剩余两项需要用户在场：

1. **Task 11 Step 3 手工 WebUI 验收**（详见 ONBOARDING 下方/Task 11
   计划）：安装计划与许可确认、刷新不触发下载、已装 profile 直接 ready、
   启停/端口占用/400-900px/控制台/既有项目恢复。
2. **Task 11 Step 6 分支收口**：合并到 `master`/推送必须由用户明确授权；
   当前 `codex/phase-4-managed-comfyui` 未合并未推送。

固定 Node 运行时 `runtime/node-v24.18.0-win-x64` 仍缺失，前端门禁暂用
全局 Node v24.13.0 复现；恢复固定运行时后再更新 `docs/TESTING.md` 的
命令路径。

当前工作树包含本轮收尾代码/文档变更和用户已有的未跟踪 `temp/`；`temp/`
不属于项目变更，必须保留。接手按 `AGENTS.md` 的必读顺序阅读，以当前代码和
可重复验证结果为准。Phase 4 不做生产目录、候选采用、导出、移动端或
Java/Bedrock 转换。
