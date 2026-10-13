# FOREST Skills 交接

本包完成前端重构所需的七个仓库技能。FOREST 应用实现、基线测试与真实浏览器验收属于下一次重构任务；本包的源码核查不代表产品验收通过。

## 放入 FOREST

将本包 `.agents/skills/` 下的七个 `forest-*` 目录合入正式 FOREST checkout 的 `.agents/skills/`，同时带入 `docs/skills/`。先检查同名目录；存在时审阅差异后合并，避免覆盖用户修改。

保留目标仓库现有 `AGENTS.md`。将本包根 `AGENTS.md` 的 **Skill routing** 与 **Shared redesign constraints** 合并为前端任务指导；不要把技能准备仓库的项目说明或 PR 审查策略相互覆盖。保留现有 `forest-autonomous-research`、`forest-research-judgment`、`forest-experimentation` 和 `forest-academic-writing`，它们继续负责科研判断与运行时科研政策。

从 FOREST 根目录启动 Codex。Codex 从仓库内 `.agents/skills/` 发现技能；正文和 references 按需读取。若列表未更新，重启会话。发现目录、显式 `$skill-name` 调用和 `agents/openai.yaml` 的用途见 [OpenAI 官方技能文档](https://learn.chatgpt.com/docs/build-skills)。

## 后续 agent 设置

在实际任务宿主中选择 **gpt-6.1-sol / ultra**。`agents/openai.yaml` 只提供技能显示信息和调用示例，不选择模型或 reasoning effort。本包没有修改全局 Codex 配置，也没有启动模型会话。

若用 Codex CLI，先核实该安装及账户接受的模型标识与 effort；不把桌面显示名当成已验证的 CLI 参数，不静默换模型。原任务提到的 `gpt-6-luna / medium` 调试建议是可选方案，不是本包运行时依赖。

## 可直接交给后续 agent 的启动指令

```text
在当前 FOREST 仓库完成前端产品重构与真实功能集成。

读取当前有效的 AGENTS.md、docs/skills/redesign-mission.md 和 docs/skills/source-audit.md。
任务以本次 HEAD 为准；先记录 git 状态与 commit，复核源码地图，不沿用旧问题的结论。

按任务加载 $forest-product-ux、$forest-visual-design、$forest-frontend-architecture、
$forest-research-graph、$forest-run-lifecycle、$forest-evidence-integrity、
$forest-browser-testing；references 只读当前阶段需要的部分。

先建立真实组件、功能与 API 覆盖记录，运行隔离基线并保存真实页面截图。
随后连续推进可靠性、设计系统、导航外壳、Workspace、其余页面、架构整理与完整验收。
不要停在文档、视觉变化或组件测试。保留全部现有能力和科研执行语义，
不得伪造科研数据、进度、成功、持久化或证据状态。常用动作保留直接入口。

优先验证 Runs 路由、branch/run 身份、Escape 选择、预算草稿、响应式入口
和不同入口的作用域一致性；这些源码线索仍需本次 HEAD 和浏览器复核。

保护用户数据与活动任务。测试使用独立数据库、数据目录和端口。
不自动 push、创建 PR 或 merge，不擅自调用付费服务或修改正式部署与凭据。
普通实现选择自主完成，只有具体数据损失、不可逆生产操作、额外收费或
安全性无法判断的迁移需要用户授权。

每阶段保留可运行构建并运行相关验证；失败先修复再继续。
验收报告区分源码检查、fixture、真实 API/worker、浏览器和科研测量证据。
未执行项标 NOT RUN 并写缺失条件；不得降低测试预期、删除或跳过测试掩盖回归。
完整记录前后任务点击数、上下文切换数、必要决策数、截图、日志、测试结果和剩余限制。
```

详细任务原文保存在 [redesign-mission.md](redesign-mission.md)，上游方法筛选见 [upstream-references.md](upstream-references.md)，技能验证结果见 [validation.md](validation.md)。
