# FOREST 前端重构与研究分析交付报告

交付日期：2026-10-13。基线：`ff16c3f656f94d053d1bba65c6b3a07a5feaccde`。交付分支：`codex/frontend-product-redesign`。生产验证使用 `index-BIrSAtO6.js`、`WorkspacePage-BMJxELRb.js`、`index-CVqPoWfS.css`；详见 [运行清单](evidence/run-manifest.json)。

## 1. Executive Summary

研究工作区以持久 Research Graph 为主视图，提供完整节点编辑、路径筛选与比较、影响预览、执行控制和历史查询。右侧新增 AI 分析面板，可选择项目总体、当前节点或研究路径；点击图中节点会切换分析焦点，浏览上下文不会自动提交模型请求。

分析已接通实际 API、调度器、worker 和模型 HTTP 接口。接受请求时保存来源快照、版本、指纹和完整对象 ID；结果包括项目摘要、逐实验说明、问题、发现、建议、引用及覆盖范围。所有建议保留历史，项目资料变化会提示重新分析。

最终生产构建通过；浏览器 **108/108 PASS**，单元 **102/102 PASS**，相关后端 **35 PASS / 2 SKIP**。真实分析连通性验证完成三个范围的任务，使用明确标注的本地模型协议响应及真实 Crossref 检索。**真实模型的科研建议质量、引文对结论的支持程度尚未验证**，不能从这些测试推断科学有效性。完整验收边界保留在 [验收矩阵](test-matrix.md)。

## 2. Before / After UI Changes

| 工作流程 | 基线 | 当前实现 |
| --- | --- | --- |
| 研究图与节点 | 图、编辑器和多种辅助视图竞争空间 | 图保持主视图；临时检查器、Focus 视图及上下文动作保留完整编辑能力 |
| 项目导航 | 十个项目入口 | 五个主入口，按任务组织二级页面，旧 URL 保持可达 |
| 论文写作 | 切换主页面编辑论文 | 图旁阅读/写作面板与完整 Paper 页面共享同一草稿会话 |
| 项目/节点分析 | 没有独立来源绑定的分析面板 | 项目、节点、路径上下文预览；显式提交、历史报告、来源和引用可追溯 |
| 实验输出 | 多种状态、日志、指标需要跨页面追查 | 收起式运行 dock、精确 run 深链、实际日志/指标/产物入口 |
| 外观 | 集中式样式与不一致层级 | Sage 配色、明暗/系统主题、语义色、可见焦点和响应式布局 |

实际前后操作对照：节点 Save→Run 均为四次点击；Goal→论文检查/编译/导出→研究图均为七次点击，但主视图切换由两次降为零。当前可下载真实编译 PDF；基线对应入口只提供源码 ZIP。详见 [操作对照](journey-comparison.md)。

## 3. Navigation Changes

主入口为 Workspace、Library、Experiments & Results、Paper、Files；全局 Settings 位于底部。Workspace 包含 Overview、Graph、Ideas、Theory；实验区包含 Experiments、Runs、Data & analysis、Figures。图、论文、分析之间可切换并保留草稿。

项目切换器与命令面板搜索真实项目、节点和运行记录。完整 project/node/branch/run ID 进入 URL；浏览器前进、后退和刷新恢复选择，缺失或跨项目对象显示可恢复状态。路径比较、路径操作、分支执行和高级图命令具有明确入口，窄屏仍可到达。详见 [信息架构](information-architecture.md) 与 [交互规范](interaction-spec.md)。

## 4. Major Component Refactoring

`pages.tsx` 从 4,932 行的集中页面模块改为九个页面所有者及兼容导出，路由按需加载。新增 shell、shared、runs 模块和独立草稿状态助手，保留现有请求、刷新、身份与版本机制。

`WorkspacePage` 管理图旁 Paper/Analysis 面板；`ResearchAnalysisPanel` 管理预览、分析意图、任务状态、报告和引用；`PaperWritingReview` 与完整论文编辑器共享会话；`ProjectRequirements` 编辑出版要求与评估协议。后端新增 `research_analysis` 任务及上下文服务，独立于 controller 路径审查。

重构并未消除全部大型模块：`App.tsx` 约 1,841 行、`Workspace.tsx` 约 2,815 行，仍需要后续拆分。模块归属及验证记录见 [组件清单](component-inventory.md)、[模块说明](frontend-modules.md)。

