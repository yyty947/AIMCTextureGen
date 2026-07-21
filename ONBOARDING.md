# AIMCTextureGen 当前交接

最后核对日期：2026-07-21

## 当前状态

- 当前实现位于 `codex/phase-1-foundation-import` 工作树分支。
- MVP 产品设计已在提交 `026dec8` 中确认。
- 已完成第一阶段任务 1：FastAPI 应用工厂、`GET /api/health` 健康契约、固定后端依赖清单和健康契约测试已实现。
- 已完成第一阶段任务 2：严格的目录 Pydantic 契约、明确标记为 `development_fixture` 的格式 34 开发目录，以及确定性的主 `pack_format` 选择已实现。
- 已完成第一阶段任务 3：ZIP 与目录资源包的只读检查、严格不可变结果契约、安全路径和大小写/拓扑冲突校验、单一根目录识别、`pack.mcmeta` 主格式解析及不支持压缩方式的稳定错误映射已实现；尚未进行工作区提取。
- 已完成第一阶段任务 4：ZIP 与目录导入会先完成检查和目录配置解析，再通过 `<project-id>.tmp` 建立不可变 ZIP 快照、工作副本和经重新验证的 `project.json`，最后原子重命名；工作副本写入期间逐级持有不共享删除权限的 Windows 原生目录句柄并核对 file ID、最终路径和 reparse 属性，快照在发布前重新核对身份和 SHA-256，失败只清理身份未变化且不含 junction/symlink/reparse point 的本次临时目录。
- 当前没有可运行的 WebUI 或 ComfyUI 集成；FastAPI 应用可由 `aimctexturegen.main:create_app` 创建。
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

当前应执行 [第一阶段：基础设施与 Java 导入](docs/superpowers/plans/2026-07-21-phase-1-foundation-and-import.md)。这一阶段结束时应得到一个不依赖 GPU 的垂直切片：用户可导入安全的 Java 资源包，在 WebUI 中看到资源格式和基于测试目录配置计算的覆盖状态。

第一阶段之外的工作顺序见 [MVP 实施路线](docs/superpowers/plans/2026-07-21-aimc-texturegen-mvp-roadmap.md)。不要提前接入真实模型或构建完整生产目录。

## 接手步骤

1. 运行 `git status --short`，确认并保留当前未提交改动。
2. 阅读 `AGENTS.md`、当前阶段计划和 MVP 设计规格。
3. 找到当前阶段计划中第一个未勾选任务（目前为任务 5：Coverage Classification），只执行该任务定义的范围。
4. 先运行该任务的基线测试，再按计划测试先行实现。
5. 完成任务后更新计划复选框、本文件的当前状态和验证结果。

## 当前可用验证

当前可运行后端验证：

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\test_health.py -v
.\.venv\Scripts\python -W error -m pytest backend\tests\catalog\test_registry.py -v
.\.venv\Scripts\python -W error -m pytest backend\tests\packs -v
.\.venv\Scripts\python -W error -m pytest backend\tests\projects -v
.\.venv\Scripts\python -W error -m pytest backend\tests -v
git diff --check
git status --short
```

已于 2026-07-21 使用 Python 3.12.10 运行：项目工作区套件为 18 passed，完整后端套件为 76 passed；所有 pytest 命令均使用 `-W error` 且无警告。预期：`git diff --check` 无输出；`git status --short` 只显示当前有意创建或修改的文件。

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

