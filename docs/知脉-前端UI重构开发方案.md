# 知脉（Zhimai）前端 UI 重构开发方案（Agent 交接规范）

> 本文档为**可直接交接给任意开发 Agent 执行的独立 UI 重构规格书**。内容基于全网 19 款国内外简历产品/大模型调研、GitHub 高星开源标杆分析，以及本仓库 `frontend/src` 现状与全部离线/SSR 回归测试约束整理而成。执行时请严格遵守 §1 工程红线，按 §4 顺序分阶段实施并完成 §4 末的回归验收。

---

## §0 产品北极星、竞品调研与现状诊断

### 0.1 产品北极星
- **核心定位**：从零到一的**简历成长教练智能体**——「先聊出真实经历 → 沉淀可溯源的个人证据库 → 生成多岗位定制简历 → 通过透明 Diff 与教练批注学会怎么优化简历」。
- **核心设计原则**：克制、智识感、去焦虑、高可读性、AI 与教学信息分层清晰。

### 0.2 全网 19 款产品 + GitHub 开源标杆调研汇总

#### 国内简历产品（9 款）
| 产品 | 视觉风格与配色 | 布局与交互亮点 | 对「知脉」最值得借鉴 / 需规避 |
|---|---|---|---|
| **超级简历 WonderCV** | 极简商务风；低饱和商务蓝/深灰/墨绿，大量留白，强 ATS 专业信任感 | 左结构化表单 + 右毫秒级 A4 实时预览；内置要点写作 Tips 与一页纸智能排版 | **借鉴**：编辑与 A4 预览的克制专业感、写作引导提示；**规避**：传统纯表单的“空白填表焦虑” |
| **CocoCV / CodeCV** (`acmenlei/codecv`) | 极客清爽风；内容与样式彻底分离，纯白纸面配轻灰工作区 | 双模式编辑（Markdown / 所见即所得）+ 多版本复用 + 高清 PDF 导出 | **借鉴**：内容与版式解耦、多版本派生切换的轻快感 |
| **AiCV 简历王** | 现代 SaaS 卡片风；科技蓝/深灰 + 翠绿（通过）/ 柔橙（缺口警告） | 左模块导航 + 中表单/AI 润色快捷入口 + 右 A4 预览；诊断评分与 JD 匹配高亮 | **借鉴**：JD 关键词红绿命中反馈与 STAR 重塑的直达按钮 |
| **职管加** | 商务服务大厅与顾问看板风；稳重专业色系 | 重“陪伴感”与进度看板（诊断 → 定制 → 投递 → 面试复盘） | **借鉴**：把简历制作放进「求职成长路径」步骤感中，减少新手迷茫 |
| **至核简历** | 极简功能导向、去花哨装饰；数据化/指标化视觉 | 围绕「人岗匹配（JD Matching）」展开，突出 ATS 关键词覆盖率与 STAR 量化转化 | **借鉴**：直击痛点的 JD 关键词覆盖可视化，不做无意义的炫技图表 |
| **熊猫简历（PandaResume）** | 轻快清爽、低门槛大众风；响应式多端体验 | 左侧拖拽模块化编辑 + 右侧实时预览；内置 Panda Agent 润色/检测，一键换肤换模板 | **借鉴**：新手极低的上手门槛、移动端「编辑 / 预览」清晰切换 |
| **OfferLab** | 「求职作战中心」Dashboard 风格；模块化强对比 | 流式向导：上传/填写 → JD 拆解匹配度 → **改动前后对比（Added/Changed/Removed）** → 导出 | **借鉴**：**优化前后 Diff 对比视图**和 JD 拆解证据对照，与知脉“教你优化”高度契合 |
| **职徒简历（52cv）** | 国内成熟工具型 SaaS 风格；严谨蓝灰配色 | 顶栏模板/样式微调 + 左分块表单 + 右精细 A4 预览，行业范文与排版控件丰富 | **借鉴**：工具栏分区清晰、符合国内校招/社招审美的中文纸面排版 |
| **知遇（Magic Resume）** | 现代极客美学（Shadcn/Radix 风格、支持明暗双色、细腻动效） | 三栏工作台 + **AI 修改不全文覆盖，以 Diff 卡片逐条「采纳/跳过」** + 明确防编造边界 + 本地优先隐私感 | **最契合知脉！**「防编造护栏 + 逐条 Diff 审阅 + 极简现代工作台」 |

#### 海外 AI 简历 / 职业智能体（7 款）
| 产品 | 视觉风格与配色 | 布局与交互亮点 | 对「知脉」的启发 |
|---|---|---|---|
| **JRNEY.ai** | 极简数据清晰风；白底深字 + 红/黄/绿状态旗标 | 严格顺序流（Audit → Optimize → Export）；无旧简历时用**对话式 AI 访谈**挖成果，逐行审阅接受/拒绝 | **借鉴**：对话挖素材 → 诊断 → 逐行采纳的清晰闭环 |
| **Enhancv** | 设计驱动、高定制感；精选行业主题色板（藏青/青绿/经典灰） | 左可折叠工具栏 + 右大画幅实时预览；行内写作建议与指标检查提示 | **借鉴**：克制而高级的「语义主题色」与模块内嵌微提示（Inline Tips） |
| **CVlume** | 极简高效率风；干净卡片网格 | **多版本简历库仪表盘** + 分屏实时预览 + 一键切换 ATS 模板 | **借鉴**：把文字列表简历库升级为「可视化多版本卡片网格」 |
| **Refacet** | 现代设计师/极客风；支持**暗黑模式**，悬浮上下文工具栏 | **对话式面试官追问（“改变了什么？提升了多少？”）** → 单一主档案 → **AI 建议以「审批卡片（Approval Card）」形式显式确认** | **高度同构！** 直接对应知脉的「访谈 → `proposed` 事实审批卡 → 简历库」 |
| **Odyseek** | 温暖、去“冰冷 AI 味”的现代叙事风 | **“Value Story”对话引导** + **“Brag Book（成就证据本）”**独立页面持续记录成果与数据 | **借鉴**：把「长期记忆页」做成有温度、有成就感的「个人经历证据库」 |
| **Cruit（AskCruit）** | 命令中心（Command Hub）+ AI 侧边副驾驶 | 意图快捷指令入口 + 零排版烦恼的对话式主简历编辑 + 职业日志 | **借鉴**：首页用清晰的场景指令卡降低冷启动门槛 |
| **Resumation** | 冷静理性、强信任感；支持明暗主题切换 | **时间线式「职业证据库（Evidence Vault）」** + 确定性 JD 匹配分析 + 无干扰分步向导 | **借鉴**：用“透明、可溯源、确定性”的视觉语言缓解求职焦虑 |

