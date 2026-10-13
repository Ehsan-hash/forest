# FOREST 技能源码核查

目标：[mikamikasuki/FOREST](https://github.com/mikamikasuki/FOREST)。核查日期：2026-10-13。隔离 checkout：`ff16c3f656f94d053d1bba65c6b3a07a5feaccde`，浅克隆的默认分支快照。

本记录用于给技能提供真实源码定位与后续验收起点。它不是完整前端审计、功能对照表或 UI 重构完成报告。运行中的服务、浏览器、生产数据与付费 provider 未参与本次核查。

## 代码与规则

| 范围 | 已读入口 / 后续查找位置 |
| --- | --- |
| 仓库规则 | 根 `AGENTS.md` 是独立 PR review policy；其中 current/history、provenance、recovery 等约束适用，PR 决策输出用于实际 PR 审查 |
| 现有科研技能 | `.agents/skills/` 的 autonomous-research、research-judgment、experimentation、academic-writing；运行时政策另由 `research/agents/policy.py` 等组装 |
| 前端 | `apps/web/src/App.tsx`、`Workspace.tsx`、`pages.tsx`、`ui.tsx`、`api.ts`、`apiClient.ts`、`ExecutionSettings.tsx`、`RunEvidence.tsx`、`PaperLayout.tsx`、progress/interventions/files |
| API 契约 | `packages/contracts/api.generated.ts`、`openapi.json`、`INTERFACES.md`；`services/api/` 的 schema/routes 与 kernel 实现一起核对 |
| 执行与证据 | `research/kernel/graph.py`、`services/worker/scheduler.py`、worker、interventions/verification/paper 等，详见各技能 repository-map |
| 测试 | `apps/web/package.json`、`playwright.config.ts`、Vitest/Playwright 用例、`tests/`、`scripts/`、`.github/workflows/ci.yml` |

前端使用 React 19、React Router 7、Vite 6、TypeScript 5.8、React Flow 12、Monaco、xterm、Vitest 4、Playwright；准确安装版本应再读锁文件。没有迁移到 Next.js 的理由。`npm run build` 包含 `tsc -b && vite build`；不存在独立 `lint` / `typecheck` 脚本。Playwright 配置没有自动 `webServer`，需准备独立服务。

## 待浏览器验证的可靠性线索

| 线索 | 本快照的源码证据 | 下一步 |
| --- | --- | --- |
| Runs 深链接 | `App.tsx` 没有 `/projects/:id/runs` 路由，而 intervention 有相关链接 | 点击真实记录，确认 run/project 身份和错误回退，添加失败回归 |
| Branch 查询 | Workspace 的 branch 选择是局部状态，未消费 URL 的 `branch` | 验证直接加载/刷新/后退，以及链接是否传了 run ID |
| Escape 选择 | 清除 selected 后，自动选首节点 effect 可重新选中 | 真实交互复现，包含空白点击及编辑器焦点 |
| 预算草稿 | Overview 在 project 对象变化时重设 budget draft | 编辑未保存值并触发真实项目刷新，验证草稿保留 |
| 响应式入口 | 多段媒体查询覆盖与隐藏规则存在 | 在指定各宽度核对完整功能可达性，不能仅凭 CSS 确认故障 |
| 重复控制 | ProjectShell、Overview、Workspace 的 run/controller 入口分散 | 分别追踪作用域、payload、可用性和状态刷新；不先假定语义相同 |

这些是 **SOURCE INSPECTION**，不是已执行的 browser reproduction，也不是已修复的问题。后续 agent 必须在实际工作 HEAD 复核后再修改。

## 技能中固定的非显然契约

- Graph 操作必须从 kernel 和 API 实现列举；本快照包括 `reopen_branch` 和 `revise_goal` 等摘要之外的操作。节点编辑的 guard 应按真实图/project revision 使用，而不是想当然地传 node revision。
- Retry 的新 Run 保留产生该执行的 revision/config/provider snapshot；历史结果可查看，不自动成为当前 producer。
- `materialize_workspace` 的 `InterventionEffect.run_id` 是重载字段：`services/interventions/workspaces.py` 实际写入并按 branch ID 查询。该 action 的链接不能只按字段名称判为传错 run ID；应依据 action 与 producer 解释，修复查询参数消费，而不擅改持久化格式。
- 接收指令、采用/消费指令、执行完成、证据验证、目标适用性和成果接受是不同事实。
- 隔离服务必须同时核实 data-dir、数据库 URL、`.env`、provider 配置和 worker。单独换端口或 `--data-dir` 不证明数据隔离。
- Fixture/route mock 用于布局和故障测试；它们不能证明真实 worker 产物、科学数值或证据完整性。

详细字段和命令由各技能的 references 给出。若 HEAD 的 source/contracts 与快照冲突，以当前代码为准，先修订技能地图，不按旧文字更改科研语义。