## 5. Functional Bugs Fixed

- 修复缺失 Runs 路由、branch/run 查询未消费、Escape 后重新选择节点，以及迟到的 Retry 响应覆盖较新选择。
- 节点、项目运行设置、全局设置、论文、干预指令和分析问题保留原始草稿与基线；刷新、切换、重载、冲突及迟到保存确认不会清空后续编辑。
- 修复窄屏路径比较/操作入口被旧 CSS 隐藏、文件分页旧响应覆盖当前页面、源文件被重建后旧编辑器覆盖新内容等问题。
- Undo/Redo 保留真实已完成运行、输出和来源配置；实际显式/隐式执行循环被拒绝，图版本保持不变。
- 运行设置首次 PATCH 初始化完整默认值；真实 figure worker 渲染中的依赖函数遮蔽问题已修复。
- PDF 预览下载完整字节后再初始化 pdf.js，卸载时取消下载；真实 PDF 的十二次延迟下载/退出循环无页面错误，最终画布有实际内容。
- AI 上下游使用服务器计算的执行依赖，包含隐式输入关系；参考关系仍可查看，但不被误当作执行依赖。

每项回归对应的实际/模拟/故障注入边界与失败记录见 [浏览器逐项结果](evidence/browser-results.md)、[工作区审计](workspace-audit.md) 和 [验收矩阵](test-matrix.md)。

## 6. Feature Preservation Matrix

| 能力组 | 当前入口与保留内容 | 验证范围 |
| --- | --- | --- |
| 图与研究路径 | Workspace：节点/边、类型关系、分支、比较、合并、影响预览、撤销重做、修订与完整身份 | 实际图 API、浏览器、循环拒绝及历史保留；部分手势/操作组合仍为 PARTIAL |
| 执行与干预 | Run dock / Runs：提交、队列、日志、Pause/Resume/Cancel/Retry/Skip、checkpoint、指令与决策 | 真实普通 worker 和停止调试 checkpoint；live Agent 决策消费另列 NOT RUN |
| 研究资料与证据 | Library / Experiments / Data / Figures：来源、实验、验证、统计、图及导出 | 真实检索、文件与计算、来源绑定；不等于科学结论已验证 |
| 论文与要求 | Paper / 右侧面板 / Requirements：源码、Bib、排版、来源、编译、PDF、ZIP、协议 | 实际草稿、冲突、worker 编译与产物；publication PATCH 原子版本保护仍有边界 |
| 文件与系统 | Files / Settings：目录、源码、二进制、仓库来源、provider/host/Agent/资源/分享 | 实际字节、权限和来源读回；删除性清理保留模拟验证边界 |
| 新增 AI 分析 | 右侧 Analysis：项目/节点/路径、上下文、历史、建议、文献、来源导出 | 实际 API/worker/模型接口和文献检索；live 科研质量 NOT RUN |

完整 [功能清单](feature-inventory.md) 包含原有 F01–F102 与新增 F103；[API 覆盖](api-coverage.md) 包含原有 A01–A57 与新增 A58。源码可达性、浏览器观察和科学验证分别记录，不能将入口保留等同于全部场景 PASS。

## 7. API Integration Verification

当前生成契约包含 **185 个操作、142 个路径**；新分析请求有类型契约，前端使用现有传输、事件刷新与错误恢复机制。四个分析入口为项目上下文预览、提交、历史，以及指定 run 的报告读取。操作列表覆盖不表示 185 个操作都经过浏览器验证。

分析上下文贯通项目内所有已记录节点、边、分支、显式/隐式依赖、指令/配置/输入输出，以及当前和历史科学任务、指标、错误、验证、产物；还包括研究资源、来源段落、Goal/Protocol 版本、干预及效果、决策、工具调用、事件、文件版本和材料位置。各关系保留原 ID；外项目记录、分析任务及其自产资源被排除，分析历史另行保留。

模型输入保存完整可用的结构化记录和有界文本；单文件日志/文本上限为 262,144 字节，剩余未读部分明确标记。大型二进制与数据集提供位置和元数据，不声称模型已读取全部字节。已知配置凭据与识别到的凭据字段经过脱敏；测试不证明任意秘密格式均能被识别。没有 provider 时仍可查看实际上下文，提交被拒绝且不入队。

