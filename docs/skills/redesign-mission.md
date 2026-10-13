# MISSION: FOREST Frontend Product Redesign & Full Functional Integration

Repository:
https://github.com/mikamikasuki/FOREST

Project:
FOREST — An Agentic Scientific Research Workspace

Mission Type:
Full Product Redesign
Frontend Architecture Refactoring
Interaction Design
Visual Design System
End-to-End Functional Integration
Reliability Engineering
User Experience Optimization

Priority:
P0 — Product usability
P0 — Functional correctness
P0 — Existing feature preservation
P1 — Visual excellence
P1 — Frontend maintainability
P1 — Performance
P1 — Accessibility

================================================================================
0. YOUR ROLE AND MISSION
================================================================================

你现在负责对 FOREST 进行一次完整的前端产品级重构。

你的角色不是单纯的 CSS 工程师。

你需要同时承担以下职责：

1. Senior Product Designer
2. Senior UX Designer
3. Frontend Architect
4. React / TypeScript Engineer
5. Human–Computer Interaction Engineer
6. Design System Engineer
7. Frontend Reliability Engineer
8. End-to-End QA Engineer
9. Accessibility Engineer
10. Product Integration Engineer

你的目标不是制作一个视觉上类似 AI 产品的静态界面。

你的目标是将 FOREST 改造成一款真正成熟、直观、美观、可靠的 AI 科研工作台。

设计品质参考：

- ChatGPT
- Claude
- Cursor
- Linear
- Notion
- Raycast
- Apple macOS applications

这些产品仅作为视觉层级、交互细节、信息密度和组件品质的参考。

禁止简单复制任何产品的完整布局。

FOREST 必须保留自己的产品身份。

FOREST 的核心是：

Persistent Research Graph
Autonomous Research Execution
Human Intervention
Scientific Experimentation
Evidence Verification
Research Artifact Management
Scientific Manuscript Production

它不是一个普通聊天机器人。

不能为了模仿 ChatGPT，就把 Research Graph、实验系统、Evidence Gate、论文编辑器简化成一个聊天框。

最终产品应同时具有：

- AI 产品的简洁和自然交互
- 科研软件的严谨和可追溯性
- 开发工具的强大功能
- 专业生产力软件的稳定性
- 现代 SaaS 应用的视觉品质

================================================================================
1. NON-NEGOTIABLE REQUIREMENTS
================================================================================

以下要求不可妥协。

1.1 必须真正修改代码

禁止只提供：

- 设计建议
- UI mockup
- Figma 风格描述
- Markdown 设计文档
- 静态 HTML Demo
- 无后端的 React 原型

最终必须是已经接入 FOREST 真实业务逻辑的可运行前端。

1.2 必须保留全部功能

重新设计导航不等于删除功能。

隐藏复杂度不等于禁用复杂功能。

必须保留：

- Research Graph
- Goal Management
- Project Management
- Research Planning
- Graph Editing
- Research Nodes
- Research Branches
- Literature Management
- Ideas
- Hypotheses
- Theory
- Experiments
- Runs
- Data Analysis
- Figures
- Manuscript Editing
- Manuscript Compilation
- Evidence Tracking
- Human Intervention
- Human Decisions
- Agent Execution
- Provider Configuration
- Execution Settings
- Budget Management
- File Management
- Logs
- Terminal
- Export
- Import
- Sharing

以及当前仓库实际实现的其他全部功能。

不能因为某项功能暂时没有合适的 UI 位置，就直接删除。

1.3 严格禁止虚假功能

以下行为一律不允许：

- 按钮只有 onClick，但没有真实效果
- 点击后只显示 toast
- 使用 setTimeout 模拟执行完成
- 使用假进度条模拟研究进度
- 使用 mock data 作为正式科研结果
- 用硬编码 JSON 假装真实 API 响应
- 使用假论文代替真实 Manuscript
- 使用假图表代替实验结果
- 使用固定的“成功”状态掩盖错误
- API 调用失败但 UI 显示成功
- 保存操作没有持久化
- 页面刷新后数据丢失
- 运行状态与真实后台状态不一致

测试 fixture 可以存在于隔离测试环境。

生产 UI 不能依赖伪造数据才能正常展示。

1.4 不破坏现有科研系统

严禁为了重构 UI 修改科研执行语义。

特别保护：

- Goal Contract
- Research Graph
- Node Identity
- Graph Revision
- Branch Identity
- Execution Dependencies
- Evidence Lineage
- Experiment Results
- Run History
- Research Decisions
- Checkpoints
- Manuscript Artifacts
- User Instructions
- Research State Persistence

不得无故改变已有科研数据格式。

如果必须修改 API 或数据结构：

必须证明必要性、兼容性，并添加迁移或回归测试。

1.5 不修改现有用户数据

不得执行破坏性清理。

不得使用：

git reset --hard
git clean -fd
rm -rf

清理用户已有的数据、未提交修改或实验结果。

不得擅自取消当前正在运行的科研任务。

运行测试时优先使用独立的临时数据库、工作目录和服务端口。

1.6 不重复造轮子

优先使用现有技术：

React
TypeScript
Vite
React Router
React Flow
Monaco Editor
xterm
Playwright
Vitest

引入新的依赖必须有明确理由。

禁止为了一个简单 UI 控件引入完整的新框架。

禁止无必要地迁移整个技术栈。

1.7 完整实施，而不是只提交计划

先进行代码审计，然后直接开始实施。

按阶段完成，每阶段保持构建可运行。

不要因普通设计细节反复要求用户确认。

只有涉及以下情况才需要明确用户授权：

- 可能丢失用户数据
- 不可逆的生产操作
- 额外付费
- 修改正式服务部署
- 修改真实生产凭据
- 无法判断是否安全的数据迁移

禁止自动 Push、创建 PR 或 Merge。

允许创建本地、可审查的 Git Commits。

================================================================================
2. FIRST STAGE: COMPLETE EXISTING FRONTEND AUDIT
================================================================================

在修改任何代码之前，先阅读当前仓库。

重点检查：

apps/web/src/App.tsx
apps/web/src/Workspace.tsx
apps/web/src/pages.tsx
apps/web/src/styles.css
apps/web/src/ui.tsx
apps/web/src/api.ts

apps/web/src/ExecutionSettings.tsx
apps/web/src/RunEvidence.tsx
apps/web/src/PaperLayout.tsx
apps/web/src/DemoGuide.tsx
apps/web/src/ProviderUsage.tsx

apps/web/src/progress/
apps/web/src/interventions/
apps/web/src/files/

apps/web/package.json
apps/web/playwright.config.ts

packages/contracts/
services/api/
tests/

以及其他实际相关文件。

同时阅读：

AGENTS.md
README.md
相关设计文档
API contracts
现有测试
CI configuration

优先遵守仓库当前有效的开发约束。

--------------------------------------------------------------------------------
2.1 建立完整前端组件地图
--------------------------------------------------------------------------------

输出：

docs/ui-redesign/component-inventory.md

记录：

Component
Source File
Parent Component
Route
User Purpose
State Dependencies
API Dependencies
User Actions
Current Problems
Proposed Changes

分析是否存在：

- 巨型组件
- 重复组件
- 过深的组件层级
- 过多状态变量
- 重复 useEffect
- 重复数据请求
- 过多条件渲染
- 多处维护相同状态
- 无法独立测试的复杂组件
- UI 和业务逻辑耦合
- 不可控的事件订阅
- 隐式依赖

特别审计：

App.tsx
Workspace.tsx
pages.tsx

这些文件较大，应检查是否适合按职责拆分。

但是：

禁止为了追求小文件，将相关逻辑机械拆成数百个文件。

拆分原则：

High Cohesion
Low Coupling
Single Responsibility
Stable Public Interfaces

--------------------------------------------------------------------------------
2.2 建立真实功能地图
--------------------------------------------------------------------------------

输出：

docs/ui-redesign/feature-inventory.md

为全部真实功能建立记录：

Feature ID
Feature Name
Current Route
Current Component
User Entry Point
API Endpoint
Request Payload
Response Type
Required IDs
Revision Handling
Persistence
Error Handling
New Entry Point
Test Coverage
Verification Status

必须从代码和 API contracts 获取真实信息。

禁止臆造不存在的 Endpoint。

--------------------------------------------------------------------------------
2.3 建立交互问题清单
--------------------------------------------------------------------------------

重点寻找：

A. 一个操作存在多个入口，但语义不一致

B. 两个按钮看起来不同，实际执行相同操作

C. 相同操作在不同页面表现不一致

D. 点击按钮后用户不知道发生了什么

E. 用户不知道任务当前状态

F. 功能需要多次不必要的页面跳转

G. 常用功能隐藏太深

H. 低频功能占据重要位置

I. 操作缺少撤销或返回机制

J. 表单输入被自动刷新覆盖

K. 用户选择在刷新后丢失

L. 用户无法定位错误原因

M. UI 状态与服务端状态不同步

N. 视觉上可操作，但实际上不能执行

O. 深链接无法恢复正确对象

P. 页面存在明显的空白或重复布局

Q. 页面滚动层级混乱

R. 弹出层遮挡主要操作

S. 响应式布局使部分功能不可访问