#### 通用大模型网页端（3 款） & GitHub 开源标杆
- **豆包**：首屏强引导场景启动卡片，消除冷启动“不知道聊什么”的心理门槛。
- **通义千问**：弱化厚重气泡边界，采用现代文档流排版（表格/列表/引用清晰），左侧收敛历史会话。
- **DeepSeek**：极简克制高留白，**无边框宽幅 AI 回复 + 可折叠「思考过程（Thinking）」时间轴面板** + 极窄可折叠侧栏。
- **GitHub 高星标杆**：`reactive-resume/reactive-resume`（45.9k★ 多版本网格+三栏工作台）、`srbhr/Resume-Matcher`（29.4k★ JD 关键词命中/缺口可视化）、`JOYCEQL/magic-resume`（11.5k★ GenUI+Diff 审阅）、`xitanggg/open-resume`（9.0k★ 纯净 ATS 纸面渲染）、`Renovamen/oh-my-cv`（1.1k★ Vue 3 轻量工作台）。

### 0.3 当前前端 6 大核心痛点诊断
1. **视觉割裂（两套配色打架）**：`style.css` / `InterviewView.vue` 使用暖米纸 `#FAF7F2` + 深青 `#14525A` + 胶囊按钮 `999px`；而 `MemoryView.vue` / `ResumeLibraryView.vue` / `ResumeWizardView.vue` 硬编码冷灰 `#F4F5F7` + 亮蓝 `#2563EB` + 方形按钮 `8px`。
2. **导航栏简陋且有激活态 Bug**：`App.vue` 使用原始 Unicode 符号（`◈ ▤ ◉ ☰ 🗑`）；`#/resume/library` 页面在左侧主导航**没有高亮**（仅判断了 `resume` / `resumeV1`）。
3. **访谈页不够沉浸**：`.stream` 被写死 `max-height: 56vh`，大屏浪费近半高度；用户气泡大块实心深青、AI 消息被框在窄气泡内；底部只是单行 `<input>` 无法换行编辑长经历。
4. **“学会怎么优化简历”的核心卖点被埋没**：`ResumeLibraryView.vue` 将 `证据出处`、`重新生成`、`智能优化`、`长表单`、`优化改动 Diff` 全部纵向堆叠在左侧一栏，点完“智能优化”必须向下滚过几十个输入框才能看到对比与原因讲解。
5. **长期记忆页像“开发者后台”**：直接暴露 `skills.groups.0.items.0` 原始字段路径；待确认事实、回填、忘掉会话、清空全部危险按钮平铺堆叠。
6. **简历库列表信息粗糙**：缺少视觉化卡片网格与进度条，操作按钮与版本历史横排挤占编辑器顶部空间。

### 0.4 必须杜绝的反模式（Anti-Patterns）
1. **严禁色系割裂**：全站统一使用 Design Tokens，禁止散落硬编码色值。
2. **严禁滥用 AI 紫渐变与花哨动效**：求职场景重信任与专注，不堆砌霓虹渐变与发光边框。
3. **严禁超长单栏垂直瀑布**：编辑器左侧辅面板必须 Tab 化或抽屉化，让「AI 优化教学 Diff」居于核心视线。
4. **严禁污染 A4 纸面**：任何编辑器提示、出处引用、批注都不得侵入 `<ResumePreview>`。

---

## §1 硬性工程红线与自动化测试保护清单（EXECUTOR MUST OBEY）

本仓库包含基于**源码正则扫描**与 **Vue 3 SSR 隔离执行**的自动化回归测试。修改任何文件时**必须 100% 遵守**以下每一条约束，否则回归测试必失败：

### 1.1 `backend/resume_assets/` 与 `ResumePreview.vue` 隔离红线
- **禁止修改** `backend/resume_assets/render.mjs`、`backend/resume_assets/resume.css`、`backend/resume_assets/fonts.css`。
- **禁止修改** `<ResumePreview>` 的 props 契约：在 `ResumeLibraryView.vue` 和 `ResumeWizardView.vue` 中，`<ResumePreview>` **必须**保留 `:layout="layout"`，且**严禁**传入 `:gaps`、`:compare`、`:focus`、`:evidence`（见 `frontend/tests/resumeExport.test.mjs`）。
- **保持 v3.1 服务端 PDF 导出链路**（`exportResumePdf` / `exportDraftPdf`）：**严禁**在 `ResumeLibraryView.vue` 或 `ResumeWizardView.vue` 源码中出现 `downloadElementAsPdf` 或 `标绿` 字样（正则断言 `/标绿|downloadElementAsPdf/` 必须不匹配）。

### 1.2 `ResumeLibraryView.vue` 与 `ResumeWizardView.vue` SSR 测试红线
（参见 `frontend/tests/resumeExport.test.mjs` 第 772–1053 行）
1. **Tab 切换严禁使用 `v-if` 销毁受测 DOM**：左侧改为「内容编辑 / AI 优化教练 / 证据与定制」3 个 Tab 时，**必须使用 `v-show`（或 CSS `data-tab` 显隐）**，确保 SSR 默认渲染下 `<details class="evidence-panel">`、`<fieldset class="lib-fields">` / `<fieldset class="wiz-fields">`、`<button>放弃优化稿</button>` 始终存在于 DOM HTML 中。
2. **证据面板结构不可变**：
   - 必须在 `<aside>` 标签内部保留 `<details v-if="evidenceRows.length" class="evidence-panel">`；
   - **默认闭合**（不可出现 `open` 属性）；
   - `<summary>` 文案必须包含 `出处` 二字；
   - 内部禁止任何 `<a>`、`<script>`、`<img>` 标签或 `href=` 属性；
   - 组件 `<style scoped>` 必须保留 `@media print { ... .evidence-panel { display: none !important; } }`。
