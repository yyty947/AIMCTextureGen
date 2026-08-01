# AIMCTextureGen 当前交接

最后核对日期：2026-08-01

## 当前状态

第三阶段“持久化任务与项目恢复”已在分支
`codex/phase-3-durable-jobs` 完成并具备评审证据。实现与加固提交为：

- Task 1：`a89c924`；Task 2：`d4ee5cf`、`2d32c78`、`9f06d6c`、`cd26604`；Task 3：`d7bad0a`、`100822e`。
- Task 4：`a0dbb50`、`c19a756`、`f3ab034`；Task 5：`cbf6649`、`57073ff`、`2be0c80`；Task 6：`6ec87d7`、`2b15e21`。
- Task 7：`85cbf75`、`8a0d903`；Task 8：`ac89bd0`、`9508b41`；Task 9：`9c16126`、`441cec3`、`051a032`、`21b050c`、`5244dad`。
- Task 10 的 UI/浏览器修复：`9f71adacdfb2d55c8381d80fab7366b102fb87dd`；重启审计加固：`6fb7beb22e1e6518310066d88517042486d51d89`。
- 最终评审修复波：`75b6806`、`00bb904`、`ad03538`、`f6d2f0e`、
  `a11302f`、`e02d229`、`d587cae`、`0f41b03`、`678fb3a`、
  `2358f61`、`efb719f`、`14496b1`、`a82c1c9`。

项目目录中的 schema-2 `project.json` 和任务 JSON 是权威数据；schema-1
项目会只原子替换 `project.json`，保留 `source/` 与 `pack/`。任务在创建时
持久化四个唯一 seed、请求与状态，SQLite 仅保存可从 JSON 重建的查询摘要。重启时
会重建删除的索引，将 `generating`/`postprocessing` 任务恢复为
`failed/JOB_INTERRUPTED`，并保留 queued/终态任务；审计逐路径比较 `source/` 和
`pack/` 的 SHA-256 映射，二者均不得改变。运行中外部手工/强制改写项目文件仍按
[`ADR-0001`](docs/adr/0001-running-project-mutation-boundary.md) 不受支持。

最终评审修复波进一步保证：索引扫描—发布与增量写入按项目根目录串行化，任务
revision 只能前进；坏任务 sibling 不再隐藏有效历史；活动任务恢复写失败会阻断
启动；SQLite 语义坏值只触发一次集中重建；前端项目列表与恢复报告只接受最新重试
结果。Windows `COM¹`—`COM³`/`LPT¹`—`LPT³` 别名、原子临时文件清理、
严格公历时间戳与 favicon ARIA 引用也已有回归覆盖。ADR-0001 的边界没有扩大，
没有引入 TxF 或敌对外部 rename 防御。

## 已验证的最终 Phase 3 门禁

在本 worktree 于 2026-08-01 实际运行：

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
- 后端：Python 3.12.10、pytest 9.1.1，614/614 通过，3483 语句、392 未覆盖、总覆盖率 89%。
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

下一阶段仅为 Phase 4“托管 ComfyUI 与模型配置”的规划入口：先固定 runtime、ComfyUI/节点 commit、模型来源/许可证/SHA-256、确认式下载、健康检查、假服务 CI 与受支持 NVIDIA 环境的人工配置验证，再开始实现。不要在本阶段接入 GPU、模型、ComfyUI、生产目录、候选采用、导出、移动端或 Java/Bedrock 转换。

接手先阅读 `AGENTS.md`、路线图、Phase 4 计划（建立后）和设计规格；以当前代码与可重复验证结果为准。