T. 关键错误被隐藏在 toast 中

--------------------------------------------------------------------------------
2.4 优先核查已知问题
--------------------------------------------------------------------------------

以下线索来自较早版本的源码检查。

必须在当前 HEAD 重新确认。

不要假定它们仍然存在。

问题 1：

InterventionPanel 中存在指向：

/projects/:id/runs?run=...

的链接。

较早版本的 App 路由没有注册对应的 runs 页面。

检查：

- 当前是否仍存在
- 点击是否正常
- 是否跳转错误
- 是否丢失 run ID
- 是否出现错误项目上下文

问题 2：

存在：

workspace?branch=...

类型的链接。

检查 Workspace 是否真正解析 URL 中的 branch 参数。

特别检查链接中是否错误传入 run_id，而不是 branch_id。

问题 3：

Workspace 使用 Escape 取消选择后，
自动选中第一个节点的 effect 可能重新选择节点。

验证实际行为。

问题 4：

Overview 中 Project 对象更新可能重置预算编辑草稿。

验证用户编辑过程中收到实时更新时，
尚未保存的内容是否丢失。

问题 5：

部分媒体查询使用 display:none 隐藏搜索或操作按钮。

检查是否出现：

较宽屏幕无法访问功能，
更窄屏幕反而重新显示功能的情况。

问题 6：

检查所有相同功能的重复入口。

例如：

运行控制
Project Settings
Paper Editing
Research Instructions
Research Suggestions
Graph Operations

确认语义和状态一致。

每一个确认的问题：

1. 创建复现步骤
2. 添加失败回归测试
3. 修复
4. 运行测试
5. 记录结果

================================================================================
3. PRODUCT DESIGN PHILOSOPHY
================================================================================

核心设计理念：

"Complexity in capability, simplicity in interaction."

能力可以非常复杂。

界面不能因此变得复杂。

用户不应该为了使用 FOREST，
先理解其全部内部 Agent Architecture。

用户应该能够自然完成：

定义研究目标
观察研究进展
干预研究过程
查看真实实验
分析结果
编辑论文
审查证据
导出成果

--------------------------------------------------------------------------------
3.1 Progressive Disclosure
--------------------------------------------------------------------------------

默认展示最有用的信息。

高级选项按需展开。

但不能将功能永久隐藏。

例如：

普通用户看到：

Research Objective
Current Progress
Run Research
Review Results

高级用户展开后看到：

Execution Backend
Provider
Budget
Branch Configuration
Node Configuration
Graph Revision
Raw JSON
Event History
Evidence Metadata

--------------------------------------------------------------------------------
3.2 Contextual Interaction
--------------------------------------------------------------------------------

操作应出现在需要它的位置。

例如：

选中节点：

显示 Edit Node、Run Node、Inspect Evidence。

选中多个节点：

显示 Run Selected、Group、Compare 或现有允许的批量操作。

选中分支：

显示 Branch Actions。

查看实验：

显示 Results、Logs、Evidence、Retry。

查看论文：

显示 Edit、Preview、Compile、Export。

不能将所有操作全部堆在全局工具栏。

--------------------------------------------------------------------------------
3.3 Single Source of Truth
--------------------------------------------------------------------------------

相同实体只能有一个权威状态来源。

例如：

同一个 Run 在：

Workspace
Experiments
Progress
Interventions

中的状态应一致。

同一论文在：

侧边实时论文视图
独立论文编辑器

之间不能产生两套互相覆盖的编辑状态。

--------------------------------------------------------------------------------
3.4 Preserve Scientific Transparency
--------------------------------------------------------------------------------

科研信息必须准确。

UI 不能将：

Execution Success

解释成：

Scientific Validation Success。

不能将：

Evidence Recorded

解释成：

Evidence Verified。

不能将：

Instruction Accepted

解释成：

Instruction Executed。

必须严格区分实际科研状态。

--------------------------------------------------------------------------------
3.5 No Fake Simplicity
--------------------------------------------------------------------------------

禁止通过删除功能制造简洁。

禁止通过把字缩小制造留白。

禁止通过隐藏错误制造干净。

禁止通过牺牲可访问性制造高级感。

================================================================================
4. INFORMATION ARCHITECTURE REDESIGN
================================================================================

当前项目导航过于平铺。

将项目内一级导航重组为：

1. Workspace
2. Library
3. Experiments & Results
4. Paper
5. Files

Settings 放在全局侧栏底部。

--------------------------------------------------------------------------------
4.1 Workspace
--------------------------------------------------------------------------------

包含：

Research Overview
Current Goal
Research Graph
Research Nodes
Ideas
Hypotheses
Theory
Branch Management
Research Progress
Human Intervention

这些功能可通过工作台内的不同视图、对象分类、
上下文菜单或编辑模式访问。

禁止直接删除原页面实现。

--------------------------------------------------------------------------------
4.2 Library
--------------------------------------------------------------------------------

包含：

Literature
Research References
Sources
Related Documents
Existing Literature Operations

--------------------------------------------------------------------------------
4.3 Experiments & Results
--------------------------------------------------------------------------------

二级视图：

Experiments
Runs
Data
Figures

内部进一步提供：

Metrics
Logs
Results
Artifacts
Evidence
Comparisons

================================================================================
4.4 Paper
================================================================================

包含：

Manuscript Editor
Source Editing
Preview
PDF
Figures
Tables
Evidence References
Compilation
Validation
Export

================================================================================
4.5 Files
================================================================================

包含：

Project Files
File Tree
Upload
Download
Preview
Editing
Artifacts
Source Inspection

--------------------------------------------------------------------------------
4.6 Navigation Requirements
--------------------------------------------------------------------------------

导航必须支持：

- Active State
- Keyboard Navigation
- Deep Linking
- Browser Back
- Browser Forward
- Direct URL Loading
- Project Switching
- Route Persistence
- Search
- Command Palette

原始路由必须保留兼容。

例如：

旧的 /ideas 可以重定向到 Workspace 的对应分类。

旧的 /figures 可以重定向到 Experiments & Results。

旧的 /overview 可以重定向到 Workspace 概览。

重定向应尽量保留：

Project ID
Selected Entity
Branch ID
Run ID
Document ID
Query Parameters
Demo State

不得将所有未知或错误链接都直接跳转到 /projects。

必须区分：

Not Found
Unauthorized
Project Missing
Object Missing
Invalid Route
Unavailable Resource

================================================================================
5. GLOBAL APPLICATION LAYOUT
================================================================================

目标采用现代 AI 工作台布局。

桌面默认结构：

┌─────────────────────────────────────────────────────────────────────┐
│ Project Name           Research Status          Actions / Settings │
├────────────┬───────────────────────────────────┬────────────────────┤
│            │                                   │                    │
│ Workspace  │                                   │                    │
│ Library    │         Main Workspace            │  Live Manuscript   │
│ Experiments│                                   │                    │
│ Paper      │          Research Graph           │   Paper Preview    │
│ Files      │                                   │                    │
│            │                                   │                    │
│            │                                   │                    │
│ Settings   │                                   │                    │
├────────────┴───────────────────────────────────┴────────────────────┤
│ Research Status / Needs Attention / Expand Runs                     │
└─────────────────────────────────────────────────────────────────────┘

注意：

这只是主要工作态的布局结构。

不是要求所有页面始终保留三个面板。

根据用户实际任务自适应。

--------------------------------------------------------------------------------
5.1 Sidebar
--------------------------------------------------------------------------------

建议展开宽度：

216–232px

折叠后：

56–68px

要求：

- 品牌标识清晰
- 项目切换器真实可用
- 搜索入口可用
- 导航层级清楚
- 当前页面明显
- Settings 放到底部
- 减少无意义分隔线
- 减少重复标签
- 支持 Collapse / Expand

不得使用巨大的品牌区浪费高度。

项目切换器必须支持：

Search Project
Select Project
Create Project
Return to All Projects

只有在后端确实支持时才提供其他操作。

--------------------------------------------------------------------------------
5.2 Top Bar
--------------------------------------------------------------------------------

建议高度：

52–60px

优先显示：

Project Name
Current Research Status
Needs Attention
Primary Run Action
More Menu

模型连接状态可以使用紧凑指示器。

点击状态后显示详细信息。

不要默认同时显示大量技术参数。

当前项目预算如需展示：

使用简洁摘要。

详细配置进入 Settings 或 Execution Controls。

--------------------------------------------------------------------------------
5.3 Center Workspace
--------------------------------------------------------------------------------

中央为主要工作区域。

必须拥有最大的可用空间。

默认展示：

Research Graph
或者当前用户实际编辑的主要内容。

要求：

- 不被多层面板挤压
- 不被大量卡片包围
- 尽可能减少固定工具栏
- 支持自适应布局
- 支持全屏
- 支持 Focus Mode

--------------------------------------------------------------------------------
5.4 Right Panel
--------------------------------------------------------------------------------

右侧优先服务于成果展示。

推荐默认内容：

Live Manuscript

适用于已有论文的研究项目。

如果没有论文：

展示清楚的空状态。

不要伪造论文。

允许用户：

Hide
Show
Resize
Pin
Expand to Full Page

Node Inspector 不应该永久与 Paper 占据两列。

需要编辑节点时：

