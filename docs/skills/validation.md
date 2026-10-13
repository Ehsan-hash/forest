# Skills 验证结果

日期：2026-10-13。FOREST 源码快照：`ff16c3f656f94d053d1bba65c6b3a07a5feaccde`。

## 格式与引用

| 检查 | 结果 | 方法 / 证据 |
| --- | --- | --- |
| 七个 SKILL.md | PASS，7/7 | skill-creator 的 `quick_validate.py:validate_skill`，检查 frontmatter、命名、描述及未完成模板 |
| 七个 agents/openai.yaml | PASS，7/7 | PyYAML 解析；显示描述长度、`$skill-name` 默认提示、目录名称一致性、默认自动调用策略 |
| 本地参考链接 | PASS，17 个 | 解析技能 Markdown 链接并检查真实文件存在 |
| 源码文件定位 | PASS，123 个显式路径 | 在上述 FOREST checkout 中检查文件存在；这不是运行测试 |
| Python 所有者定位 | PASS，26 个 | 解析源码 AST，核对被引用的函数、类或变量 |
| Python 测试定位 | PASS，57 个名称 | 核对测试函数或测试模块存在；这些测试没有在本轮执行 |
| Bash 示例 | PASS，3 个代码块 | `bash -n`，只检查语法，未启动服务 |
| 完整任务原文 | PASS | `redesign-mission.md` 与用户附件逐字节一致 |

格式检查使用独立临时 Python 环境和 PyYAML 6.0.3。机器可读结果见 [structural-validation.json](structural-validation.json)。引用定位只能证明材料存在，不能证明描述的业务行为已运行通过。

## 独立场景应用

以技能文件与真实源码作为输入，独立只读评估产生具体实施和验收 brief；没有提供预期答案，也没有修改应用。

| 场景 | 结果 | 实际检查的指导能力 |
| --- | --- | --- |
| A：768px 五项导航，人工决定历史打开指定 Run | COMPLETED | 能区分能力分组与删除、API 与浏览器路由、完整 Run 身份与首项回退、断点可达性 |
| B：暗色实时论文面板，resize/hide/full editor | COMPLETED | 能定位统一稿件会话、GET 初始 draft、SSE/dirty 分离、布局与指令草稿跨卸载保护 |
| C：fork/reopen 与正式指令，并发修改旧 preview | COMPLETED | 能保持原 guard/intent/request ID、409 恢复、materialize effect 的 Branch 身份、reopen 与 Resume 区分 |
| D：历史 revision retry，Data/Figures/Paper 状态与当前 PDF | COMPLETED | 能区分执行、验证、适用性与接受；保留历史来源；使用 current PDF guard；设计无 provider 的真实 worker 产物验收 |

发现并已复核解决三处技能歧义：论文 GET 创建初始持久 draft；共享文档身份不足以保留所有编辑草稿；run 范围的正式指令限定 active Agent + `next_request`。另补入现有数值实验的真实产物/导出/刷新旅程，以及测试 helper 必须先设置隔离环境再 import API 模块。

场景检查支持“这些技能能指导后续实施”的结论，不构成产品功能、浏览器可用性、科研有效性或完整 Skills 运行时发现的证明。

## 尚未执行

| 检查 | 结果 | 原因 |
| --- | --- | --- |
| FOREST 前端 build / Vitest / Playwright | NOT RUN | 本轮范围为技能构建，应用重构及基线验证交给后续任务 |
| 真实 API / normal worker / 数值产物 / TeX 编译 | NOT RUN | 本轮只核查代码与指导流程，未启动业务服务 |
| 真实浏览器截图、响应式、无障碍、性能测量 | NOT RUN | 未实施前端，也未运行应用；没有相应运行证据 |
| 模型/provider 调用 | NOT RUN | 技能构建无需模型执行或付费服务 |
| 新会话技能发现与目标模型配置 | NOT RUN | 本包准备仓库目录与调用信息；后续在正式 FOREST checkout 和宿主设置中启用 |

最终状态：**技能包已完成并通过结构与只读场景验证；前端重构尚未实施。** 后续入口见 [handoff.md](handoff.md)。
