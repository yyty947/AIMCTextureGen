# AIMCTextureGen 当前交接

最后核对日期：2026-07-21

## 当前状态

- 当前实现位于 `codex/phase-1-foundation-import` 工作树分支。
- MVP 产品设计已在提交 `026dec8` 中确认。
- 已完成第一阶段任务 1：FastAPI 应用工厂、`GET /api/health` 健康契约、固定后端依赖清单和健康契约测试已实现。
- 已完成第一阶段任务 2：严格的目录 Pydantic 契约、明确标记为 `development_fixture` 的格式 34 开发目录，以及确定性的主 `pack_format` 选择已实现。
- 已完成第一阶段任务 3：ZIP 与目录资源包的只读检查、严格不可变结果契约、安全路径和大小写/拓扑冲突校验、单一根目录识别、`pack.mcmeta` 主格式解析及不支持压缩方式的稳定错误映射已实现；尚未进行工作区提取。
- 已完成第一阶段任务 4：ZIP 与目录导入会先完成检查和目录配置解析，再通过 `<project-id>.tmp` 建立不可变 ZIP 快照、工作副本和经重新验证的 `project.json`，最后原子重命名；工作副本写入期间逐级持有不共享删除权限的 Windows 原生目录句柄并核对 file ID、最终路径和 reparse 属性，快照在发布前重新核对身份和 SHA-256，失败只清理身份未变化且不含 junction/symlink/reparse point 的本次临时目录。
- 已完成第一阶段任务 5：覆盖分类以精确、保留大小写的相对路径比对工作副本；标准目录 PNG 必须可解码，损坏文件会产生显式验证错误；未知候选仅包含 `assets/*/textures/` 下可解码的方形 PNG，目录 junction/symlink 不会被遍历。
- 已完成第一阶段任务 6：FastAPI 提供仅接受 multipart ZIP 上传的项目导入、规范 UUID 项目清单和实时覆盖端点；纯 ASGI 中间件在 multipart 解析前限制整个请求体，解析后继续限制文件字节数，并将真实客户端断开转换到 multipart 的确定性 spool 关闭路径和稳定错误信封，上传使用项目根内的有界临时文件并在成功或失败后精确清理；覆盖分类全过程持有项目根、项目、`pack/` 和清单文件的原生句柄，已知错误统一映射为稳定错误信封，意外异常只在日志保留技术详情；应用工厂支持显式注入服务和测试用大小限制。
- 已完成第一阶段任务 7：React WebUI 提供单列 ZIP 导入和覆盖摘要，使用类型化 FastAPI 客户端，显示资源格式、开发目录警告、已覆盖/未覆盖计数、缺失可生成条目与未知文件计数；稳定 API 错误、网络错误、非 JSON 响应和形状错误的成功 JSON 均显示可读警报，新导入开始时会清除旧摘要。若项目导入成功但覆盖请求失败，界面会保留已创建项目并只重试覆盖分析，避免重复导入。文件选择会按不区分大小写的 `.zip` 后缀校验，并通过输入关联的文字说明错误。原生表单控件具有标签、键盘焦点、忙碌/禁用状态和移动端无横向滚动样式。
- 已完成第一阶段任务 8 的自动化证据：合成 ZIP 的完整 API 导入流会核对原输入、持久化哈希和不可变快照 SHA-256，重新打开快照 ZIP、`project.json` 与工作副本，并精确验证格式 34 开发目录的一项已覆盖和一项缺失。应用导入和 API 流程运行在 `-B` 独立子进程中；子进程在导入 `aimctexturegen.main` 前安装 audit hook，只允许项目根内写入，阻断外部网络与子进程事件，并核对 ComfyUI、CUDA、NVIDIA、Torch、Diffusers、Transformers 和 Hugging Face 运行时模块均未加载。真实浏览器 smoke 由控制器执行，尚未声称通过。
- 前端固定 Node.js 24.18.0；首次 lockfile 和验证使用经 Node.js 官方 `SHASUMS256.txt` 校验的便携 Windows x64 ZIP，SHA-256 为 `0ae68406b42d7725661da979b1403ec9926da205c6770827f33aac9d8f26e821`。当前没有 ComfyUI 集成；FastAPI 应用可由 `aimctexturegen.main:create_app` 创建，运行时默认项目根和目录根从仓库位置解析，不依赖当前 PowerShell 目录。
- 当前没有需要迁移的用户项目数据。

## 已确认边界