在中央区域进入 Focus，
或显示临时 Inspector Drawer。

避免同时出现：

Left Sidebar
Research Graph
Node Inspector
Manuscript Panel
Run Panel

导致研究图只剩下很小的区域。

--------------------------------------------------------------------------------
5.5 Bottom Status Bar
--------------------------------------------------------------------------------

默认高度：

32–36px

用于显示：

Current Run Status
Active Run Count
Pending Decisions
Connection Health
Last Update

要求：

紧凑、清楚、不干扰研究操作。

用户点击：

Runs

打开 Run Drawer。

点击：

Needs Attention

打开待处理任务。

点击：

Connection

查看连接信息。

无任务时不显示大量空内容。

--------------------------------------------------------------------------------
5.6 Panel Resizing
--------------------------------------------------------------------------------

支持调整：

Research / Manuscript 分割比例
Run Drawer 高度
必要的 Inspector 宽度

需要具备：

Minimum Width
Maximum Width
Keyboard Accessible Resize
Double Click Reset
Persistent Preference

使用现有技术实现即可。

不要因为 Resize 功能引入复杂、脆弱的新布局框架。

================================================================================
6. VISUAL DESIGN SYSTEM
================================================================================

设计方向：

Premium Scientific AI Workspace
Minimalist
Frosted Glass
Subtle Depth
Clean Typography
Soft Shadows
High Readability

希望视觉接近：

高质量现代 AI SaaS + macOS 桌面应用。

不是游戏 UI。

不是复杂企业 ERP。

不是仪表盘模板。

不是大量绿色卡片的管理后台。

--------------------------------------------------------------------------------
6.1 Color System
--------------------------------------------------------------------------------

实现完整 Light / Dark 两套主题。

推荐主色：

FOREST Sage Green

Dark Theme：

App Background:
#101715

Surface:
#19221F

Elevated Surface:
#222D28

Primary Text:
#EDF2EE

Secondary Text:
#A5B0A9

Accent:
#8CB69B

Accent Hover:
#A4CBB0

Border:
rgba(255,255,255,0.09)

Light Theme：

App Background:
#F4F6F2

Surface:
#FFFFFF

Elevated Surface:
#FAFBF8

Primary Text:
#18211D

Secondary Text:
#59675E

Accent:
#2E6549

Accent Hover:
#234C39

Border:
rgba(30,55,39,0.10)

Paper Surface：

#FAF8F3

这只是颜色初稿。

最终必须在实际渲染环境验证：

Contrast
Theme Consistency
Hover State
Focus State
Disabled State
Selected State

不要简单将所有颜色变量全局替换。

需要建立有语义的 Design Tokens。

--------------------------------------------------------------------------------
6.2 Frosted Glass
--------------------------------------------------------------------------------

磨砂玻璃是重要视觉特征。

但必须克制。

适合使用：

Sidebar
Topbar
Floating Toolbar
Dropdown
Command Palette
Context Menu
部分 Drawer

不适合使用：

Paper Body
Code Editor
Terminal
Data Table
Experiment Log
Scientific Figure
每个 Research Node

建议：

backdrop-filter: blur(16px) saturate(135%)

也可以根据实际视觉效果调整。

必须提供：

不支持 backdrop-filter 时的回退。

减少透明度偏好下的回退。

透明面板后方文字必须仍然清晰。

禁止：

Glass on Glass
过度模糊
复杂光线折射
大面积霓虹
过强边缘高光
一直运动的背景
无意义玻璃卡片套娃

--------------------------------------------------------------------------------
6.3 Typography
--------------------------------------------------------------------------------

统一字体层级。

建议：

Page Title:
24–28px

Section Title:
17–20px

Body:
14px

Secondary Text:
13px

Small Labels:
12px

只有非关键装饰性元信息才可以更小。

禁止重要交互文本使用：

8px
9px
10px

正文必须足够易读。

中文与英文混排必须自然。

使用合理的系统字体回退。

代码、日志、公式和论文正文应分别使用合适的排版。

--------------------------------------------------------------------------------
6.4 Spacing
--------------------------------------------------------------------------------

建议统一间距系统：

4
8
12
16
20
24
32
40

不要每个组件自行发明间距。

避免：

大量卡片紧贴
重复边框
超大空白
过宽的无意义 Padding
工具栏拥挤

--------------------------------------------------------------------------------
6.5 Radius
--------------------------------------------------------------------------------

Buttons:
8–12px

Inputs:
10–12px

Panels:
14–18px

Floating Containers:
14–18px

避免所有元素都使用极大的圆角。

--------------------------------------------------------------------------------
6.6 Shadows
--------------------------------------------------------------------------------

使用克制的层次感。

默认阴影低对比。

Floating Layers 可以增加柔和阴影。

不要让所有卡片都悬浮。

--------------------------------------------------------------------------------
6.7 Animations
--------------------------------------------------------------------------------

优先使用：

Opacity
Transform
Subtle Hover
Drawer Transition
Selection Transition

建议：

普通状态反馈 120–200ms
面板切换 180–260ms

尊重：

prefers-reduced-motion

不要对巨大 Research Graph 应用大量持续动画。

节点状态可以使用：

Color
Icon
Badge
Border

运行中允许轻微动态提示。

禁止所有节点持续闪烁。

================================================================================
7. COMPONENT DESIGN SYSTEM
================================================================================

建立可复用组件。

按当前项目实际需要实现：

AppShell
Sidebar
ProjectSwitcher
TopBar
StatusBar
PageHeader
Breadcrumbs

Button
IconButton
SplitButton
Input
Textarea
Select
Checkbox
Switch
SearchField

Tabs
SegmentedControl
DropdownMenu
ContextMenu
CommandPalette

Panel
Drawer
Modal
Popover
Tooltip
Toast

Badge
StatusIndicator
ProgressIndicator
EmptyState
ErrorState
LoadingState

DataTable
PropertyEditor
ResizablePanel
ConfirmDialog

ActionToolbar
EntityActions
RunStatus
EvidenceStatus

要求：

所有组件：

- 统一样式
- 统一交互行为
- 统一焦点处理
- 统一错误表达
- 支持 Dark / Light
- 支持键盘
- 支持中英文
- 避免重复代码

复杂组件优先复用可靠的无障碍基础实现。

不得为了减少依赖而牺牲正确性。

但也不得无必要地引入大型 UI 框架。

================================================================================
8. WORKSPACE REDESIGN
================================================================================

这是整个任务中最高优先级的页面。

重点优化：

Research Graph
Research Nodes
Human Research Workflow
Research Progress
Agent Execution
Manuscript Relationship

--------------------------------------------------------------------------------
8.1 Workspace Default View
--------------------------------------------------------------------------------

打开项目后：

显示研究目标。
显示真实当前进度。
显示 Research Graph。
如果有论文，允许并排显示。
显示需要用户处理的事情。

不要默认显示：

大块技术 JSON
空终端
空日志
多个空面板
所有高级配置

如果项目为空：

显示：

Project Goal
Create First Node
Start Research（仅在满足条件时启用）
Explore Templates（仅当真实支持）

不能显示假研究结果。

--------------------------------------------------------------------------------
8.2 Graph Visualization
--------------------------------------------------------------------------------

继续使用 React Flow。

保留当前图结构数据和所有业务关系。

优化节点视觉：

每个节点主要显示：

Node Type
Node Title
Execution Status
Research Status
Key Metric（如果真实存在）

其余内容放在详情。

不要让每个节点都成为巨大的信息卡片。

推荐节点宽度：

220–260px

但根据真实文本内容自适应。

节点必须清楚区分：

Selected
Running
Queued
Completed
Failed
Blocked
Archived
Needs Review

同时保留独立的 Research Status 语义。

不要仅用颜色区别状态。

--------------------------------------------------------------------------------
8.3 Graph Edge Semantics
--------------------------------------------------------------------------------

必须保留：

Execution Dependencies
Input Bindings
References
Derived-From
Evidence Relationships
其他真实存在的关系类型

不同关系使用稳定的视觉编码。

例如：

Execution Dependency:
Solid Line

Reference:
Dashed Line

Evidence:
Distinct Label / Pattern

Implicit Input Binding:
Read-only Visual Edge

具体关系来自实际 Graph Schema。

不要臆造边类型。

不要错误地将引用关系解释为执行依赖。

不能允许删除隐式输入绑定后，
UI 假装底层依赖已经消失。

--------------------------------------------------------------------------------
8.4 Graph Operations
--------------------------------------------------------------------------------

完整审查并保留当前 Graph Command 系统。

包括源码实际实现的：

add_node
edit_node
delete_node
add_dependency
remove_dependency
fork_branch
clone_subtree
insert_before
insert_after
reparent_subtree
merge_branches
split_node
group_nodes
prune_branch
restore_branch
set_main_branch
apply_instruction_patch
undo
redo

以及当前 HEAD 中新增的其他操作。

为这些命令建立：

Operation Registry

记录：

Operation Name
Applicable Selection
Required Parameters
Preview Support
Risk Level
API Mapping
Success Feedback
Error Feedback

常用操作直接暴露。

复杂操作通过 Context Menu 或 More Menu。

重要图修改继续使用现有：

