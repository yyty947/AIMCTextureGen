# AIMCTextureGen

AIMCTextureGen 是一个面向 Minecraft 资源包作者的本地 WebUI，用于管理材质缺失项、配置本地图像生成工作流、比较候选结果，并把采用的材质写入资源包工作副本。

## 当前状态

MVP 第一阶段的导入纵向切片已完成自动化验证：FastAPI 可以把合成 Java ZIP 导入隔离项目，保留不可变快照与工作副本，并用格式 34 的开发目录配置计算覆盖状态；React WebUI 可以上传 ZIP 并展示该结果。该目录仍是 `development_fixture`，不是生产兼容性声明。真实浏览器 smoke 仍是进入第二阶段前的最后一项人工门禁；一键启动与 GPU 生成流程尚未实现。

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

## 当前开发验证

在已准备好仓库 `.venv` 和 `runtime/node-v24.18.0-win-x64` 的工作树中，以下 PowerShell 命令已实际通过：

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests --cov=aimctexturegen --cov-report=term-missing
Push-Location frontend
..\runtime\node-v24.18.0-win-x64\npm.cmd test
..\runtime\node-v24.18.0-win-x64\npm.cmd run build
Pop-Location
git diff --check
```

当前结果为后端 120 个测试通过、总覆盖率 86%，前端 1 个测试文件中的 11 个测试通过，Vite 生产构建成功。`runtime/`、`.venv/`、`frontend/node_modules/` 和 `frontend/dist/` 都是本地忽略产物，不应提交。

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

