# AIMCTextureGen

AIMCTextureGen 是一个面向 Minecraft 资源包作者的本地 WebUI，用于管理材质缺失项、配置本地图像生成工作流、比较候选结果，并把采用的材质写入资源包工作副本。

## 当前状态

项目处于 MVP 第一阶段实施中。后端已提供可测试的 FastAPI 健康契约；完整启动流程将在后续阶段加入，因此当前仓库还没有可运行的启动命令。

首个 MVP 仅覆盖：

- Windows 与 NVIDIA CUDA；
- Java 版资源包；
- 普通、单贴图、非透明、非动画方块；
- 单个缺失材质的四候选生成、后处理、采用与 ZIP 导出；
- SDXL、mapchipLora、IP-Adapter 和可选 img2img 的托管 ComfyUI 工作流。

MVP 不做 Java/基岩版转换、跨版本转换、Minecraft JAR 扫描、原版素材提取、批量生成、物品、实体、模型、PBR 或桌面安装包。

## 文档导航

新加入项目或在无上下文对话中接手时，按以下顺序阅读：

1. [`ONBOARDING.md`](ONBOARDING.md)：当前进度、下一步和验证命令。
2. [`AGENTS.md`](AGENTS.md)：项目不变量、工作规则和完成标准。
3. [MVP 实施路线](docs/superpowers/plans/2026-07-21-aimc-texturegen-mvp-roadmap.md)：阶段顺序与验收门槛。
4. [第一阶段实施计划](docs/superpowers/plans/2026-07-21-phase-1-foundation-and-import.md)：当前可执行任务。
5. [MVP 设计规格](docs/superpowers/specs/2026-07-18-aimc-texturegen-mvp-design.md)：产品、架构与安全边界。

设计规格回答“做什么和为什么”；实施计划回答“按什么顺序修改哪些文件”；`ONBOARDING.md` 只记录当前事实，不替代前两者。

## 核心边界

- 原始资源包始终只读；所有采用操作只修改项目工作副本。
- 仓库只保存独立整理的材质路径元数据，不包含 Mojang/Microsoft 原始贴图或完整游戏资产。
- 应用不扫描硬盘寻找 Minecraft、游戏 JAR 或用户已有的 ComfyUI。
- Java 版和未来的基岩版分别使用原生适配器，不相互转换。
- GPU 显存不足时解释原因并建议用户降低并行数，不静默改变质量参数。
- ComfyUI、模型和节点使用显式固定版本；缺失内容只在用户确认后下载。

## 开发与许可状态

项目尚未选择并发布仓库许可证。在许可证文件加入前，不应假定仓库内容已授予再分发或衍生使用许可。模型、节点和第三方代码各自受其原始许可证约束，首次公开发布前必须完成第三方许可清单与复核。

AIMCTextureGen 是非官方项目，与 Mojang Studios 或 Microsoft 没有隶属、赞助或认可关系。“Minecraft”仅用于说明兼容对象。