3. **按钮纯文本正则保护**：
   - 导出按钮必须保持纯文本内容（**`<button>` 标签内部不要嵌 `<svg>` 子元素**），确保匹配 `/<button[^>]*disabled[^>]*>\s*导出 PDF\s*<\/button>/`；
   - 放弃优化按钮必须保持 `<button ...>放弃优化稿</button>` 纯文本；
   - 保留 `中文经典`、`中文现代`、`编辑`、`预览` 文案与 `aria-pressed` 属性。
4. **表单绑定保护**：
   - `ResumeWizardView.vue` 中必须保持**恰好 2 个** `<ResumeFieldsForm ... />`（step 1 与 step 2），均使用 `:model-value="fields"` 与 `@update:model-value="onFieldsUpdate"`（禁用 `v-model`）；
   - 两页的 `<ResumeFieldsForm>` 均须保留 `:context="photoContext"`、`@photo-busy="onPhotoBusy"`，且 `:disabled` 属性表达式中**不得包含 `photoBusy`**。
5. **脚本顶层绑定与 import 保护**：`resumeExport.test.mjs` 的 `evaluate()` 会剥离 `import ... from "..."` 后运行 `<script setup>`，因此**不要**在 `<script setup>` 运行时逻辑里引用未在测试中 mock 的外部导入对象/组件（图标放在模板内联 `<svg>` 或独立纯 UI 视图中）。

### 1.3 `MemoryView.vue` 测试红线
（参见 `frontend/tests/v3Memory.test.mjs` 第 14–117 行）
1. `<script setup>` 中以下 **15 个标识符不可重命名或删除**：`boot`, `onRecover`, `onEnable`, `onDisable`, `togglePick`, `onBackfill`, `enabled`, `disabledMemory`, `status`, `facts`, `error`, `onboarding`, `recoveryCode`, `recoveryError`, `picked`。
2. 模板中 `v-if="onboarding"` 的源码位置**必须早于** `v-if="!booted"`（`indexOf('v-if="onboarding"') < indexOf('v-if="!booted"')`）。
3. 回填复选框必须保留精确属性表达式：`:disabled="backfilling || !hasSessionCredential(s.id)"`。

### 1.4 源码切片测试红线
（参见 `tests/review_frontend_regressions.mjs` 第 62–85 行）
1. `ResumeFieldsForm.vue` 中 `function removeEntry(` **必须继续紧邻位于 `const join` 之前**（测试用 `indexOf('function removeEntry(')` 到 `indexOf('const join')` 切片）。
2. `InterviewView.vue` 中 `async function boot()` **必须继续紧邻位于 `onMounted(boot)` 之前**（测试用 `indexOf('async function boot()')` 到 `indexOf('onMounted(boot)')` 切片）。

### 1.5 零新依赖 SVG 图标方案
- **不引入**任何第三方图标包或 UI 框架（`npm` 依赖零新增）。
- 新建轻量纯组件 `frontend/src/components/UiIcon.vue`（封装 18 个 `24×24`, `viewBox="0 0 24 24"`, `stroke="currentColor"`, `stroke-width="1.75"`, `fill="none"` 的 Lucide 风格线性图标），供 `App.vue`、`InterviewView.vue`、`SessionList.vue`、`ThinkingSteps.vue`、`ProfilePanel.vue`、`AdminView.vue`、`ChatView.vue`、`ResumeView.vue` 使用；
- 在受 `evaluate()` / SSR 隔离约束的 `ResumeLibraryView.vue`、`ResumeWizardView.vue`、`MemoryView.vue` 中，仅在非正则断言的装饰位置使用轻量内联 `<svg aria-hidden="true">`（避免注册组件引用导致测试 `evaluate` 报错）。

---

## §2 全站 Design System 规范（方案 A：青瓷智识 · Porcelain Studio）

### 2.1 完整 CSS Variables（直接替换写入 `frontend/src/style.css` 顶部）

所有页面（含各 `.vue` 的 `<style scoped>`）必须**全部使用下方语义 Token**，严禁硬编码散落色值。同时提供可选 `[data-theme="dark"]` 暗色变量表，默认浅色生效。