提交保留 request UUID、已审阅版本/指纹和问题/provider 意图。同一请求重放返回同一 run；资料只改变而图版本未变时也能返回 CONTEXT_CHANGED，保留草稿等待显式重审。分析不绑定实验节点执行，也不覆盖节点状态、latest run 或 controller 决策。

引用来自服务器管理的文献库与 Crossref/arXiv 检索接口；记录 metadata/abstract/passages 的实际阅读范围。真实 smoke 验证了 Crossref 元数据，arXiv live 检索与全文科学支持 NOT RUN。不存在的引用、错误节点/run 归属、缺少要求覆盖或快照完整性校验失败会使分析失败；保存的报告被改动时，读取显示产物错误并隐藏报告内容。这些校验保护身份、结构和产物完整性，不评定建议的科学有效性。详见 [后端逐项结果](backend-analysis-results.md)、[真实分析收据](evidence/analysis-worker-smoke.json)。

## 8. Real E2E Results

| 验证 | 结果 | 方法与证据 |
| --- | --- | --- |
| 最终生产构建 | PASS | TypeScript + Vite：[日志](evidence/build-delivery.txt) |
| 最终浏览器 | 108/108 PASS，196.30 秒；无 skip/flaky/unexpected | 同一最终生产资源、独立实际 API/worker；各用例保留真实、模拟和故障边界：[逐项索引](evidence/browser-results.md)、[JSON](evidence/e2e-delivery.json)、[日志](evidence/e2e-delivery.txt) |
| 最终单元 | 102/102 PASS，20 个测试文件；日志报告 977ms | helper/组件行为：[逐项索引](evidence/unit-results.md)、[JSON](evidence/unit-delivery.json)、[日志](evidence/vitest-delivery.txt) |
| 相关后端 | 35 PASS / 2 SKIP，64.33 秒 | 实际 SQLite/API/上下文与指定执行路径，模型/search seams 明示；PostgreSQL 未配置：[37 项结果](backend-analysis-results.md)、[日志](evidence/analysis-integration-tests-final.txt) |
| 三范围分析 smoke | 项目/节点/路径 3/3 completed | 实际 API→scheduler→worker→ModelClient HTTP→保存报告；两个真实实验，一个完成一个 exit7 失败；模型响应为协议 fixture：[JSON](evidence/analysis-worker-smoke.json)、[日志](evidence/analysis-worker-smoke.txt) |
| 原始全后端基线 | 1429 PASS / 71 SKIP / 63 FAIL，1572.60 秒 | macOS 缺 Linux Bubblewrap 条件；61 项确认、1 项推断相关，另1项旧 skill 清单断言已修复并定向通过：[分类](backend-baseline-failures.json)、[原日志](evidence/baseline-pytest.txt) |

实际数值实验计算 5,000 区间的 x² 梯形积分，保存指标、CSV、产物与来源；实际二进制上传下载、配对统计、source-bound checks、figure SVG/PDF、论文 PDF、权限拒绝及 EventSource 重连也有独立收据，见 [证据索引](evidence/README.md)。模型 fixture 验证通路与恢复，不验证建议质量；重复定向测试不累加为独立用例数。

原始56项验收为35项限定范围 PASS、18项 PARTIAL、2项 MOCK UI/真实条件 NOT RUN、1项 NOT RUN；新增8项分析验收为6 PASS、1 PARTIAL、1 NOT RUN。可执行测试通过不替代这些尚未运行的条件。

## 9. Visual Regression Results

最终资源覆盖主路由 156 次观察及四个面板截图；另有 48 张 AI 状态、30 张展开编辑状态、36 张可读性页面与12张路径对话框，以及32张实际运行/图状态截图。明暗主题、390/768/1024/1280/1440/1920 宽度按各组定义执行，并不声称每一种状态都覆盖全部六宽度。

图状态32张均由实际打开像素复核；AI48张通过原分辨率面板联系图及指定完整截图复核，其他组的精确打开范围见 [视觉对照](visual-comparison.md)。采样对比度与无溢出/无页面错误观察分别记录，不能替代所有屏幕的人工审查。实际 populated 输出/表头最低采样对比度 5.6496；AI及可读性采样最低值分别约 5.1079、5.2127。

