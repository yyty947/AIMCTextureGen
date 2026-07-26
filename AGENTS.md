# AIMCTextureGen Agent Guide

本文件适用于整个仓库。任何自动化代理或无上下文开发者在修改文件前都必须完整阅读本文件。

## 必读顺序

1. `ONBOARDING.md`
2. 本文件
3. `docs/superpowers/plans/2026-07-21-aimc-texturegen-mvp-roadmap.md`
4. `ONBOARDING.md` 指向的当前阶段计划
5. `docs/superpowers/specs/2026-07-18-aimc-texturegen-mvp-design.md`
6. 与任务直接相关的源码、测试和 ADR

若文档与当前代码或实际验证结果冲突，以当前代码和可重复验证结果为准，并在同一次变更中修正文档。不要用旧计划覆盖已验证的现实。

## 产品不变量

- v0.1 只支持 Windows、NVIDIA CUDA 和 Java 版普通方块单张生成闭环。
- 原始导入包和 `source/` 快照不可修改。采用候选只写入项目的 `pack/` 工作副本。
- 不做 Java/基岩版转换或任意跨版本资源包转换。
- 不扫描 Minecraft 安装，不读取客户端 JAR，不在仓库中分发原版 PNG、模型 JSON 或完整游戏资产。
- 内置目录是独立维护的路径元数据。目录来源、格式版本和生成过程必须可追踪。
- `pack.pack_format` 选择主目录配置；`supported_formats` 只保存和展示兼容范围。
- WebUI 只调用 FastAPI；WebUI 不直接访问本地路径或 ComfyUI。
- FastAPI 是业务边界；ComfyUI 仅负责受控 GPU 推理。
- ComfyUI、节点、workflow 和模型必须使用显式版本、来源、许可证与 SHA-256，不允许静默更新。
- 四个候选的 seed 在任务创建时确定并持久化。并行度只允许 1、2、4，由用户选择。
- OOM 或其他失败不得自动修改用户参数、采用候选或写入工作副本。
- 后处理必须是确定性的，并能脱离 ComfyUI 单独测试。
- ZIP 导入防止 path traversal、绝对路径、设备路径和大小写冲突；写入与导出使用临时文件校验后原子替换。

对这些不变量的修改属于产品设计变更。先更新设计规格或新增 ADR，并取得用户确认，再修改实现。

## 工程规则

- 在 Windows PowerShell 环境工作；命令和文档示例必须能在 PowerShell 中执行。
- Python 使用仓库管理的隔离环境，不修改全局 Python、Miniconda 或用户已有 ComfyUI。
- 不提交 `.venv`、Node 依赖、ComfyUI、模型、生成产物、项目工作目录或用户资源包。
- 优先按职责拆分小文件。禁止让路由直接处理 ZIP、数据库或图像算法。
- 跨模块行为先定义类型和接口，再实现调用方。
- 新功能和缺陷修复按测试先行执行；测试应证明失败原因，并在最小实现后转为通过。
- 外部服务使用假 ComfyUI 或临时目录测试；普通 CI 不依赖 GPU 或真实模型。
- 不因为当前计划提到某个路径就假定文件已经存在；先检查当前工作树。
- 保留用户已有改动，不重置或覆盖无关文件。

## 模块边界

- `JavaPackAdapter`：解析 `pack.mcmeta`、检查包结构和扫描路径；不依赖 AI。覆盖状态由 `packs/coverage.py` 的 `classify_coverage` 计算，同样不依赖 AI。
- `CatalogRegistry`：加载和验证按资源格式维护的材质路径元数据；不读取原版图像。
- `ProjectWorkspace`：创建项目、保存只读快照、维护工作副本和可迁移 JSON；不负责生成推理。
- `TextureProcessor`：网格吸附、预览和 seam score；不依赖 FastAPI 或 ComfyUI。
- `GenerationService`：将持久化任务转换为固定 workflow 请求并管理候选状态。
- `ComfyUIManager`：安装、版本校验、健康检查、进程与日志；不包含产品业务规则。
- API 路由只做输入输出映射、调用服务和错误转换。

## 计划与文档规则

- 当前工作入口由 `ONBOARDING.md` 指定。不要同时执行多个阶段计划。
- 每完成一个可独立验证的任务，更新计划复选框和 `ONBOARDING.md` 的当前状态。
- 行为、接口、命令或目录发生变化时，同步更新相关文档。
- 架构决策只在代价高、影响多个模块或推翻既有结论时写 ADR；不要为普通实现细节创建 ADR。
- `README.md` 只放稳定入口和真实可用命令，不复制整份设计规格。
- `ONBOARDING.md` 必须短、具体且基于当前 checkout，不记录猜测。
- 测试命令实际存在后再加入 `docs/TESTING.md`；模型配置固定后再加入 `docs/MODEL_PROFILES.md`；生产目录来源确定后再加入 `docs/CATALOG.md`。

## 当前验证基线

在业务代码和工具链尚未加入时，文档变更至少运行：

```powershell
git diff --check
git status --short
```

实现开始后，以当前阶段计划和 `ONBOARDING.md` 中的命令为准。不得声称通过了未实际运行的测试。

## 完成与交接标准

任务只有在以下条件满足时才能标记完成：

1. 计划要求的行为已经实现，没有用注释或占位返回代替。
2. 相关自动化测试和适度的人工验证实际运行并记录结果。
3. 原始包只读、安全路径、确定性和错误恢复边界未被破坏。
4. `ONBOARDING.md` 已更新为真实的下一状态。
5. `git diff --check` 通过，未混入无关改动或本地生成文件。