Impact Preview
Expected Revision
Conflict Handling
Undo / Redo

不得重写后失去安全机制。

--------------------------------------------------------------------------------
8.5 Node Selection
--------------------------------------------------------------------------------

单击节点：

选中。

再次点击空白：

取消选择。

双击节点：

打开 Focus Editor。

右键节点：

打开真正的上下文菜单。

按 Escape：

关闭最上层临时 UI；
必要时取消选择。

不允许清除选择后又自动选中第一个节点。

键盘操作必须避开：

Input
Textarea
Contenteditable
Monaco
Terminal

--------------------------------------------------------------------------------
8.6 Context Menu
--------------------------------------------------------------------------------

右键节点显示：

Edit
Run
Inspect Results
Inspect Evidence
Duplicate / Clone
Branch Actions
More

只能显示当前状态真实允许的操作。

例如：

如果某操作不支持该节点：

禁用并解释原因，
或者不提供该项。

不能让每一个节点右键都默认打开 Fork Branch Modal。

--------------------------------------------------------------------------------
8.7 Multi-Selection
--------------------------------------------------------------------------------

支持当前已有的多节点选择行为。

例如：

Shift Selection
Box Selection
Run Selected
Group
Graph Operations

操作栏根据当前选择变化。

不要始终占据页面空间。

--------------------------------------------------------------------------------
8.8 Auto Layout
--------------------------------------------------------------------------------

优化 Graph Layout。

要求：

保留用户手动布局的可控性。
避免无意重排整个图。
大图布局保持稳定。
切换分支不导致节点乱跳。
重复布局结果尽可能稳定。

对于会永久修改节点位置的操作：

提供清楚的操作语义。
保留 Undo。
沿用 revision 机制。

如果需要复杂图布局库：

先分析依赖成本与性能收益。

--------------------------------------------------------------------------------
8.9 Graph Search
--------------------------------------------------------------------------------

搜索必须始终可访问。

支持：

Node Title
Node Type
Node ID
Relevant Metadata

搜索结果点击后：

1. 选中正确节点
2. 展开必要的隐藏祖先
3. 处理当前分支过滤
4. 将节点滚动/缩放到可见位置
5. 显示节点详情

不能仅修改 selected ID，
但节点仍然在画布外。

--------------------------------------------------------------------------------
8.10 Large Graph Performance
--------------------------------------------------------------------------------

使用实际可重复的测试数据评估：

100 nodes
500 nodes
1000 nodes

这些为性能测试规模，不要求伪造生产数据。

检查：

Render Time
Interaction Latency
Dragging
Zoom
Filtering
Selection
Memory
Event Subscription Count

优化：

Memoization
Stable References
Controlled State
Viewport Rendering
Incremental Updates
Expensive Layout Computation

不得牺牲正确性换取速度。

================================================================================
9. NODE INSPECTOR REDESIGN
================================================================================

节点详情应该采用上下文驱动的布局。

建议分类：

Overview
Inputs & Dependencies
Execution
Results & Evidence
History
Advanced

具体 Tab 根据节点类型动态显示。

--------------------------------------------------------------------------------
9.1 Overview
--------------------------------------------------------------------------------

显示：

Node Title
Node Type
Instructions
Current Status
Research Status
Description
Relevant Metadata

允许编辑真实可修改字段。

--------------------------------------------------------------------------------
9.2 Inputs & Dependencies
--------------------------------------------------------------------------------

显示：

Required Inputs
Execution Dependencies
Upstream Nodes
Referenced Resources
Input Bindings

提供跳转到相关节点的能力。

不能将所有关系简单展示成一大段 JSON。

--------------------------------------------------------------------------------
9.3 Execution
--------------------------------------------------------------------------------

显示：

Execution Mode
Relevant Configuration
Run Action
Last Run
Current Run

高级执行配置按需展开。

--------------------------------------------------------------------------------
9.4 Results & Evidence
--------------------------------------------------------------------------------

显示：

Run Results
Metrics
Artifacts
Evidence Status
Verification Status
Provenance

必须能跳转到真实来源。

--------------------------------------------------------------------------------
9.5 History
--------------------------------------------------------------------------------

显示：

Revision
Previous Runs
Relevant Modifications
Relevant Human Actions

如果没有历史数据：

明确显示无历史记录。

--------------------------------------------------------------------------------
9.6 Advanced
--------------------------------------------------------------------------------

保留：

Raw JSON
Advanced Config
Technical Metadata

普通编辑器不能因为隐藏 Raw JSON，
就丢失未知配置字段。

================================================================================
10. RESEARCH PROGRESS REDESIGN
================================================================================

现在的实时进度信息需要重新整理。

首页只重点显示：

Current Research Activity
Needs Attention
Recent Meaningful Changes
Next Planned Work

完整进度页面可以包含：

Now
Attention
Blocked
Recent
Next
Evidence
History

但默认不应展示六块空区域。

--------------------------------------------------------------------------------
10.1 Live Progress
--------------------------------------------------------------------------------

继续使用真实进度系统。

显示：

Current Status
Current Work
Updated Time
Pending Items
Blocking Errors

不要伪造百分比。

如果底层没有可靠的进度数值：

使用阶段状态或不确定进度指示器。

--------------------------------------------------------------------------------
10.2 Needs Attention
--------------------------------------------------------------------------------

聚合：

Human Decisions
Failed Runs
Blocked Operations
Missing Inputs
Connection Problems
Budget Issues
Other Actionable States

只展示真实检测到的问题。

每一项必须有具体动作。

例如：

Review Decision
Open Failed Run
Fix Provider
Inspect Evidence

点击后定位准确实体。

--------------------------------------------------------------------------------
10.3 Activity Timeline
--------------------------------------------------------------------------------

将相关真实事件整理为时间线。

例如：

Run Started
Run Completed
Evidence Recorded
Node Updated
Human Instruction Accepted
Decision Applied
Manuscript Updated

时间来自真实事件记录。

不同事件需要保留来源 ID。

不能只做前端临时日志。

================================================================================
11. HUMAN INTERVENTION UX
================================================================================

这是 FOREST 的核心竞争能力。

必须重点优化。

--------------------------------------------------------------------------------
11.1 Separate Three Types of Human Input
--------------------------------------------------------------------------------

严格区分：

A. Comment / Annotation
B. Research Suggestion
C. Formal Instruction

Comment：

普通备注。

不自动修改科研执行。

Research Suggestion：

向系统提出研究建议。

根据现有 API 创建相应任务或建议。

Formal Instruction：

通过正式指令机制影响研究执行。

必须保留：

Scope
Target
Boundary
Request ID
Acceptance
Application
Consumption
Effects

不能将三者混为一个发送按钮。

--------------------------------------------------------------------------------
11.2 Formal Instruction UI
--------------------------------------------------------------------------------

提供自然语言输入区域。

允许选择：

Current Node
Current Branch
Entire Project

具体 Scope 以实际 API 支持为准。

用户必须清楚知道：

这条指令将影响什么。

何时被系统采用。

采用后是否需要等待下一次执行。

--------------------------------------------------------------------------------
11.3 Instruction Lifecycle
--------------------------------------------------------------------------------

清楚区分：

Draft
Submitted
Accepted
Applied
Consumed
Rejected
Failed

仅在服务端真实提供对应状态时显示。

不能自行推断不存在的阶段。

例如：

Instruction Accepted

不能显示：

Agent has followed your instruction

除非存在对应执行证据。

--------------------------------------------------------------------------------
11.4 Pending Human Decisions
--------------------------------------------------------------------------------

Pending Decision 必须醒目但不过度打扰。

允许用户：

Inspect Proposed Action
Accept
Edit
Reject
Provide Reason

必须展示：

Proposed Action
Affected Run
Relevant Context
Consequences
Current Status

确认后：

反馈真实服务端状态。

如果还需要 Resume：

提供明确的 Resume 按钮。

--------------------------------------------------------------------------------
11.5 Intervention History
--------------------------------------------------------------------------------

允许查看：

Previous Instructions
Decision History
Actual Effects
Relevant Runs
Timestamps

所有链接定位到真实对象。

保留完整历史分页。

不能因为 UI 简化而删除审计记录。

================================================================================
12. RUN CONTROL AND EXECUTION EXPERIENCE
================================================================================

将 Run 系统打造为成熟的执行控制界面。

--------------------------------------------------------------------------------
12.1 Separate Execution Scopes
--------------------------------------------------------------------------------

必须区分：

Run Node
Run Selected Nodes
Run Branch
Start / Pause Research Controller
Pause / Resume / Cancel Specific Run
Project-Level Bulk Controls

不同操作不能仅使用相同的 Play/Pause 图标而不说明作用域。

--------------------------------------------------------------------------------
12.2 Run Submission
--------------------------------------------------------------------------------

点击运行：

1. 检查必要条件
2. 发起真实请求
3. 获取真实 Run ID
4. 显示提交结果
5. 自动定位此 Run
6. 订阅/查询后续状态
7. 显示真实结果或错误

如果提交失败：

不要显示 Task Queued。

如果提交成功但 worker 失败：

必须将失败显示在对应 Run。

--------------------------------------------------------------------------------
12.3 Run States
--------------------------------------------------------------------------------

根据后端支持显示：