测试项目的两个 smoke 节点在分析完成后仅调整位置，版本2→3，并保存 [布局读回收据](evidence/analysis-visual-layout.json)；原分析快照仍是版本2。深色窄屏 Focus 标题在等待页面样式稳定后重新截图，实际颜色与打开像素通过：[收据](evidence/focus-title-settled.json)。

## 10. Accessibility Results

可见焦点、语义按钮/标签、命令面板键盘选择、对话框 Escape 与焦点归还、图键盘位置保存、紧凑阅读面板 Tab 限制/背景 inert、明暗主题和 reduced-motion 路径已执行浏览器验证。窄屏操作入口与选中节点标题/文字状态可读；原生 200% 缩放有七张限定范围的实际 Chrome 观察。

这不是完整 WCAG 认证。屏幕阅读器、每个 overlay 的全部键盘序列、Monaco/终端所有原生快捷键排除及全页面200%矩阵尚未完整运行。详见 [验收矩阵](test-matrix.md) 与 [视觉证据](visual-comparison.md)。

## 11. Performance Results

同一安装依赖与 Vite 6.4.3 条件下，初始 index JS 从 **694,362→410,121 字节（−40.94%）**，gzip 从 **217,261→128,162 字节（−41.01%）**。这是初始 JS 的测量，不是全部延迟加载资源的缩减，也不是运行速度结论。详见 [字节比较](evidence/bundle-comparison.json)。

Monaco 的约2.31MB延迟块仍有构建警告。100节点结构 fixture 支持搜索、选择和检查器观察；大图 pan/edit 帧率和 FCP 前后对照 NOT RUN。PDF 为消除加载卸载竞态先缓冲完整文件，大型 PDF 首屏时间与内存基准 NOT RUN。

## 12. Remaining Issues

| 边界 | 当前事实与下一步验证 |
| --- | --- |
| live AI 科研质量 | 未使用真实模型评估建议或全文支持；需用实际研究项目和配置 provider 审查结论、概率成功标准及原文引文支持 |
| Linux Agent / 人类决策消费 | macOS 无 Bubblewrap；需独立 Linux 条件执行真实 Agent 干预/accept/edit/reject 链路 |
| PostgreSQL | 两个变体 SKIP；需专用临时数据库运行 |
| publication 配置并发 | 草稿和未知 key 保留；现有 publication PATCH 缺少原子服务器版本保护，fresh-read 与写入之间仍有并发窗口 |
| 完整交互矩阵 | 部分边创建手势、输出 split-byte 旧响应、历史 source-change Retry、原生快捷键和完整 overlay 键盘序列未逐一闭合，见56项验收行 |
| 性能与架构 | FCP/大图帧率/大型 PDF 缓冲尚未测量；App/Workspace 仍是大型模块 |

## 13. Files Changed

[完整文件清单](evidence/changed-files.tsv) 区分修改/新增路径；[源文件 SHA-256](evidence/source-sha256.tsv) 标识可审阅版本。主要交付为 `apps/web/src` 的页面、shell、共享面板、草稿与设计系统；`apps/web/tests` 的实际/故障回归；API/worker 的研究分析任务与契约；`tests/test_workspace_analysis.py`；以及本目录中的审计、矩阵、截图、脚本和证据。

生成契约、API 参考与七个 frontend skill 一并更新。截图和测试记录属于交付证据，不计为产品功能或新的科研结果。

## 14. Commits

交付提交主题：`feat: redesign research workspace and integrate source-bound AI analysis`。交付分支为 `codex/frontend-product-redesign`，确切提交 ID 以该分支的 Git 记录为准，可用 `git log -1 --format='%H %s' codex/frontend-product-redesign` 查询。可审阅差异相对于基线 `ff16c3f656f94d053d1bba65c6b3a07a5feaccde`。

## 15. Final Delivery Status

前端实现、研究路径操作与右侧 AI 分析的上下文/API/worker/模型接口贯通已交付，最终构建及指定回归通过。分析以真实状态树、历史实验和来源为依据，支持显式请求、问题定位、建议、文献与导出，并保留草稿、冲突和来源变化提示。

整份任务的所有外部条件尚未全部验收；PARTIAL、SKIP、MOCK 与 NOT RUN 保留原状态。真实模型科学质量、Linux Agent、PostgreSQL、完整无障碍及性能基准是明确的后续验证范围。可运行预览位于独立测试服务 `http://127.0.0.1:18340`，其中保留的协议 fixture 报告明确标注测试响应。