```css
:root {
  color-scheme: light;

  /* === 1. 表面与画布层级 === */
  --bg-canvas: #f7f8fa;          /* 全局主工作区底色 */
  --bg-sidebar: #f1f3f6;         /* 左侧主导航栏、会话历史栏底色 */
  --bg-surface: #ffffff;         /* 卡片、弹窗、表单容器、A4 纸面 */
  --bg-subtle: #f4f6f8;          /* 次级输入区、标签底色、表格表头、思考折叠块 */
  --bg-hover: #eaeef3;           /* 列表项/图标按钮悬停底色 */
  --bg-canvas-dots: #eceff4;     /* A4 预览画布底色 */
  --dot-color: #d1d7e2;          /* A4 预览画布点阵色 */

  /* === 2. 边框与分隔 === */
  --border: #e5e8ee;             /* 默认 1px 极细实线边框 */
  --border-strong: #cfd6e0;      /* 输入框/按钮默认与悬停边框 */
  --border-subtle: #eef0f4;      /* 卡片内部分割线 */

  /* === 3. 品牌主色（深靛墨蓝 · 大厂专业信任感） === */
  --brand: #1b365d;              /* 主按钮、核心标题、激活导航文字/图标 */
  --brand-hover: #132744;        /* 主按钮悬停 */
  --brand-soft: #eef3f9;         /* 激活导航底色、用户消息柔和底色 */
  --brand-border: #c5d5ea;       /* 品牌轻描边 */
  --brand-ring: rgba(27, 54, 93, 0.14);

  /* === 4. 智能体与证据色（玉石青 · AI 教练/证据溯源/优化增量） === */
  --agent: #0d7c66;              /* AI 教练徽章、覆盖率圆环、已确认状态、Diff 改后 */
  --agent-hover: #0a6351;
  --agent-soft: #ecfdf5;         /* 证据引用条底色、Diff 改后底色、命中芯片 */
  --agent-border: #a7f3d0;

  /* === 5. 教练批注与待补充（柔琥珀 · 教学讲解/缺口/stale） === */
  --coach: #b45309;              /* 教练思路标题、待确认/草稿/stale 状态 */
  --coach-soft: #fffbeb;         /* 教学批注卡底色、待补充输入框底色、stale 提醒条 */
  --coach-border: #fde68a;

  /* === 6. 错误与危险 === */
  --danger: #dc2626;
  --danger-hover: #b91c1c;
  --danger-soft: #fef2f2;
  --danger-border: #fecaca;

  /* === 7. 文字层级 === */
  --ink: #111827;                /* 主正文、标题 */
  --ink-secondary: #4b5563;      /* 副标题、辅助说明、表单标签 */
  --ink-muted: #9ca3af;          /* 时间戳、弱提示、占位符 */

  /* === 8. 旧变量语义兼容别名（防未改造片段失效） === */
  --paper: var(--bg-canvas);
  --surface: var(--bg-surface);
  --surface-warm: var(--bg-subtle);
  --line: var(--border);
  --teal: var(--brand);
  --hover-teal: var(--brand-soft);
  --accent: var(--agent);
  --accent-bg: var(--agent-soft);
  --accent-line: var(--agent-border);
  --accent-hover: #d1fae5;
  --good: var(--agent);
  --good-bg: var(--agent-soft);
  --warn: var(--coach);
  --warn-bg: var(--coach-soft);
  --warn-line: var(--coach-border);
  --err: var(--danger);
  --ink-soft: var(--ink-secondary);
  --mute: var(--ink-muted);
  --step-idle: #cbd5e1;

  /* === 9. 圆角、阴影、动效、布局尺码 === */
  --radius-xs: 6px;              /* 徽章、小按钮 */
  --radius-sm: 8px;              /* 常规按钮、输入框、下拉框 */
  --radius-md: 12px;             /* 卡片、面板、列表项 */
  --radius-lg: 16px;             /* 悬浮输入框 Dock、模态弹窗 */
  --radius-pill: 999px;          /* 胶囊筛选、状态药丸 */

  --shadow-xs: 0 1px 2px rgba(17, 24, 39, 0.04);
  --shadow-sm: 0 2px 8px rgba(17, 24, 39, 0.05), 0 1px 2px rgba(17, 24, 39, 0.03);
  --shadow-md: 0 8px 24px rgba(17, 24, 39, 0.08), 0 2px 6px rgba(17, 24, 39, 0.04);
  --shadow-paper: 0 10px 30px rgba(15, 23, 42, 0.10), 0 1px 3px rgba(15, 23, 42, 0.06);

  --ease: cubic-bezier(0.2, 0.8, 0.2, 1);
  --dur-fast: 150ms;
  --dur-base: 220ms;

  --sidebar-w: 216px;
  --sidebar-collapsed-w: 64px;
  --topbar-h: 52px;
  --col-side: 244px;
  --col-panel: 280px;

  font-family: "Inter", "PingFang SC", "Noto Sans SC", "Microsoft YaHei", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}

/* 可选暗色主题 Token（当根节点挂载 data-theme="dark" 时生效；A4 简历纸面保持纯白） */
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg-canvas: #0b0f17;
  --bg-sidebar: #111622;
  --bg-surface: #161c2b;
  --bg-subtle: #1d2436;
  --bg-hover: #252e42;
  --bg-canvas-dots: #0e131e;
  --dot-color: #242e44;
  --border: #252f42;
  --border-strong: #33415c;
  --border-subtle: #1e2638;
  --brand: #60a5fa;
  --brand-hover: #3b82f6;
  --brand-soft: rgba(96, 165, 250, 0.14);
  --brand-border: rgba(96, 165, 250, 0.35);
  --brand-ring: rgba(96, 165, 250, 0.24);
  --agent: #34d399;
  --agent-hover: #10b981;
  --agent-soft: rgba(52, 211, 153, 0.12);
  --agent-border: rgba(52, 211, 153, 0.32);
  --coach: #fbbf24;
  --coach-soft: rgba(251, 191, 36, 0.12);
  --coach-border: rgba(251, 191, 36, 0.32);
  --danger: #f87171;
  --danger-hover: #ef4444;
  --danger-soft: rgba(248, 113, 113, 0.12);
  --danger-border: rgba(248, 113, 113, 0.32);
  --ink: #f3f4f6;
  --ink-secondary: #9ca3af;
  --ink-muted: #6b7280;
}
```

### 2.2 字体与排印阶梯
| 层级 | 字号 | 字重 | 行高 | 用途 |
|---|---|---|---|---|
| Page Title | `20px` (`1.25rem`) | `650` | `1.3` | 页面主标题（岗位简历库、个人经历库） |
| Section Title | `15px` (`0.9375rem`) | `600` | `1.4` | 卡片、表单分区标题 |
| Body / Chat | `14.5px` (`0.906rem`) | `400` | `1.7` | 访谈对话流 Markdown 正文 |
| UI Control | `13.5px` (`0.844rem`) | `500` | `1.4` | 按钮、导航项、输入框、筛选芯片 |
| Caption / Badge | `12px` (`0.75rem`) | `500` | `1.35` | 状态徽章、时间戳、槽位副标 |
| Metric Number | `22–24px` | `700` | `1.2` | 覆盖率、事实数（启用 `font-variant-numeric: tabular-nums`） |