Queued
Running
Waiting
Waiting for Input
Pausing
Paused
Completed
Failed
Cancelled
Budget Exhausted

以及其他真实状态。

必须区分：

Run Status
Controller Status
Research Status

--------------------------------------------------------------------------------
12.4 Run Drawer
--------------------------------------------------------------------------------

底部运行区域默认收起。

用户启动任务时可以自动打开。

提供：

Run List
Current Run
Logs
Terminal
Metrics
Artifacts
Evidence
Errors

只有真实支持的内容才显示对应 Tab。

--------------------------------------------------------------------------------
12.5 Run History
--------------------------------------------------------------------------------

支持：

Search
Filter
Sort
Select
Open Run
Deep Link

运行记录必须按真实 ID 定位。

历史运行的结果不能被当前状态覆盖。

--------------------------------------------------------------------------------
12.6 Pause / Resume / Cancel
--------------------------------------------------------------------------------

验证每项操作：

作用范围正确。
服务端状态真正变化。
UI 状态正确刷新。
失败能够恢复。
重复点击不会产生错误副作用。

项目级批量取消必须展示：

成功数量
失败数量
仍然活动的任务

不能 Promise.all 中部分失败，
却显示全部完成。

--------------------------------------------------------------------------------
12.7 Model Connection
--------------------------------------------------------------------------------

明确区分：

Connected
Checking
Not Configured
Authentication Required
Connection Failed
Provider Unavailable

不能将尚未验证的连接显示成 Connected。

如果未配置模型：

展示明确的配置入口。

不要让 Run 按钮失败后才告诉用户没有模型。

================================================================================
13. EXPERIMENTS & RESULTS REDESIGN
================================================================================

实验系统必须既美观又严谨。

内部二级导航：

Experiments
Runs
Data
Figures

--------------------------------------------------------------------------------
13.1 Experiments
--------------------------------------------------------------------------------

显示：

Experiment Name
Related Node
Status
Latest Run
Key Metrics
Actions

支持现有：

Create
Edit
Run
Inspect
Compare
Retry
Export

这些操作是否存在由当前实现决定。

不能凭空添加未实现的能力而标记完成。

--------------------------------------------------------------------------------
13.2 Data
--------------------------------------------------------------------------------

数据页面应优先展示：

Data Sources
Tables
Metrics
Comparisons
Analysis Results

要求：

数字易读。
列宽合理。
支持横向溢出。
支持长字段查看。
没有多余卡片。
状态和数值含义清楚。

不得修改真实实验数值。

不得四舍五入后覆盖原始数据。

--------------------------------------------------------------------------------
13.3 Figures
--------------------------------------------------------------------------------

图表工作台建议：

Left / Main:
Figure Canvas

Right:
Figure Properties

显示：

Source
Caption
Style
Relevant Data
Revision
Export

保留现有：

Figure Selection
Editing
Code
Preview
Export
Evidence Binding

不能为了美化而破坏现有图形选择器和预览逻辑。

--------------------------------------------------------------------------------
13.4 Data / Figure Provenance
--------------------------------------------------------------------------------

每一个科研图表应尽量提供：

Source Run
Source Data
Relevant Metrics
Evidence Reference

但必须由真实数据支持。

不能给所有图表统一标记 Verified。

================================================================================
14. MANUSCRIPT EXPERIENCE
================================================================================

将论文系统升级为真正的实时科研成果工作区。

--------------------------------------------------------------------------------
14.1 Unified Manuscript State
--------------------------------------------------------------------------------

侧边论文面板与独立论文页必须共享：

Document Identity
Current Revision
Draft State
Save State
Selection
Compilation State

不得出现：

侧边修改一次
完整页面又是一份旧稿

的情况。

--------------------------------------------------------------------------------
14.2 Manuscript Side Panel
--------------------------------------------------------------------------------

用于：

Reading
Quick Review
Current Content
Evidence Navigation
Open Full Editor

不需要始终展示全部编译设置。

--------------------------------------------------------------------------------
14.3 Full Manuscript Editor
--------------------------------------------------------------------------------

完整页面提供：

Source Editor
Rich Preview / PDF
Figures
Tables
Evidence Links
Validation
Compile
Export

继续复用当前支持的编辑器。

不要以静态 Markdown 预览替代现有 Manuscript 功能。

--------------------------------------------------------------------------------
14.4 Paper Layout
--------------------------------------------------------------------------------

将模板和版面配置放在：

Layout Settings

不应在每次打开论文时占据大量空间。

保留：

Template Selection
Layout Configuration
Compilation Controls
Validation Settings

--------------------------------------------------------------------------------
14.5 Scientific Evidence
--------------------------------------------------------------------------------

明确区分：

Claim Exists
Claim Linked
Evidence Present
Evidence Verified
Evidence Stale
Evidence Missing

仅显示真实服务端能够证明的状态。

如果检测到失效证据：

允许用户定位受影响内容。

不能静默移除历史引用。

--------------------------------------------------------------------------------
14.6 Compilation
--------------------------------------------------------------------------------

用户点击 Compile 后：

展示真实编译任务状态。

成功：

展示真实结果。

失败：

展示具体错误和相关日志。

不能因为 API 返回一个任务 ID，
就立即显示 Compilation Successful。

--------------------------------------------------------------------------------
14.7 Export
--------------------------------------------------------------------------------

导出必须下载真实文件。

测试：

文件存在
大小合理
类型正确
内容对应当前选择
版本正确

不能点击后无响应。

================================================================================
15. LIBRARY, THEORY, IDEAS AND FILES
================================================================================

这些页面不能只是统一换 CSS。

需要重新整理操作逻辑。

--------------------------------------------------------------------------------
15.1 Library
--------------------------------------------------------------------------------

优化：

Literature Search
Source Selection
Reference Details
Import
Existing Record Editing

确保：

搜索结果可点击。
记录保存后存在。
刷新后可恢复。
来源引用可追溯。

--------------------------------------------------------------------------------
15.2 Ideas
--------------------------------------------------------------------------------

建议展示：

Idea Title
Description
Relevant Research Goal
Related Nodes
Status
Actions

如果 idea 是独立记录实体：

保持与 Research Node 的区别。

不得直接合并数据库对象。

--------------------------------------------------------------------------------
15.3 Theory
--------------------------------------------------------------------------------

保留：

Theory Editing
Mathematical Content
Formula Rendering
Existing Theory Operations

如果存在专用理论编辑器：

保留对应能力。

--------------------------------------------------------------------------------
15.4 Files
--------------------------------------------------------------------------------

优化文件树。

支持现有：

File Selection
Preview
Upload
Download
Editing
Source Navigation

明确区分：

File
Directory
Generated Artifact
Evidence Source

如果用户从证据引用跳转：

自动定位具体文件。

文件不存在：

显示真实错误。

不能悄悄打开文件列表首页。

================================================================================
16. SETTINGS AND CONFIGURATION
================================================================================

当前应用中部分高级配置对普通用户过于直接。

重新设计设置结构。

建议分组：

General
Model Providers
Execution
Budgets
Appearance
Advanced

实际功能以当前代码为准。

--------------------------------------------------------------------------------
16.1 Model Provider Settings
--------------------------------------------------------------------------------

显示：

Provider Name
Model
Connection Status
Authentication Status
Relevant Configuration

不要显示明文 Secret。

不要将仅保存配置解释成连接成功。

--------------------------------------------------------------------------------
16.2 Execution Settings
--------------------------------------------------------------------------------

常用配置：

使用表单。

高级配置：

使用折叠区域。

保留原始字段和扩展配置。

--------------------------------------------------------------------------------
16.3 Budget Settings
--------------------------------------------------------------------------------

使用结构化输入替代默认大块 JSON。

明确字段单位：

Seconds
Minutes
USD
Requests
Other Limits

只有 API 支持的字段才允许提交。

展示实际允许范围。

不能自行改变研究预算语义。

--------------------------------------------------------------------------------
16.4 Appearance
--------------------------------------------------------------------------------

支持：

Light
Dark
System Preference

可选：

Reduced Transparency

保留已有语言设置。

所有设置刷新后应恢复。

================================================================================
17. COMMAND PALETTE
================================================================================

升级现有 Cmd/Ctrl + K。

目标参考：

Raycast
Linear
现代 AI 产品的命令搜索

支持：

Page Navigation
Project Navigation
Node Navigation
Run Navigation
Available Commands

只显示真实可执行的命令。

支持：

Keyboard Selection
Enter Execute
Escape Close
Recent Commands

对于有破坏性的操作：

仍然需要独立确认。

命令列表不能只是静态页面链接集合。

但也不能添加只有名称、没有执行逻辑的命令。

================================================================================
18. GLOBAL STATE AND DATA ARCHITECTURE
================================================================================

检查目前：

useLoad
Local State
Project Events
Refresh Events
Polling
SSE
Mutation Handling

优化数据同步。

--------------------------------------------------------------------------------
18.1 Shared Project State
--------------------------------------------------------------------------------

建立清楚的 Project Context 边界。

避免多个组件同时对相同项目做不必要的完整请求。

但禁止把所有业务状态无差别放进一个巨大 Context，
导致整个应用每次事件都重新渲染。

