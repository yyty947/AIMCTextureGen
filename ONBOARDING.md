# AIMCTextureGen 当前交接

最后核对日期：2026-07-27

## 当前状态

- Phase 1 实现已通过合并提交 `3f6c352` 合入 `master`，阶段分支与 worktree 已删除；MIT LICENSE 由 `3eb153e`（GitHub 网页端提交）加入。
- MVP 产品设计已在提交 `026dec8` 中确认。
- 已完成第一阶段任务 1：FastAPI 应用工厂、`GET /api/health` 健康契约、固定后端依赖清单和健康契约测试已实现。
- 已完成第一阶段任务 2：严格且禁止数值/布尔强制转换的目录 Pydantic 契约、规范且唯一的 `semantic_id`/`relative_path`、明确标记为 `development_fixture` 的格式 34 开发目录，以及确定性的主 `pack_format` 选择已实现。
- 已完成第一阶段任务 3：ZIP 与目录资源包的只读检查、严格不可变结果契约、安全路径和大小写/拓扑冲突校验、单一根目录识别、`pack.mcmeta` 主格式解析及稳定错误映射已实现。ZIP 在读取前限制 4096 个成员、1 MiB `pack.mcmeta`、256 MiB 单成员、1 GiB 总展开量和 200:1 压缩率，并拒绝加密成员与非 stored/deflate 压缩。
- 已完成第一阶段任务 4：ZIP 与目录导入会先完成检查和目录配置解析，再通过 `<project-id>.tmp` 建立不可变 ZIP 快照、工作副本和经重新验证的 `project.json`，最后原子重命名；工作副本写入期间逐级持有不共享删除权限的 Windows 原生目录句柄并核对 file ID、最终路径和 reparse 属性，逐块复制还会核对实际单成员/总字节数并把 CRC、截断、加密和压缩错误转换为稳定资源包错误。项目名称上限为 128 个 Unicode code point，清单发布前确认序列化大小低于 1 MiB 读取边界。快照在发布前重新核对身份和 SHA-256，失败只清理身份未变化且不含 junction/symlink/reparse point 的本次临时目录。
- 已完成第一阶段任务 5：覆盖分类以精确、保留大小写的相对路径比对工作副本；标准目录 PNG 必须可解码，损坏文件会产生显式验证错误；未知候选仅包含 `assets/*/textures/` 下可解码的方形 PNG，目录 junction/symlink 不会被遍历。
- 已完成第一阶段任务 6：FastAPI 提供仅接受 multipart ZIP 上传的项目导入、规范 UUID 项目清单和实时覆盖端点；纯 ASGI 中间件先限制整个请求体，专用增量 multipart 解析器再从 `Request.stream()` 把 ZIP 直接写入项目根内的身份受检临时文件，不创建 Starlette 或系统临时目录 spool，并继续精确限制文件字节数、项目名长度、boundary、单字段、单值、单 part 头总量、头数量及重复/未知字段。按字节碎片输入、pack-first 顺序和缺失/重复/未知字段均有协议测试；目标文件写入失败稳定映射为 `PROJECT_STORAGE_UNAVAILABLE` 500 并精确清理，而畸形请求仍为 400。覆盖分类全过程持有项目根、项目、`pack/` 和清单文件的原生句柄，已知错误统一映射为稳定错误信封，意外异常只在日志保留技术详情；应用工厂支持显式注入服务和测试用大小限制。
- 已完成第一阶段任务 7：React WebUI 提供单列 ZIP 导入和覆盖摘要，使用类型化 FastAPI 客户端，显示资源格式、开发目录警告、已覆盖/未覆盖计数、缺失可生成条目与未知文件计数；成功响应会验证规范 UUID、64 位十六进制 SHA-256、带 `Z` 或显式偏移的 RFC 3339 时间戳和非负整数格式/计数，项目名称与后端一致按 Unicode code point 限制为 128。稳定 API 错误、网络错误、非 JSON 响应和形状错误的成功 JSON 均显示可读警报，新导入开始时会清除旧摘要。若项目导入成功但覆盖请求失败，界面会保留已创建项目并只重试覆盖分析，避免重复导入。文件选择会按不区分大小写的 `.zip` 后缀校验，并通过输入关联的文字说明错误。
- 已完成第一阶段任务 8：合成 ZIP 的完整 API 导入流会核对原输入、持久化哈希和不可变快照 SHA-256，重新打开快照 ZIP、`project.json` 与工作副本，并精确验证格式 34 开发目录的一项已覆盖和一项缺失。审计负载超过 1 MiB；应用导入和 API 流程运行在 `-B` 独立子进程中，audit hook 只允许项目根内写入并阻断外部网络与子进程事件，因此当前成功结果同时证明大上传未写入外部 spool。2026-07-22 用户已在正常 Windows 桌面窗口和 400–900 px 宽的窄桌面窗口完成当前 multipart 实现的手工复验，页面显示和导入结果均正确。375 px 不再是产品验收条件，v0.1 不声明移动端支持。
- 前端固定 Node.js 24.18.0；首次 lockfile 和验证使用经 Node.js 官方 `SHASUMS256.txt` 校验的便携 Windows x64 ZIP，SHA-256 为 `0ae68406b42d7725661da979b1403ec9926da205c6770827f33aac9d8f26e821`。当前没有 ComfyUI 集成；FastAPI 应用可由 `aimctexturegen.main:create_app` 创建，运行时默认项目根和目录根从仓库位置解析，不依赖当前 PowerShell 目录。
- 当前没有需要迁移的用户项目数据；本地 `projects/` 下可能存在 2026-07-22 手工验收残留项目（已被 `.gitignore` 忽略，可安全删除）。
- 第二阶段“确定性材质处理”已通过合并提交 `4f5ba49` 合入 `master`，新增 `backend/src/aimctexturegen/processing/`：`models.py`（`ProcessingReport` 报告契约，`schema_version` 1、`ALGORITHM_VERSION` 1、`SCORE_DECIMALS` 6、确定性 `dump_report_json`）、`errors.py`（`ProcessingError`）、`validation.py`（仅接受 RGB 或完全不透明 RGBA、正方形且边长可被 16/32/64 整除的画布）、`grid_snap.py`（逐格逐通道下位中位数网格吸附）、`seam.py`（归一化环绕缝隙分数）、`previews.py`（512px 最近邻预览与 1536px 3x3 平铺预览）、`palette.py`（确定性 median-cut 调色板限制）、`pipeline.py`（`process_candidate` 编排，原子 temp + `os.replace` 写入）；对应测试位于 `backend/tests/processing/`，含子进程隔离门禁（验证导入图中无 fastapi/torch/comfy/numpy）。实现期间两处计划偏差已同步进 `docs/superpowers/plans/2026-07-26-phase-2-deterministic-texture-processing.md`：Pillow 12.3 弃用 `Image.getdata()`，全部调用点改用 `get_flattened_data()`（提交 `3d53e97`）；新增的 `backend/tests/processing/test_models.py` 与既有 `backend/tests/packs/test_models.py` 同名，导致合并后 pytest 单次收集 `backend\tests` 时报 `import file mismatch`，已重命名为 `test_report_models.py`（提交 `672c666`）。
- 第二阶段计划中的历史完成项已全部勾选，并补充合并门禁与五项延后清理说明，不再与本文件的完成状态冲突。
- 第三阶段 Task 1 已由提交 `a89c924` 完成：补齐 Phase 2 的分辨率/文件名预检、失败临时文件清理、RGBA 端到端覆盖和处理模块文档。
- 第三阶段 Task 2 已由提交 `d4ee5cf` 实现并完成第一轮评审加固：项目清单严格区分 schema 1/2，schema 1 可保持原字段和双时间戳迁移到 schema 2；新导入直接写入默认分辨率 16、并行度 1、空风格参考的 schema 2。共享项目相对路径语法现在同时拒绝 Windows 非法字符、控制字符、尾随点/空格和设备名。清单原子替换在 Windows 上从写入、`fsync`、回读验证到发布始终绑定同一不共享删除权限的文件句柄，再通过 `SetFileInformationByHandle` 发布，验证回调无法把同名未验证文件换入。
- 第三阶段 Task 3 已完成并通过第二轮评审加固：`ProjectRepository` 在持有项目根和项目目录身份期间安全读取有界清单，并只原子迁移 schema 1 的 `project.json`；进程锁仅作优化，Windows 条件替换会以拒绝写共享的目标句柄覆盖比较与发布窗口，核对目标身份及原始字节后再以源句柄发布。即使新清单在验证后、发布前替换目标，也会保留新字节并返回 `PROJECT_MANIFEST_CONFLICT`。直接扫描只接受规范 UUID 目录，以 `updated_at DESC, project_id ASC` 返回有效项目，并把损坏或不安全的规范项目隔离成类型化问题。`ProjectService` 现负责导入、获取、列表和实时覆盖业务，以磁盘项目为权威并在索引写入失败后只尝试一次重建；项目 API 新增 `GET /api/projects`，其余路由不再直接打开 `project.json`、遍历 `pack/` 或执行目录配置匹配。导入已落盘但索引重建失败时，API 保留 `INDEX_UNAVAILABLE` 并明确提示从项目列表重新打开或重启重建，避免误导用户重复导入。

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

