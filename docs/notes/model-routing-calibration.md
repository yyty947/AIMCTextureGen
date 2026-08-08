# Subagent Model Routing Calibration

> 这是模型路由的校准快照，不是产品不变量，也不是对未来模型、价格、额度或 benchmark 的承诺。需要在模型列表、价格、Codex 行为或 benchmark 版本变化时重新核对。

## 校准元数据

- 校准日期：2026-08-08
- 项目：AIMCTextureGen
- 当前运行环境的 Advisor 状态：`auto`，模型来源为 `codex debug models`，优先级为 `gpt-5.6-sol`，额外重试 1 次（最多 2 次）。这是运行时观察，不应写死为项目永久配置。
- 当前项目偏好：普通实现优先 `gpt-5.6-luna`，按任务选择 reasoning effort；Advisor 与普通子代理分开统计。

## 稳定路由规则

长期规则写在仓库根目录的 `AGENTS.md`。摘要如下：

1. 清晰、边界明确、可验证的实现优先 Luna；通常使用 `high`，大而清晰的任务使用 `xhigh`，`max` 仅用于有明确质量收益的长链路任务。
2. 跨模块状态一致性、并发、持久化、架构取舍和证据冲突优先 Sol；通常使用 `high`，只有高后果且仍有关键不确定性时使用 `xhigh`/`max`。
3. Terra 不预设固定职责，只有新校准或本地结果证明其有价值时采用。
4. 缺上下文先补上下文，范围过大先拆分，只有“方向正确但推理浅/遗漏边界”才提高同一模型的 effort；语义失败不能盲目重试。
5. Advisor 是低频、只读、独立的战略审阅层，不实现代码、不派生子代理、不替代控制器判断。
6. 普通子代理禁止 Ultra；Fast 需用户明确授权。

## 官方资料快照

以下页面是 2026-08-08 核对的官方来源，后续应以页面当前内容为准：

- [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)：Sol/Terra/Luna 的定位，以及从较低 effort 开始、以代表性任务验证更高 effort 的建议。
- [OpenAI model catalog](https://developers.openai.com/api/docs/models)：当前模型 ID、reasoning 支持和 API 价格页面。
- [Codex models](https://learn.chatgpt.com/docs/models)：Codex 的模型/effort 说明、Ultra 与子代理的关系。
- [Codex speed](https://learn.chatgpt.com/docs/agent-configuration/speed)：Standard/Fast 的速度和 credits 规则。
- [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)：并行子代理的适用场景、额外 token 消耗和写入冲突风险。

当前抓取到的官方模型页面显示的 API 价格为（输入/输出，每百万 token；价格可能随时变化）：

| 模型 | 输入 | 输出 | 说明 |
|---|---:|---:|---|
| `gpt-5.6-sol` | `$5` | `$30` | 最高能力档 |
| `gpt-5.6-terra` | `$2` | `$12` | 平衡档 |
| `gpt-5.6-luna` | `$0.20` | `$1.20` | 高吞吐/低成本档 |

API 价格不能直接换算 ChatGPT/Codex 订阅额度。Codex Work 与 ChatGPT 共享 credits；GPT-5.6 Fast 使用更高的 credits 倍率，因此本项目仍要求 Fast 必须有明确授权。

## Artificial Analysis Coding Agent Index v1.3

来源：[Coding Agent Index](https://artificialanalysis.ai/agents/coding-agents) 和 [Codex comparison](https://artificialanalysis.ai/agents/coding-agents/comparisons/claude-code-vs-codex)。该 index 是特定 agent、harness 和 benchmark 的快照；成本是 API token 成本代理，不是订阅价格；耗时是 agent 活跃墙钟时间，不含所有环境/验证开销。分数按其公开页面记录，不能当作本项目成功率。

以下表格按 `none → low → medium → high → xhigh → max` 列出当前 GPT-5.6 家族的 `score / cost-per-task / active-time / tokens-per-task`：

| 模型 | none | low | medium | high | xhigh | max |
|---|---|---|---|---|---|---|
| Sol | 43 / $1.40 / 3.4m / 3.4M | 54 / $1.72 / 3.7m / 3.2M | 61 / $2.99 / 5.2m / 5.8M | 64 / $4.14 / 6.3m / 8.1M | 65 / $5.24 / 7.4m / 9.9M | 67 / $7.08 / 10.2m / 13.2M |
| Terra | 24 / $0.30 / 1.8m / 1.1M | 37 / $0.39 / 2.8m / 1.5M | 48 / $0.72 / 4.3m / 3.1M | 56 / $1.27 / 6.2m / 5.5M | 57 / $1.52 / 6.9m / 6.5M | 62 / $2.21 / 8.4m / 9.5M |
| Luna | 20 / $0.07 / 2.5m / 3.6M | 25 / $0.04 / 1.9m / 1.5M | 42 / $0.09 / 3.4m / 4.4M | 51 / $0.19 / 5.7m / 9.5M | 55 / $0.25 / 6.6m / 12.3M | 59 / $0.31 / 8.0m / 15.5M |

解读限制：

- 不能把这个表解释为模型能力的永久排序；benchmark、模型版本、工具链和 harness 变化都会改变结果。
- 一般 Intelligence Index 不能替代 coding-agent 结果；应同时看任务类型、失败模式和本项目实测。
- `Luna max` 在成本/分数上是有吸引力的质量档，但不代表它适合所有判断任务；Sol 的价值主要在复杂根因和高后果决策。
- 旧模型只在兼容、复现、回归或本项目局部证据支持时保留，不因历史习惯作为默认路由。

## 本项目的校准观察

Phase 5 已暴露出以下路由信号：

- `JOB_REVISION_CONFLICT`、任务状态机和实时更新问题需要跨模块根因判断；适合 Sol 诊断、Luna 进行边界明确的实现，并要求真实 WebUI/进程验证。
- 单元测试通过但浏览器不会自动刷新，说明测试报告不能替代真实浏览器和 GPU 流程验收。
- `pack.mcmeta` 支持范围必须核对当前 Mojang/官方资料，不能从开发夹具推断标准。
- `dev-format-34.json` 是开发夹具而不是完整生产目录；代理必须核查目录来源、状态标记和覆盖范围。
- PowerShell 命令中的引号错误、参考图格式校验失败，说明文档命令和人工测试资产都要经过产品边界的实际验证。
- 64×64 结果像模糊的 16×16，以及规律性依赖参考图/结构参考，属于生成模型/workflow 质量问题，不能自动归因于软件闭环错误。
- 动态材质、Alpha、不同面贴图、提示词约束、生成质量评估和生产目录仍是后续阶段的未决事项，必须在无上下文交接中保留。

## 后续重校准条件

在以下任一条件发生时重新核对官方资料和 benchmark，并补充本项目实测：模型或 effort 列表变化、价格/credits 规则变化、Artificial Analysis benchmark 版本变化、Advisor 配置变化，或本项目出现新的跨模块失败模式。实测应分别记录成功率、返工次数、耗时、token/额度代理和是否需要人工 GPU/浏览器验收；不要只记录“测试通过”。