--------------------------------------------------------------------------------
18.2 Entity Identity
--------------------------------------------------------------------------------

所有实体必须严格绑定：

Project ID
Node ID
Branch ID
Run ID
Document ID
Relevant Revision

不同项目之间不得串数据。

--------------------------------------------------------------------------------
18.3 Mutation Handling
--------------------------------------------------------------------------------

保留：

Request ID
Expected Revision
Conflict Handling
Idempotency
Retry Semantics

HTTP 409：

显示冲突处理。

HTTP 401：

提供认证恢复。

HTTP 403：

显示无权限。

HTTP 404：

显示对象不存在。

HTTP 500：

显示真实服务错误。

不得将所有错误压缩成一条通用 toast。

--------------------------------------------------------------------------------
18.4 Unsaved Draft Protection
--------------------------------------------------------------------------------

用户编辑内容时：

实时更新不能直接覆盖草稿。

需要区分：

Server State
Local Draft
Dirty State
Pending Save
Conflict

项目切换时：

提示未保存内容。

或者使用已经存在且可靠的草稿保存机制。

不允许静默丢失编辑。

--------------------------------------------------------------------------------
18.5 Reconnection
--------------------------------------------------------------------------------

断开实时连接后：

显示状态。

重连后：

刷新必要对象。

不能重复执行已提交操作。

不能重复创建 Run。

不能因为旧事件到达覆盖新版本。

--------------------------------------------------------------------------------
18.6 Request Race Conditions
--------------------------------------------------------------------------------

测试：

快速切换 Project A → B → A
连续选择不同节点
连续点击 Run
连续打开不同论文
切换分支同时收到 Graph Update
多个事件乱序到达
Network Retry

确保旧请求不会污染当前页面。

================================================================================
19. COMPLETE INTERACTION CONSISTENCY
================================================================================

整个系统建立统一 Action Semantics。

例如：

Create
Edit
Save
Cancel
Delete
Archive
Restore
Run
Pause
Resume
Retry
Export
Inspect
Compare

相同动作必须具有一致的行为。

--------------------------------------------------------------------------------
19.1 Save
--------------------------------------------------------------------------------

保存成功：

明确反馈。

保存失败：

保留草稿。

保存中：

避免重复提交。

--------------------------------------------------------------------------------
19.2 Delete
--------------------------------------------------------------------------------

删除前说明影响。

需要时使用现有 Impact Preview。

成功后：

刷新相关状态。

失败后：

不要让对象从 UI 中永久消失。

--------------------------------------------------------------------------------
19.3 Retry
--------------------------------------------------------------------------------

必须确认 Retry 使用什么配置。

如果存在历史配置与当前配置差异：

清楚显示。

不能错误地标记旧配置为当前配置。

--------------------------------------------------------------------------------
19.4 Cancel
--------------------------------------------------------------------------------

取消操作必须：

作用于正确任务。

请求失败可恢复。

显示真实取消状态。

不允许只让前端进度条消失。

--------------------------------------------------------------------------------
19.5 Navigation
--------------------------------------------------------------------------------

切换页面后：

相关实体选择尽量保留。

浏览器返回：

回到正确上下文。

刷新：

恢复重要 URL 状态。

================================================================================
20. RESPONSIVE DESIGN
================================================================================

测试以下宽度：

390px
768px
1024px
1280px
1440px
1920px

同时验证 200% Zoom。

--------------------------------------------------------------------------------
20.1 Desktop
--------------------------------------------------------------------------------

优先保证：

Workspace + Manuscript

能够并排工作。

--------------------------------------------------------------------------------
20.2 Medium Width
--------------------------------------------------------------------------------

自动收起非核心面板。

Paper 改为可展开 Drawer。

保持 Graph 可用面积。

--------------------------------------------------------------------------------
20.3 Mobile
--------------------------------------------------------------------------------

Sidebar 改为可访问抽屉。

主内容一次显示一个工作区域。

Graph 支持触摸操作。

关键功能可以通过菜单打开。

不能因窄屏直接删除重要功能。

--------------------------------------------------------------------------------
20.4 No Hidden Functionality
--------------------------------------------------------------------------------

禁止简单使用：

display:none

使核心功能在某些宽度永久无法访问。

应该将这些功能迁移到：

Overflow Menu
Drawer
Context Menu
Responsive Toolbar

================================================================================
21. ACCESSIBILITY
================================================================================

目标达到 WCAG 2.2 AA 相关要求。

需要验证：

Keyboard Navigation
Focus Visibility
Screen Reader Labels
Accessible Dialog
Focus Restoration
Color Contrast
Reduced Motion
Reduced Transparency

--------------------------------------------------------------------------------
21.1 Keyboard
--------------------------------------------------------------------------------

所有常用按钮必须可键盘访问。

Dropdown、Modal、Drawer 不能困住用户。

Escape 按当前层级关闭。

--------------------------------------------------------------------------------
21.2 Focus
--------------------------------------------------------------------------------

显示清楚的 Focus Ring。

打开 Modal：

焦点进入 Modal。

关闭 Modal：

恢复原位置。

--------------------------------------------------------------------------------
21.3 Status
--------------------------------------------------------------------------------

不能仅靠绿色、红色区分状态。

必须同时使用：

Text
Icon
Accessible Label

--------------------------------------------------------------------------------
21.4 Touch
--------------------------------------------------------------------------------

移动端重要点击目标保持足够尺寸。

不要用 12px 的小图标作为唯一操作入口。

================================================================================
22. PERFORMANCE AND RENDERING
================================================================================

优化前端性能，但不能破坏科研功能。

重点检查：

Graph Rerender
Repeated API Requests
Repeated SSE Subscriptions
Large Tables
Large Logs
Large Manuscripts
PDF Rendering
Heavy Editors

要求：

1. 避免不必要的全页面重渲染
2. 避免每个节点独立重复请求相同项目数据
3. 避免拖拽节点时产生过量网络请求
4. 保留位置保存可靠性
5. 控制高频事件更新
6. 避免布局抖动
7. 避免切换视图时丢失编辑草稿
8. 避免不可见重组件持续执行昂贵渲染

需要使用真实测试数据测量。

不要为了声称性能提升而编造毫秒数。

================================================================================
23. FRONTEND / BACKEND FUNCTIONAL INTEGRATION
================================================================================

这是硬性验收重点。

为每个重要 UI Action 建立：

UI Action
Component
Service Call
Endpoint
Request
Response
State Update
Persistence
Error
Test

需要建立一套：

UI-to-API Coverage Matrix

输出：

docs/ui-redesign/api-coverage.md

不能有：

Clickable UI with no action
Successful toast with failed operation
Wrong project ID
Wrong run ID
Wrong branch ID
Wrong revision
Stale data presented as current

--------------------------------------------------------------------------------
23.1 Async Actions
--------------------------------------------------------------------------------

明确区分：

Request Submitted
Task Accepted
Task Started
Task Running
Task Finished
Task Verified

这些状态不能混合。

--------------------------------------------------------------------------------
23.2 Evidence
--------------------------------------------------------------------------------

对于科研产物：

检查：

Artifact ID
Run ID
Source Reference
Evidence Status
Revision

不能仅因为存在 Figure 或 Manuscript 文件就视为已验证。

--------------------------------------------------------------------------------
23.3 Downloads
--------------------------------------------------------------------------------

导出操作必须实际下载文件。

验证：

HTTP Response
Content Type
File Presence
File Size
Expected Content

--------------------------------------------------------------------------------
23.4 Authentication / Authorization
--------------------------------------------------------------------------------

保留已有认证行为。

没有权限时：

不能显示具有误导性的可操作按钮。

Read-only Share：

不能执行写操作。

不要把 API Secret 输出到浏览器日志。

================================================================================
24. TEST STRATEGY
================================================================================

必须完成：

Unit Tests
Component Tests
Integration Tests
Browser E2E Tests
Real Backend Smoke Tests

测试使用隔离环境。

不影响用户真实科研数据。

--------------------------------------------------------------------------------
24.1 Unit Tests
--------------------------------------------------------------------------------

重点覆盖：

Routing
State Transitions
Graph Command Mapping
Selection
Query Parsing
Draft Preservation
Error Handling
Status Mapping
Action Availability

--------------------------------------------------------------------------------
24.2 Component Tests
--------------------------------------------------------------------------------

重点覆盖：

Sidebar
ProjectSwitcher
CommandPalette
Workspace
NodeInspector
RunDrawer
ManuscriptPanel
Modal
ContextMenu
Settings

--------------------------------------------------------------------------------
24.3 End-to-End Tests
--------------------------------------------------------------------------------

必须设计并执行以下场景。

TEST 01:
Create project → open workspace

TEST 02:
Create node → reload → node persists

TEST 03:
Edit node → save → reload → changes persist

TEST 04:
Select node → Escape → selection clears

TEST 05:
Graph drag → save → reload → position persists

TEST 06:
Add dependency → graph updates

TEST 07:
Invalid dependency cycle → rejected correctly

TEST 08:
Undo / Redo works

TEST 09:
Branch filtering persists correctly

TEST 10:
Deep link selects correct branch

TEST 11:
Deep link selects correct node

TEST 12:
Graph search reveals hidden node

