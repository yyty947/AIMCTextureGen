# AIMCTextureGen 当前交接

最后核对日期：2026-08-01

## 当前状态

Phase 3 已通过合并提交 `ae424b1` 进入 `master`/`origin/master`。当前 checkout
位于新阶段分支 `codex/phase-4-managed-comfyui`。Phase 4 的设计和实施计划已
落盘，但业务实现尚未开始，所有任务复选框都应保持未完成。

已确认的 Phase 4 决策：

- v0.1 首个模型配置仍为 SDXL Base 1.0 + mapchipLora + IP-Adapter SDXL
  ViT-H；以后 FLUX.2 Klein 4B 通过新增版本化配置接入，不原地替换 SDXL。
- ComfyUI 使用官方 Windows NVIDIA portable 包，项目下载并校验固定版本和
  SHA-256；其内置 Python/PyTorch 与后端 `.venv`、全局 Python、Conda 和用户
  既有 ComfyUI 隔离。
- 首个 runtime 候选为 ComfyUI `v0.29.2`、commit
  `322122449c9d2ba8b8df1bb517364527dd0615f1`、官方 NVIDIA archive SHA-256
  `e7a39a817002d85b4fb2d4f6bd176c10d104a0d04031f99b9d8b7b1fd920c6fc`。
- 上述版本只是候选锁定值；只有真实下载复算哈希并完成 text2img、img2img
  两个 GPU 冒烟后，才能标记为受支持配置。
- 架构确认不等于同意立即下载约 13.18 GB 运行时/模型。真实下载前还必须通过
  实现后的安装界面展示全部来源、许可、大小和磁盘需求，并再次取得明确确认。

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

从 Phase 4 计划 Task 1 开始，按测试先行逐任务执行。Task 1 先实现严格 manifests
并独立复核自定义节点 commit archive 已记录的大小/SHA-256；不得提前下载多 GB
runtime/model，不得跳到真实 GPU 冒烟。

当前工作树预计只有本轮文档变更和用户已有的未跟踪 `temp/`；`temp/` 不属于
项目变更，必须保留。接手按 `AGENTS.md` 的必读顺序阅读，以当前代码和可重复
验证结果为准。Phase 4 不做生产目录、候选采用、导出、移动端或 Java/Bedrock
转换。