### 2.3 统一基础原子类与 18 个图标清单
在 `style.css` 中提供统一基础类（供所有页面复用）：
- **按钮规范**：统一高 `34px`（小按钮 `28px`）、`border-radius: var(--radius-sm)`（`8px`）、`font-size: 13.5px`、`font-weight: 500`、过渡 `var(--dur-fast) var(--ease)`：
  - `.btn-primary`：底 `var(--brand)`、字 `#fff`、hover `var(--brand-hover)`；
  - `.btn-agent`：底 `var(--agent)`、字 `#fff`、hover `var(--agent-hover)`（智能优化、采纳优化稿、一键生成）；
  - `.btn-secondary`：底 `var(--bg-surface)`、边框 `1px solid var(--border-strong)`、字 `var(--ink)`、hover `var(--bg-subtle)`；
  - `.btn-ghost`：透明底无边框、字 `var(--ink-secondary)`、hover `var(--bg-hover)`；
  - `.btn-danger`：底 `var(--bg-surface)`、边框 `1px solid var(--danger-border)`、字 `var(--danger)`、hover `var(--danger-soft)`。
- **教学型 Diff 卡 `.coach-diff-card`**：供 `ResumeLibraryView`、`ResumeWizardView`、`ResumeView` 共用：
  - 容器：`background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md); overflow: hidden; margin-bottom: 12px;`
  - `.diff-before`：`padding: 10px 12px; background: var(--danger-soft); border-left: 3px solid var(--danger); font-size: 13px; color: var(--ink-secondary); text-decoration: line-through;`
  - `.diff-after`：`padding: 10px 12px; background: var(--agent-soft); border-left: 3px solid var(--agent); font-size: 13.5px; color: var(--ink); font-weight: 500;`
  - `.diff-reason`：`padding: 9px 12px; background: var(--coach-soft); border-left: 3px solid var(--coach); font-size: 12.5px; color: var(--coach); line-height: 1.55;`（展示 `c.reason` 教练解析）
- **A4 点阵画布 `.paper-stage`**：
  - `background-color: var(--bg-canvas-dots); background-image: radial-gradient(var(--dot-color) 1px, transparent 1px); background-size: 16px 16px; border-radius: var(--radius-md); padding: 24px;`
  - 内部 `.resume-preview-frame { box-shadow: var(--shadow-paper); border-radius: 2px; }`
- **`UiIcon.vue` 18 个图标名清单**：
  `message`, `file-text`, `folder-kanban`, `database`, `shield`, `panel-left`, `plus`, `send`, `sparkles`, `download`, `copy`, `trash`, `history`, `check`, `x`, `alert`, `chevron-down`, `arrow-left`。

---

## §3 五大页面与组件像素级改造蓝图

### 3.1 全局工作台壳层（`frontend/src/App.vue`）

#### 桌面端线框（`> 768px`）
```text
+--------------------+---------------------------------------------------------------+
| [知] 知脉      [◁] |  顶栏 52px：页面主标题 · 轻副标       [🛡 隐私保护中 ⓘ]         |
|--------------------|---------------------------------------------------------------|
| [💬] 访谈挖掘       |                                                               |
| [📝] 简历向导       |                  主内容区 (flex: 1, min-width: 0)             |
| [📂] 简历库         |                  底色 var(--bg-canvas)                        |
| [🗂] 经历证据库     |                                                               |
|                    |                                                               |
|--------------------|                                                               |
| 成长闭环：          |                                                               |
| 聊经历→存证据→      |                                                               |
| 定制简历→学优化     |                                                               |
+--------------------+---------------------------------------------------------------+
  216px (折叠态 64px)
```

#### 详细规格
1. **可折叠左侧导航栏**：
   - 状态 `collapsed = ref(localStorage.getItem('zm_sidebar_collapsed') === '1')`，点击折叠按钮切换并持久化。
   - 展开态宽 `var(--sidebar-w)` (`216px`)，折叠态宽 `var(--sidebar-collapsed-w)` (`64px`)；底色 `var(--bg-sidebar)`，右边框 `1px solid var(--border)`。
   - **4 个一级入口**（用 `<UiIcon>` 替换 Unicode `◈ ▤ ◉`）：
     - `访谈挖掘` → `#/chat`（激活条件：`view === 'chat' || view === 'chatV1'`）
     - `简历向导` → `#/resume`（激活条件：`view === 'resume' || view === 'resumeV1'`）
     - `岗位简历库` → `#/resume/library`（激活条件：`view === 'resumeLibrary'` —— **修复旧版不亮 Bug**）
     - `经历证据库` → `#/memory`（激活条件：`view === 'memory'`）
   - 激活态：`background: var(--bg-surface); color: var(--brand); box-shadow: var(--shadow-xs);` + 左侧 `3px` 玉石青指示条 `var(--agent)`。
2. **52px 精简顶栏**：
   - 左侧：动态页标题（`访谈挖掘` / `简历向导` / `岗位简历库` / `个人经历库`）+ 移动端会话抽屉按钮 `☰ 会话`（仅 `view === 'chat'` 且 `<= 980px` 显示）；
   - 右侧：将旧版纯文本长隐私句改为可交互胶囊按钮 `<details class="tb-privacy-pop">`，默认仅展示 `🛡 隐私保护中`，点击展开轻量浮层说明（「对话 48 小时留存可删 · 长期记忆默认关闭 · 支持导出 JSON 与一键清空」）。
3. **移动端（`≤ 768px`）**：
   - 隐藏左侧竖栏，在底部固定显示 **4 等分 Tab Bar**（高 `56px + env(safe-area-inset-bottom)`，顶边框 `1px solid var(--border)`，背景 `rgba(255,255,255,0.92)` + `backdrop-filter: blur(10px)`）；
   - `.main-area` 底部预留 `padding-bottom: 76px`，保证 `375×812` 视口零遮挡与零横向滚动。

---

### 3.2 「从 0 到 1」智能访谈工作台
涉及：`InterviewView.vue`, `SessionList.vue`, `ThinkingSteps.vue`, `ProfilePanel.vue`