TEST 13:
Run node → real run ID returned

TEST 14:
Run appears in run drawer

TEST 15:
Run status follows backend

TEST 16:
Run logs display actual output

TEST 17:
Pause / Resume reflects real result

TEST 18:
Cancel affects correct run

TEST 19:
Repeated submission does not cause unintended duplicates

TEST 20:
Run history survives reload

TEST 21:
Human instruction submitted

TEST 22:
Instruction receipt appears

TEST 23:
Instruction status accurately reflects actual lifecycle

TEST 24:
Pending decision can be opened

TEST 25:
Decision can be accepted / edited / rejected as supported

TEST 26:
Decision deep link opens correct run

TEST 27:
Evidence link opens correct source

TEST 28:
Missing evidence is not labeled verified

TEST 29:
Experiment results display actual values

TEST 30:
Figure displays actual artifact

TEST 31:
Figure source can be inspected

TEST 32:
Paper opens correct document

TEST 33:
Paper draft survives view switch

TEST 34:
Paper draft survives background refresh

TEST 35:
Paper save persists actual content

TEST 36:
Paper compile reports real status

TEST 37:
Paper export downloads actual file

TEST 38:
File upload persists

TEST 39:
File download works

TEST 40:
Project switching does not mix data

TEST 41:
Browser back/forward restores context

TEST 42:
Authentication required state works

TEST 43:
403 renders read-only/permission state

TEST 44:
404 renders meaningful missing-object state

TEST 45:
409 conflict preserves user changes

TEST 46:
500 error does not produce false success

TEST 47:
SSE reconnect restores accurate data

TEST 48:
Rapid project switching ignores stale responses

TEST 49:
Dark theme remains functional

TEST 50:
Light theme remains functional

TEST 51:
Mobile navigation remains accessible

TEST 52:
Keyboard navigation works

TEST 53:
Modal restores focus

TEST 54:
Advanced configuration retains unknown fields

TEST 55:
Large graph remains usable

TEST 56:
Real research run produces an actual artifact

根据代码增加其他关键用例。

这些测试中的具体动作必须依据当前 API 能力实现。

不支持的动作不能用 mock 成功冒充。

--------------------------------------------------------------------------------
24.4 Full Scientific Workflow E2E
--------------------------------------------------------------------------------

至少完成一条真实科研工作流：

Create Project
↓
Define Goal
↓
Create Research Node
↓
Configure Real Execution
↓
Submit Run
↓
Wait for Real Completion
↓
Inspect Actual Run Logs
↓
Inspect Actual Artifact
↓
Inspect Evidence Source
↓
Open Related Result
↓
Persist / Export
↓
Reload Application
↓
Verify Artifact and Provenance Persist

禁止使用伪造科研数据冒充验证完成。

如果没有可用的外部模型：

优先使用现有支持的无付费本地执行方式。

不允许擅自调用付费服务。

无法执行的测试标记：

NOT RUN

并说明具体缺失条件。

--------------------------------------------------------------------------------
24.5 Visual Regression
--------------------------------------------------------------------------------

在修改之前：

保存关键页面截图。

在修改之后：

保存对应截图。

覆盖：

Empty Project
Active Project
Running Research
Pending Decision
Failed Run
Graph with Many Nodes
Paper Editor
Figures
Files
Settings
Light Theme
Dark Theme

截图必须来自真实渲染应用。

禁止只用生成图片代替运行截图。

--------------------------------------------------------------------------------
24.6 Browser Console
--------------------------------------------------------------------------------

检查：

JavaScript Errors
Unhandled Promise Rejections
React Warnings
Failed Network Requests
Missing Assets
Hydration / Render Warnings
Accessibility Errors

不能把控制台错误忽略后宣称完成。

================================================================================
25. REAL USER JOURNEY VALIDATION
================================================================================

从真实用户视角重新检查每个主要任务。

用户第一次使用 FOREST：

是否知道创建什么？

创建项目后：

是否知道下一步？

选中节点后：

是否知道如何编辑、运行、查看结果？

任务运行后：

是否知道它在做什么？

任务失败后：

是否知道去哪里修复？

Agent 请求人工确认后：

是否能够快速处理？

实验完成后：

是否能够查看真实数值和文件？

论文生成后：

是否知道哪些内容有证据支持？

下载论文时：

是否得到真实文件？

这些问题需要从浏览器实际操作回答。

不能仅根据组件代码猜测用户体验良好。

================================================================================
26. IMPLEMENTATION ARCHITECTURE
================================================================================

推荐按职责整理前端目录。

以下仅为目标结构示例，不要求机械照搬：

apps/web/src/

app/
  AppShell
  Routing
  Providers

components/
  ui/
  navigation/
  feedback/
  layout/

features/
  projects/
  workspace/
  graph/
  nodes/
  runs/
  interventions/
  library/
  experiments/
  data/
  figures/
  manuscripts/
  files/
  settings/

shared/
  api/
  hooks/
  state/
  types/
  utils/

styles/
  tokens
  themes
  layout
  components

要求：

尽量采用渐进式迁移。

避免一次性重写全部业务逻辑。

旧接口可暂时通过适配层维持。

完成后清理确认未使用的旧代码。

不要留下两个并行、互相冲突的 UI 系统。

================================================================================
27. EXECUTION PHASES
================================================================================

按照以下顺序连续推进。

不要完成 Phase 1 后就停止，只交设计文档。

--------------------------------------------------------------------------------
PHASE 0 — Baseline
--------------------------------------------------------------------------------

读取代码和开发约束。

记录当前 Commit。

检查工作区状态。

运行现有测试。

保存关键页面截图。

建立功能地图。

--------------------------------------------------------------------------------
PHASE 1 — Functional Reliability
--------------------------------------------------------------------------------

优先修复：

Broken Routes
Incorrect Deep Links
Wrong Entity IDs
Stale Selection
Draft Overwrite
False Success States

保证基础交互可靠。

--------------------------------------------------------------------------------
PHASE 2 — Design System
--------------------------------------------------------------------------------

实现：

Theme Tokens
Frosted Glass
Typography
Spacing
Buttons
Inputs
Dialogs
Menus
Badges
Status Components

保证现有功能继续可用。

--------------------------------------------------------------------------------
PHASE 3 — Application Shell
--------------------------------------------------------------------------------

重构：

Sidebar
Project Switcher
Topbar
Navigation
Command Palette
Status Bar
Responsive Layout

保留旧路由兼容。

--------------------------------------------------------------------------------
PHASE 4 — Workspace
--------------------------------------------------------------------------------

重构：

Graph Layout
Contextual Actions
Node Inspector
Focus Mode
Human Intervention Entry
Run Drawer
Manuscript Side Panel

这是最高优先级的完整页面重构。

--------------------------------------------------------------------------------
PHASE 5 — Remaining Product Pages
--------------------------------------------------------------------------------

重构：

Library
Ideas
Theory
Experiments
Runs
Data
Figures
Paper
Files
Settings

按新信息架构整合。

--------------------------------------------------------------------------------
PHASE 6 — Architecture Hardening
--------------------------------------------------------------------------------

整理：

Shared State
API State
Draft Management
Subscriptions
Error Handling
Component Boundaries
Performance

删除已经确认废弃的重复代码。

--------------------------------------------------------------------------------
PHASE 7 — End-to-End Verification
--------------------------------------------------------------------------------

运行完整测试。

真实浏览器交互。

真实服务端烟测。

视觉回归。

多尺寸验证。

修复发现的问题。

--------------------------------------------------------------------------------
PHASE 8 — Final Audit
--------------------------------------------------------------------------------

检查：

Feature Parity
UI Consistency
Broken Buttons
Broken Routes
State Persistence
Error Cases
Accessibility
Performance
Scientific Correctness

输出完整报告。

================================================================================
28. HARD ACCEPTANCE CRITERIA
================================================================================

只有满足以下条件才可以宣称 UI 重构达到交付标准。

A. Functional Preservation

所有原有功能存在明确的新入口。

没有未解释的功能删除。

B. Real Integration

关键按钮连接真实 API 或真实本地状态操作。

没有生产用 Mock。

C. Correct Persistence

重要数据刷新后仍然正确。

D. Correct Identity

不会使用错误的：

Project ID
Node ID
Branch ID
Run ID
Document ID

E. Correct Research Semantics

执行完成不等于科研验证完成。

指令接收不等于执行生效。

F. Reliable Error Handling

失败不会被显示成成功。

G. Visual Quality

核心页面统一、美观、没有明显拥挤或布局错乱。

H. Responsive Behavior

指定尺寸下关键功能可访问。

I. Tests

适用测试实际通过。

未运行的测试必须明确标记。

J. No Regression

不能通过删除测试、跳过测试、修改预期来掩盖功能回归。

K. No Data Damage

原有用户数据、运行和科研产物得到保护。

L. Honest Completion Report

没有证据的项目不能标记 PASS。

================================================================================
29. REQUIRED DELIVERABLES
================================================================================

最终必须提交：

1. 完整的前端实现代码

2. 新 Design System

3. 新导航和信息架构

4. 经过整理的 Workspace

5. 完整功能对照表

6. UI-to-API Coverage Matrix

7. E2E Tests

8. Visual Regression Screenshots