- v0.1 是 Windows 本地 Java 版普通方块单张生成闭环。
- 导入资源包只读，应用在独立项目目录中保存快照和工作副本。
- 目录只包含“什么标准路径应存在什么材质”的元数据，不包含具体原版贴图。
- 不从 JAR 提取材质，不扫描用户硬盘定位 Minecraft。
- 缺失材质默认不要求结构参考；用户可选上传一张，提供时走 img2img，否则走 text2img。
- 风格参考从导入包内手动选择 1–8 张，通过 IP-Adapter 输入。
- 每个任务固定四候选；用户选择逐张、两张或四张并行。
- OOM 时显示易懂说明和建议，不自动降低并行数或其他参数。
- 默认路线是 React + TypeScript + Vite、FastAPI 和应用托管的 ComfyUI；启动脚本打开浏览器，桌面壳后置。

## 当前工作入口

当前应完成 [第一阶段：基础设施与 Java 导入](docs/superpowers/plans/2026-07-21-phase-1-foundation-and-import.md) 中尚未勾选的真实浏览器 smoke：使用合成 ZIP 验证开发目录警告、格式 34、一项已覆盖、一项缺失和缺失的 Deepslate 路径。自动化门禁已经通过，但在控制器记录该人工证据前，不得宣称第一阶段退出门禁全部完成。

浏览器 smoke 通过后，下一个实施工作是路线中的第二阶段“确定性材质处理”；先为第二阶段编写并确认可执行计划。不要提前接入真实模型或构建完整生产目录。

## 接手步骤

1. 运行 `git status --short`，确认并保留当前未提交改动。
2. 阅读 `AGENTS.md`、当前阶段计划和 MVP 设计规格。
3. 执行任务 8 中尚未勾选的真实浏览器 smoke，并记录观察结果；不要重复或扩展已经通过的自动化实现。
4. smoke 通过后更新计划复选框和本文件，再为第二阶段编写可执行计划。
5. 第二阶段开始前重新运行当前自动化门禁，保留开发目录非生产的明确标记。

## 当前可用验证

当前可运行后端验证：

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\test_health.py -v
.\.venv\Scripts\python -W error -m pytest backend\tests\catalog\test_registry.py -v
.\.venv\Scripts\python -W error -m pytest backend\tests\packs\test_coverage.py -v
.\.venv\Scripts\python -W error -m pytest backend\tests\packs -v
.\.venv\Scripts\python -W error -m pytest backend\tests\projects -v
.\.venv\Scripts\python -W error -m pytest backend\tests\api -v
.\.venv\Scripts\python -W error -m pytest backend\tests --cov=aimctexturegen --cov-report=term-missing
Push-Location frontend
..\runtime\node-v24.18.0-win-x64\npm.cmd test
..\runtime\node-v24.18.0-win-x64\npm.cmd run build
Pop-Location
git diff --check
git status --short
```

已于 2026-07-21 使用 Python 3.12.10 运行完整覆盖率门禁：120 passed、总覆盖率 86%，使用 `-W error` 且无警告。使用便携 Node.js 24.18.0 运行：前端为 1 个测试文件、11 个测试通过，Vite 8.1.5 生产构建成功。`git diff --check` 无输出；`git status --short` 只应显示当前有意创建或修改的文件。任务 8 的实现提交为 `4d950ab`（`test: verify Phase 1 import flow`），当前已提交的自动化交接 HEAD 为 `0c4a32f`（`docs: finalize Phase 1 automated handoff`）。

## 需要在对应阶段确定的事项

以下内容尚未形成运行时事实，应在相应阶段通过调研、固定版本和测试后写入专门文档，不应由接手者凭记忆猜测：

- 后端 Python、前端 Node 和包管理器的最终固定版本；
- 首批生产 Java `pack_format` 配置及目录元数据来源；
- ComfyUI、IP-Adapter 节点和 workflow 的兼容 commit；
- 模型文件名、来源、许可证、大小和 SHA-256；
- 16、32、64 三档的 LoRA 触发词、权重和实测参数；
- 8 GB NVIDIA 环境下并行度 1、2、4 的实测显存和耗时。

## 交接更新规则

每次里程碑结束时，将本文件更新为：已完成内容、当前真实测试命令与结果、下一个未完成任务、已知阻塞和相关文件。删除已经失效的临时说明，不在这里累积历史日志；历史由 Git、计划复选框和后续 CHANGELOG 承担。