当前工作分支为 `codex/phase-3-durable-jobs`。第三阶段 Task 1 至 Task 3 已完成；下一项是 Task 4“任务契约与纯状态机”，负责严格持久化请求/状态模型、四候选一致性和无 I/O 状态转换。继续只执行 `docs/superpowers/plans/2026-07-27-phase-3-durable-jobs-and-recovery.md`，不接入真实模型、ComfyUI 或生产目录。

## 接手步骤

1. 运行 `git status --short`，确认并保留当前未提交改动。
2. 阅读 `AGENTS.md`、路线图、第二阶段计划和第三阶段可执行计划，了解已合并的 processing 契约以及即将建立的持久化接口。
3. 从 Task 4 开始继续逐项完成 RED → GREEN → commit；不要重复 Task 1–3 或提前执行后续任务。
4. 不要在第三阶段接入真实模型、ComfyUI 或生产目录，也不要采用候选或导出资源包。

## 当前可用验证

`master` 检出不自带 `.venv`。首次接手先用 Python 3.12 创建（系统默认 Python 版本更高时必须用 `py -3.12`）：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".\backend[dev]"
```

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

已于 2026-07-26 在合并后的 `master`（合并提交 `4f5ba49`，`.venv` Python 3.12.10）复跑门禁：后端 217 passed（第一阶段 165 项 + 第二阶段 `processing/` 52 项），使用 `-W error` 且无警告；前端 1 个测试文件 21 个测试通过，Vite 生产构建成功（本次已在合并结果上重新执行）。覆盖率基线记录自阶段分支提交 `672c666` 的带覆盖率门禁：总覆盖率 88%，`processing/` 各模块中 `errors.py`、`grid_snap.py`、`models.py`、`pipeline.py`、`previews.py`、`seam.py`、`validation.py` 均 100%，`palette.py` 97%（87 语句、3 未覆盖）。`git diff --check` 无输出。

2026-07-27 第三阶段 Task 3 第二轮评审加固后，`.\.venv\Scripts\python -W error -m pytest backend\tests\projects backend\tests\api -v` 为 120 passed；额外完整后端回归 `.\.venv\Scripts\python -W error -m pytest backend\tests` 为 306 passed。验证后、发布前并发替换的迁移竞态回归额外连续运行 20 次且全部通过。Task 2 移交的三个旧 schema-1 API 夹具已改为当前 schema-2 构造，完整后端门禁恢复通过。

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