9. Functional Verification Report

10. Remaining Limitations

建议文档：

docs/ui-redesign/
  audit.md
  component-inventory.md
  feature-inventory.md
  information-architecture.md
  design-system.md
  interaction-spec.md
  api-coverage.md
  test-matrix.md
  visual-comparison.md
  final-report.md

文档必须反映已经实际完成的工作。

不能先写一个完美的最终报告，
再让代码处于未完成状态。

================================================================================
30. FINAL REPORT FORMAT
================================================================================

完成后输出：

SECTION 1:
Executive Summary

SECTION 2:
Before / After UI Changes

SECTION 3:
Navigation Changes

SECTION 4:
Major Component Refactoring

SECTION 5:
Functional Bugs Fixed

SECTION 6:
Feature Preservation Matrix

SECTION 7:
API Integration Verification

SECTION 8:
Real E2E Results

SECTION 9:
Visual Regression Results

SECTION 10:
Accessibility Results

SECTION 11:
Performance Results

SECTION 12:
Remaining Issues

SECTION 13:
Files Changed

SECTION 14:
Commits

SECTION 15:
Final Delivery Status

每个测试至少提供：

Test Name
Result
Execution Method
Evidence
Relevant Logs
Failure Explanation（如果失败）

禁止：

未运行就标记 PASS。
只因为 TypeScript 编译通过就声称功能正常。
只因为截图好看就声称 UI 已完成。
只因为 API 返回 200 就声称科研任务完成。

================================================================================
31. EXECUTION DIRECTIVE
================================================================================

现在立即开始。

首先：

1. 检查 Git 状态和仓库最新结构。
2. 阅读项目约束。
3. 建立功能及组件清单。
4. 运行基线测试。
5. 审查真实前端行为。
6. 确认核心问题。
7. 创建实施计划。
8. 直接开始代码修改。

不要只停留在分析阶段。

完成每个阶段后：

运行相关测试。
修复失败。
保持应用可运行。
继续下一阶段。

允许根据真实代码调整实现细节。

允许发现并修复最初未列出的重要 UX 缺陷。

允许改进组件架构。

允许重新设计布局。

允许统一交互模型。

但是：

不能擅自删除业务能力。
不能破坏科研数据。
不能修改科研结果。
不能伪造验证状态。
不能为了减少工作量跳过核心功能接线。

最终目标：

FOREST 应当成为一款：

Beautiful
Intuitive
Minimal
Scientific
Reliable
Fully Functional
Professional

的 AI Scientific Research Workspace。

用户不应该看到一个由大量技术模块组成的管理后台。

用户应该看到一个能够自然开展科研、
观察 Agent 工作、
干预科研过程、
查看可信实验、
编辑论文并交付成果的统一工作环境。

Implement the complete redesign.

Do not stop at documentation.

Do not stop at visual changes.

Do not stop at passing component tests.

Complete the implementation and verify real user workflows. 禁止添加AI式功能宣传语。 ADDITIONAL REQUIREMENT — PRESERVE FOREST'S SCIENTIFIC WORKFLOW

FOREST 的核心不是聊天。

不要将系统重新设计成一个以 Chat History 为中心的普通 AI Assistant。

需要保留 Research Graph 的长期科研状态表达能力。

Human Interaction 可以采用自然语言输入，但必须区分：

- 普通对话
- 研究建议
- 正式研究指令
- 人工决策
- Graph Mutation
- Execution Request

用户输入不是简单的 Chat Message。

其可能具有作用范围、采用边界、持续状态、
科研目标影响、证据关联和执行后果。

这些语义必须完整保留。

Research Graph 是可交互的科研结构，
不是聊天消息的附属可视化。

Manuscript 是持续演化的科研成果，
不是一次性生成的文本。

Experiment 是具有真实运行状态和可追溯产物的科研操作，
不是聊天窗口中模拟的进度条。

设计必须将这些特性作为产品优势展示，
但不要把全部内部复杂度暴露给普通用户。ADDITIONAL REQUIREMENT — REAL INTERACTION SIMPLIFICATION

不能简单地把所有现有按钮移动到一个巨大的 More Menu，
然后宣称完成了 UI 简化。

必须重新分析操作频率、操作上下文和用户任务。

高频操作：

应在当前上下文直接可见。

中频操作：

应通过对象上下文菜单或清晰的二级入口访问。

低频、高级操作：

才允许放入 Advanced 或 More Menu。

必须优化完成实际任务所需要的操作步骤。

对主要用户旅程记录：

Before Click Count
After Click Count

Before Context Switch Count
After Context Switch Count

Before Required User Decisions
After Required User Decisions

对于没有必要增加步骤的常用操作，
重构后不应该变得更加繁琐。

视觉简洁必须与操作简洁同时成立。ADDITIONAL REQUIREMENT — ITERATIVE VISUAL QUALITY REVIEW

不能只进行一次 CSS 修改就结束视觉设计。

每完成一个主要页面：

1. 启动真实应用。
2. 使用 Playwright 打开页面。
3. 捕获实际截图。
4. 检查视觉层级。
5. 检查面板比例。
6. 检查文字大小。
7. 检查留白。
8. 检查对齐。
9. 检查颜色。
10. 检查玻璃透明层的可读性。
11. 检查操作按钮是否符合直觉。
12. 检查视觉重复和多余组件。

重点寻找以下视觉缺陷：

- 页面看起来像后台管理系统
- 过多矩形卡片
- 过多嵌套边框
- 大量浅绿色背景
- 太多小字
- 所有区域视觉权重相同
- 空白没有明确目的
- 工具栏占据过多高度
- 无意义的标题重复
- 图标没有统一含义
- 面板比例不协调
- 关键按钮不突出
- 无关操作过度突出
- 信息密度不一致
- 浅色主题过于苍白
- 暗色主题过于灰暗
- 透明层过多
- Paper 与 Workspace 风格割裂

发现问题后：

继续调整代码，
重新截图，
再次检查。

至少对以下页面分别完成视觉审查：

Projects
Workspace
Graph
Node Focus
Run Details
Experiments
Data
Figures
Paper
Library
Files
Settings

同时检查：

Dark Theme
Light Theme
Empty State
Loading State
Error State
Running State
Pending Human Decision State

不要只检查一个拥有理想测试数据的页面。

不要用主观的“looks good”代替逐项检查。可以参考一些开源的ai skills来先写好codex agent的skills？建议用codex cli的gpt 6 luna medium接入项目来进行调试。2. 我最推荐参考的开源 Skills
筛选后，以下资源最适合 FOREST：
开源项目	重点能力	对 FOREST 的价值
Anthropic — frontend-design	高品质视觉设计、布局、排版	避免做成千篇一律的 AI 管理后台
Vercel — agent-skills	React 性能、组件组合、Web 设计规范	优化 React Flow 周围的组件和 UI 工程质量
Addy Osmani — agent-skills	前端工程、浏览器调试、测试驱动开发	把“好看”落实为可维护、可验证的代码
OpenAI — skills	Codex Skills 的标准实践	正确组织和调用项目 Skills
PyModel — react-frontend-skills	React、Vitest、Playwright、特性模块架构	供技术栈与测试流程设计参考
其中，我最建议优先深入研究 Anthropic 的 frontend-design、Vercel 的 vercel-react-best-practices 和 Addy Osmani 的 frontend-ui-engineering。
Anthropic 的设计 Skill 特别重视有明确产品身份的视觉设计，而非模板化的“AI 风格”。Addy Osmani 的技能集则包含浏览器调试、增量实现、API 设计及故障恢复等工程流程。GitHub
不过，需要注意：这些都是通用 Skills。
FOREST 必须拥有自己的 Skills，才能理解科学研究状态、人工干预、运行生命周期、Evidence Gate 和 Research Graph。
3. 我建议创建七个 FOREST 专用 Skills

FOREST/
│
├── AGENTS.md
│
└── .agents/
    └── skills/
        │
        ├── forest-product-ux/
        │   └── SKILL.md
        │
        ├── forest-visual-design/
        │   └── SKILL.md
        │
        ├── forest-frontend-architecture/
        │   └── SKILL.md
        │
        ├── forest-research-graph/
        │   └── SKILL.md
        │
        ├── forest-run-lifecycle/
        │   └── SKILL.md
        │
        ├── forest-evidence-integrity/
        │   └── SKILL.md
        │
        └── forest-browser-testing/
            └── SKILL.md
Skill	具体职责	调用时机
forest-product-ux	导航、信息架构、操作流程、减少多余布局	设计或修改页面时
forest-visual-design	磨砂玻璃、配色、排版、动效、视觉一致性	修改样式与组件时
forest-frontend-architecture	React 状态、API、路由、草稿、组件结构	修改组件与业务接线时
forest-research-graph	节点、依赖边、分支、图命令、revision	修改 Workspace / Graph 时
forest-run-lifecycle	调度器、运行、取消、恢复、人工决策	修改运行控制时
forest-evidence-integrity	证据来源、科研状态、图表与论文关联	修改科研成果页面时
forest-browser-testing	Playwright、真实用户操作、视觉回归、接口验证	修改后验收时
先进行skills的构建，供后续6.1 sol ultra配置agent来进行任务。