#### 桌面端三栏工作台线框（总高 `calc(100vh - 52px)`，三列各自内部滚动）
```text
+---------------+-------------------------------------------+---------------------+
| ＋ 新建访谈   |  [居中对话流 max-width: 760px, flex: 1]    |  素材收集进度       |
|               |                                           |   ╭────╮            |
| 历史会话      |  (新会话空态：4步路径条 + 破冰引导卡片)    |   │ 65%│ 已收集 4项  |
| ▎秋招后端     |                                           |   ╰────╯            |
|   访谈中 · 2m |  [知] ▸ 思考过程 · 3 步 (微底折叠卡)      |                     |
|   项目复盘    |  你好！可以先聊聊你最近做过最有成就感的   |  待补充 · 点击直聊   |
|   已完成 · 1d |  一个项目，我帮你把亮点一点点挖出来...    |  [+ 实习成果指标]   |
|               |                                           |  [+ 获奖经历]       |
|               |           ╭─────────────────────────────╮ |                     |
|               |           │ 我去年在字节做后端实习，用Go │ |  ┌───────────────┐  |
|               |           ╰─────────────────────────────╯ |  │ 一键生成简历→ │  |
|               |                                           |  └───────────────┘  |
|               |  [快捷追问芯片] [快捷追问芯片]            |                     |
|               |  ┌─────────────────────────────────────┐  |                     |
|               |  │ 多行自适应输入框 (Enter发送/Shift换行)│  |                     |
|               |  │                              [发送] │  |                     |
|               |  └─────────────────────────────────────┘  |                     |
+---------------+-------------------------------------------+---------------------+
     244px                      flex: 1                            280px
```

#### 详细改动规范
1. **`InterviewView.vue`**：
   - **取消 `.stream { max-height: 56vh }`**：`.iv-workbench` 高度为 `calc(100vh - var(--topbar-h))`，`.iv-main` 使用 `display: flex; flex-direction: column; height: 100%; min-height: 0;`，`.stream` 为 `flex: 1; min-height: 0; overflow-y: auto; padding: 20px 16px;`。
   - **源码切片红线**：保持 `async function boot()` 紧邻位于 `onMounted(boot)` 之前。
   - **首屏破冰 Hero**：当 `!items.length && !busy` 或 `stage === 'greeting'` 时，展示轻量 4 步导览条（`① 聊出经历 → ② 存为证据 → ③ 生成简历 → ④ 学会优化`）；`suggest-replies.hero` 改为左右双列卡片（白底 + 1px 边框 + 左侧淡青图标块 + 粗体标题 + `translateY(-2px)` hover 动效）。
   - **DeepSeek 式消息排版**：
     - **AI 回复**：去掉实线白色大边框气泡，改为无框宽幅流（左侧 `28×28` 圆角深靛徽标 `知` + 右侧最大宽 `760px` 正文区，Markdown 行高 `1.7`）；
     - **用户消息**：右对齐、最大宽度 `74%`、背景 `var(--brand-soft)`（`#eef3f9`）、文字 `var(--ink)`、圆角 `16px 16px 4px 16px`。
   - **悬浮多行输入 Dock**：
     - 将 `<input>` 替换为 `<textarea ref="inputEl" v-model="input" rows="1" ...>`，监听 `@input` 自动调整高度（最高 `140px`）；
     - 键盘处理 `@keydown.enter.exact.prevent="send()"`（`Shift + Enter` 自然换行）；
     - 外层容器：`background: var(--bg-surface); border: 1px solid var(--border-strong); border-radius: var(--radius-lg); box-shadow: var(--shadow-sm); padding: 10px 12px;`。
2. **`SessionList.vue`**：
   - 去掉列表项之间的 `border-bottom: 1px dashed`；改用 `gap: 4px` + 悬浮圆角块；
   - 激活项：背景 `var(--bg-surface)` + 左侧 `3px solid var(--agent)` + `--shadow-xs`；
   - 垃圾桶删除按钮默认 `opacity: 0`，仅在 `.sd-item:hover`、`.sd-item:focus-within` 及移动端（`@media (hover: none)`）显示（使用 `<UiIcon name="trash" />` 替换 `🗑`）。
3. **`ThinkingSteps.vue`**：
   - 包裹为 `background: var(--bg-subtle); border: 1px solid var(--border-subtle); border-radius: 10px; padding: 8px 12px; margin-bottom: 10px;`；
   - 左侧竖向时间轴线连接步骤圆点（`running` 态用 `var(--agent)` 脉冲，已完成用实心 `var(--agent)`）；
   - `tstep-reasoning` 摘要块采用左侧 `2px solid var(--agent)` + 浅青微底 `var(--agent-soft)`。
4. **`ProfilePanel.vue`**：
   - 覆盖率圆环改为真 SVG `<svg viewBox="0 0 64 64">` 双圆环（底轨 `var(--bg-subtle)`，进度弧 `var(--agent)`，`stroke-linecap="round"`，中心等宽数字 `pct%`）；
   - 「已收集」芯片使用 `var(--agent-soft)` + `var(--agent)`；「待补充」芯片使用 `var(--coach-soft)` + `var(--coach-border)`；
   - `pct >= 80` 时在面板底部渲染显著的玉石青转化卡按钮引导前往 `#/resume`。

---

### 3.3 「边改边学」岗位简历库 & 向导工作台
涉及：`ResumeLibraryView.vue`, `ResumeWizardView.vue`, `ResumeFieldsForm.vue`

#### A. 简历库列表页（`#/resume/library`，`!doc` 态）
- **顶部工具栏**：左为标题 `岗位简历库` + 数量胶囊；右为主按钮 `＋ 新建简历`（点击打开**居中 Modal 弹窗**创建，不再顶开列表）。
- **多版本卡片网格（`.lib-grid`）**：`grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px;`。
  - 每张卡片包含：顶部（岗位标签 + 状态胶囊 `draft` 柔琥珀 / `ready` 玉石青 / `stale` 橙黄） → 中部（粗体标题） → 底部（`5px` 线性完成度进度条 + `Math.round((Number.isFinite(d.completeness) ? d.completeness : 0) * 100)%` + 更新日期）。

#### B. 简历详情编辑页（`#/resume/library/:id`）线框与 specs
```text
+---------------------------------------------------------------------------------+
| ← 列表 | 后端开发-暑期实习 [draft] | 版式:[中文经典▾] | 已保存 | 历史版本(3)▾ | 导出 PDF |
+---------------------------------------------------------------------------------+
| [若有 stale：柔琥珀警告横条 + 跳转「证据与定制」重新生成]                          |
+------------------------------------+--------------------------------------------+
| [内容编辑] [AI 优化教练] [证据/定制] |   投递预览 · 中文经典                       |
|------------------------------------|                                            |
| (Tab 1 - v-show="editorTab==='edit'")|     ┌──────────────────────────────┐     |
|  [照片][基本][教育][实习][项目][技能] |     │                              │     |
|  结构化卡片表单 (缺口柔琥珀左条)    |     │      纯白 A4 投递纸面         │     |
|                                    |     │   (--shadow-paper 立体阴影)    │     |
| (Tab 2 - v-show="editorTab==='coach'")|    │                              │     |
|  [智能优化] + 各模块优化进度列表    |     │      <ResumePreview />       │     |
|  ┌──────────────────────────────┐  |     │   (纯正投递预览，严禁批注)     │     |
|  │ 改动明细 · 项目经历           │  |     │                              │     |
|  │ 原文：负责后端接口开发        │  |     └──────────────────────────────┘     |
|  │ 优化：主导 FastAPI 后端...    │  |                                            |
|  │ 优化思路：补充量化指标与...    │  |         (外围为点阵画布 .paper-stage)      |
|  └──────────────────────────────┘  |                                            |
|  [待补充占位引导卡] [采纳/放弃按钮] |                                            |
|                                    |                                            |
| (Tab 3 - v-show="editorTab==='evidence'")|                                       |
|  <details class="lib-generation">  |                                            |
|  <details class="evidence-panel">  |                                            |
+------------------------------------+--------------------------------------------+
          左侧控制台 450px–480px                  右侧吸顶 A4 画布 flex: 1
```

#### 详细改造规范（严守 §1.2 全部 SSR 测试红线！）
1. **56px 吸顶操作栏**：
   - 收拢 `← 列表`、标题、状态胶囊、`<select v-model="layout">`（含 `中文经典` / `中文现代`）、保存状态点、`<details class="lv-dropdown">`（收拢 10 版历史快照与 `恢复` 按钮）、`保存修改`、`复制为新稿`、`删除`、以及主按钮 `<button ...>导出 PDF</button>`（**纯文本，不嵌 `<svg>`**）。
2. **左侧 3-Tab 控制台（必须用 `v-show`，严禁用 `v-if`！）**：
   - 新增状态 `const editorTab = ref("edit");`（取值 `'edit' | 'coach' | 'evidence'`）。
   - **Tab 1 (`v-show="editorTab === 'edit'"`)**：顶部横向模块跳转锚点胶囊（点击 `scrollIntoView` 到对应 `[data-key]`）+ `<fieldset class="lib-fields">...<ResumeFieldsForm ... /></fieldset>`。
   - **Tab 2 (`v-show="editorTab === 'coach'"`)**：
     - 点击 `智能优化` 按钮触发 `onOptimize()` 时，自动设置 `editorTab.value = 'coach'`，立刻展示结果；
     - 优化采纳区（`v-if="optFields"`）：柔青卡片展示 `采纳优化稿` 与纯文本按钮 `<button ...>放弃优化稿</button>`；
     - 改动展示使用 `.coach-diff-card`（`.diff-before` 原文 → `.diff-after` 优化建议 → `.diff-reason` 教练思路 `c.reason`），并展示 `placeholders` 柔琥珀补齐提示卡。
   - **Tab 3 (`v-show="editorTab === 'evidence'"`)**：
     - 包含 `<details class="lib-generation">`（目标岗位/JD/保留手工修改/重新生成）与 `<details v-if="evidenceRows.length" class="evidence-panel">`（默认闭合、无 `open`、`<summary>` 含 `出处`）。
     - **检查点**：由于 3 个 Tab 均在 `<aside class="lib-editor">` 内使用 `v-show`，SSR 编译渲染时 `evidence-panel`、`lib-fields`、`放弃优化稿` 全部存在于 `<aside>` 源码树中，100% 通过 `resumeExport.test.mjs`。
3. **右侧吸顶 `.paper-stage`**：`position: sticky; top: 72px; max-height: calc(100vh - 90px); overflow: auto;`；外围采用点阵底布，纸面附加 `--shadow-paper`。
4. **移动端（`<= 760px`）**：保留顶部分段切换 `[aria-pressed="true"]` 的 `编辑` / `预览` 按钮（满足 `resumeExport.test.mjs:1044-1046`）。
5. **`ResumeWizardView.vue` 同步**：
   - 顶部 3 步导航升级为横向连接步骤指示条 + 右侧 `简历库 →`；
   - 第 3 步使用同一套 `.coach-diff-card` 教学卡片与 `.paper-stage` 立体 A4 画布；
   - 严守恰好 2 个 `<ResumeFieldsForm>`、显式 `:model-value="fields"` & `@update:model-value="onFieldsUpdate"`、`evidence-panel` 在 `<aside>` 内等全部红线。
6. **`ResumeFieldsForm.vue` 精修**：
   - 照片区升级为水平双栏卡（左 `60×80` 圆角证件照预览框 + 右上传说明与 `44px` 高按钮）；
   - `.rff-entry` 去掉虚线，改用 `background: var(--bg-subtle); border: 1px solid var(--border); border-radius: 10px; padding: 14px;`；
   - `.rff-field.gap` 使用 `var(--coach-border)` 与 `var(--coach-soft)`；
   - **保持 `function removeEntry(` 紧邻位于 `const join` 之前**。

---

### 3.4 个人经历证据库（`frontend/src/views/MemoryView.vue`）

#### 页面结构与规格（严守 §1.3 全部红线！）
1. **顶部 Header**：标题 `个人经历库 · 长期记忆` + 安全高频操作 `导出 JSON` + `<details class="mem-privacy-menu">`（将「停用记忆」与「清空全部（DELETE_ALL）」收敛进下拉隐私抽屉，防误触与缓解焦虑）。
2. **一次性凭据横幅（`v-if="onboarding"`）**：保持源码位置**严格早于 `v-if="!booted"`**；改用玉石青高亮保护卡片（`var(--agent-soft)` + `var(--agent-border)`）。
3. **4 格统计 KPI 卡（`.mem-kpis`）**：
   - 网格 `repeat(4, minmax(0, 1fr))`（`<= 640px` 改为 `repeat(2, 1fr)`）；
   - 展示：累计事实数 / 待确认数（柔琥珀高亮）/ 已确认可用数（玉石青高亮）/ 365 天保留状态。
4. **待确认审批区 + 筛选胶囊**：
   - 若 `status?.by_status?.proposed > 0`，置顶展示显眼柔琥珀审批条「有 N 条新挖掘事实待你拍板」+ `全部确认（N）` 按钮；
   - 状态筛选（全部 / 待确认 / 已确认 / 已拒绝）与折叠的「历史会话回填」（保留 `:disabled="backfilling || !hasSessionCredential(s.id)"`）、「来源会话忘掉」面板分列清晰。
5. **人类可读的事实卡片（`.mem-fact`）**：
   - 新增纯函数 `slotLabel(path)` 将原始 `slot_path` 映射为中文（如 `basics.name` → `基本信息 · 姓名`、`internships.0.company` → `实习经历 · 公司`、`skills.groups.0.items.0` → `专业技能`、`projects.0.bullets.0` → `项目经历 · 成果`），保留 `title` hover 提示原始路径；
   - `proposed` 态卡片左侧带 `3px solid var(--coach)` 提醒条；
   - 原文证据 `.mf-quote` 升级为 `background: var(--bg-subtle); border-left: 3px solid var(--agent); padding: 8px 12px; border-radius: 0 8px 8px 0;` 引文块。
6. **保留标识符清单**：`boot`, `onRecover`, `onEnable`, `onDisable`, `togglePick`, `onBackfill`, `enabled`, `disabledMemory`, `status`, `facts`, `error`, `onboarding`, `recoveryCode`, `recoveryError`, `picked` 一字不改。

---

### 3.5 v1 诊断页 / 问答页 / 管理后台收口
- **`ResumeView.vue`**：上传区、核对表单与诊断报告统一接入 CSS Variables；JD 命中/缺失芯片采用 `var(--agent-soft)` / `var(--coach-soft)`；改写对比沿用 `.coach-diff-card`。
- **`ChatView.vue`**：继承新版 `style.css` 无框流式对话、悬浮输入框与来源引用药丸样式。
- **`AdminView.vue`**：统计指标卡、检索栏与数据表迁移到 `--brand`、`--bg-surface`、`--border`、`--shadow-xs`。

---

## §4 分阶段任务单与完工验收标准

### 阶段拆分（每完成一阶段须运行 `npm run build`）
1. **Phase 1 — Design System 与全局壳层**：新建 `UiIcon.vue` → 重写 `style.css`（§2 全部 Tokens 与原子类）→ 重构 `App.vue`（可折叠侧栏 + 修复 `resumeLibrary` 高亮 + 52px 顶栏隐私胶囊 + 移动端底部 Tab）。
2. **Phase 2 — 访谈工作台重构**：重构 `InterviewView.vue`、`SessionList.vue`、`ThinkingSteps.vue`、`ProfilePanel.vue`（全高视口 + 首屏 4 步破冰卡 + 无框 AI 流 + R1 思考折叠卡 + 多行 Enter 发送 Dock）。
3. **Phase 3 — 简历库与向导大改**：重构 `ResumeLibraryView.vue`、`ResumeWizardView.vue`、`ResumeFieldsForm.vue`（卡片网格 + Modal 新建 + 吸顶精炼栏 + 左侧 `v-show` 3-Tab + 教学型 Diff 卡片 + 点阵立体 A4 画布）。
4. **Phase 4 — 经历证据库与辅助页统一**：重构 `MemoryView.vue`（KPI 卡 + 中文槽位映射 + 隐私抽屉 + 审批引文卡），同步更新 `ResumeView.vue`、`ChatView.vue`、`AdminView.vue`。
5. **Phase 5 — 全量回归与浏览器双视口验收**。

### 完工必须执行的自动化验收命令（全部绿灯方可通过）
```bash
# 1. 前端构建（0 报错）
cd frontend && npm run build

# 2. 前端全部 5 份单元测试（重点守护 SSR / 导出 / 隐私 / 渲染隔离）
node --test frontend/tests/kbSource.test.mjs \
            frontend/tests/resumeExport.test.mjs \
            frontend/tests/resumePhoto.test.mjs \
            frontend/tests/resumeRender.test.mjs \
            frontend/tests/v3Memory.test.mjs

# 3. 前端源码回归测试（SSE 边界、不可变删除、重试）
node tests/review_frontend_regressions.mjs

# 4. 后端全量测试（确保 328+ 项无回归）
python -m unittest discover -s tests -p "test_*.py"
```

### 浏览器双视口视觉与交互验收清单（`1440×900` & `375×812`）
- [ ] 全站零旧硬编码色值冲突（无 `#2563eb`、`#faf7f2`、`#f4f5f7` 残留打架）；
- [ ] 左侧主导航 4 入口（含 `#/resume/library`）高亮准确，折叠状态可记忆；`375×812` 下切换为底部固定 4-Tab 栏且 `document.documentElement.scrollWidth <= window.innerWidth`；
- [ ] 访谈页首屏显示 4 步导览与破冰卡，对话无框排版清晰，思考步骤可折叠，多行输入 `Enter` 发送 / `Shift+Enter` 换行正常；
- [ ] 简历库列表卡片完成度正常（无 `NaN%`），新建 Modal、编辑保存、历史版本下拉恢复、**AI 优化教练 Tab 教学 Diff 卡与采纳/放弃**、版式切换、服务端 PDF 导出全通过；
- [ ] 经历证据库槽位展示中文名称，同意弹窗、一次性凭据、全部确认、回填、忘掉会话、导出 JSON 与清空全部闭环无回归；
- [ ] A4 简历预览立体清晰，导出 PDF 纸面绝对干净，不含任何批注与原文出处。
