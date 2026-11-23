# 知脉 前端 UI 第二轮调研：大厂产品对标 + GitHub 可逆向参考

> 调研日期：2026-11-23 ｜ 基线：第一轮重构已落地（`--brand/--agent/--coach` 语义 token、`iv-workbench` 三栏、`paper-stage` 点阵画布、`coach-diff-card`、隐私胶囊均已生效）
> 上一轮《知脉-前端UI重构开发方案》调研了 19 款**简历工具**；本轮聚焦**互联网大厂的 AI 产品**（对话/Agent/文档协作类）——因为知脉的形态本质是「AI 对话驱动的工作台」，不是传统简历表单工具。

---

## 0. 结论先行

**单一重合度最高（≈70%）的大厂产品 = Google NotebookLM。**

知脉访谈页「会话列表｜消息流｜画像面板」与 NotebookLM「Sources｜Chat｜Studio」是同一个三栏心智模型——**输入资产 → AI 对话 → 产出物**。NotebookLM 官方设计文档把这个模型概括为 Inputs → Chat → Outputs，与知脉「访谈挖掘 → 素材沉淀 → 简历产物」完全同构。

但任何一个单品都不能 100% 覆盖知脉，第二轮最准确的参照组合是：

| 知脉页面/部件 | 最像的大厂产品 | 重合度 | 借什么 |
|---|---|---|---|
| 访谈页三栏骨架 | **NotebookLM** | ~75% | Studio 面板形态：产出磁贴 + 生成物列表；三栏可独立折叠 |
| 对话→简历产物的衔接 | **Claude Artifacts / ChatGPT Canvas** | ~65% | 「聊天收窄成列、产物占 2/3 屏」的展开动效与层级关系 |
| ThinkingSteps 思考流 | **Manus + DeepSeek** | ~60% | Manus 的步骤时间线打勾动画；DeepSeek 的「已思考」窄行 |
| Wizard 优化对比 | **智联求职 AI** | ~55% | 「修改前/修改后/为什么 HR 更青睐」三段式说服力排版 |
| 来源卡 | **Perplexity** | ~70% | 横排序号小卡 + 域名，已对齐，保持 |
| 简历库卡片网格 | **CVlume / Reactive Resume** | ~60% | 多版本卡片网格（第一轮已借） |
| 证据库页 | **Odyseek Brag Book / Resumation Evidence Vault** | ~55% | 时间线式成就记录（第一轮已借） |

> 反过来说：豆包/元宝/Kimi/通义这类**纯对话型大厂产品**与知脉重合只有 ~30%——它们没有「对话产生可编辑产物」的第二面，参考价值限于输入框、首屏引导、思考折叠这些零件级细节。

---

## 1. 大厂产品逐个拆解（UI 长什么样 + 值得抄什么）

### 1.1 NotebookLM（Google）—— 本轮主参照 ★

```
┌──────────┬───────────────────┬──────────────────┐
│ Sources  │  Chat（居中窄列）  │  Studio          │
│ 来源列表  │  带引用的问答      │  产出磁贴网格      │
│ 可收成    │  底部输入框        │  (Audio/Video/   │
│ 图标窄栏  │                   │   MindMap/Report) │
│          │                   │  ─生成物列表(含    │
│          │                   │   "生成中"占位条)  │
└──────────┴───────────────────┴──────────────────┘
```

- **三栏各自独立折叠**：左栏可收成纯图标窄轨；右 Studio 可整块收起让 Chat 居中。知脉 ProfilePanel 现在是「展开/窄条」，可以对齐这一档。
- **Studio 面板结构值得直接映射**：上半是彩色产出磁贴（点一下就生成），下半是已产出物列表 + 「生成中」的进度条目。→ 知脉画像面板的升级方向：**上 = 覆盖率环+已收集 chips；中 = 「一键生成简历」大磁贴 + 历史产出列表；下 = 待补充 chips（点击直聊）**。面板从"状态展示"变成"产出车间"。
- **"生成中"占位条目**：用户在等待时看到 Studio 里有一条转圈的项目——知脉 autofill/optimize 分段跑时同样该在面板里挂进度条目，而不是只在消息里。
- 移动端三栏变三个 Tab，Chat 为默认选中——与现有移动规则一致。

### 1.2 Claude Artifacts（Anthropic）——「对话→产物」衔接参照

- 产物出现时**右侧面板滑出占约 60–66% 屏宽**，对话被挤成窄列；面板顶部是「渲染版/源码」模式切换 + 返回按钮。
- 关键心智：**对话是方向盘，面板是工件**。每次对话轮次就地更新产物，不在消息流里重复打印全文。
- 对应知脉：Wizard ②「编辑预览」就是这个形态——但目前是表单+预览，缺的是**从访谈页一键跳过来时"产物诞生"的仪式感**（比如从右侧滑入、消息流收窄）。第二轮可考虑 `#/resume` 进入时的过渡动效与「这条简历来自会话 #x」的出处条。
- Artifacts Tab 聚合所有产出物 → 对应「岗位简历库」卡片网格的定位（已从 CVlume 借过，方向一致）。

### 1.3 ChatGPT Canvas（OpenAI）

- 与 Artifacts 同构，差异在：**双向编辑**（人可直接改，AI 做外科式局部改写）、选中段落出 mini 工具条、版本回退按钮 + 版本间 diff。
- 可借零件：Wizard ③优化时**原稿/优化稿版本切换 + diff 视图**（现有 `coach-diff-card` 已是雏形）；预览里点字段定位表单的"点击即编辑"（第一轮已列入）。

### 1.4 Manus —— 思考过程透明化参照

- 左 = 任务线程（文字叙述 + 可展开步骤），右 = "电脑视窗"直播 agent 在干什么；右下角任务步骤可展开成 checklist，完成一项打一个勾。
- 可借零件：**步骤 checklist 的打勾感**（已完成步骤 ✓ + 当前步脉冲 + 未来步灰点），以及**长任务不刷屏**——完成的轮次收成一行「用时 Xs · N 步」，只留结果。这正是 DeepSeek Harness 社区也在讨论改进的点（思考与工具调用分开折叠）。
- 知脉 ThinkingSteps 已是 details/summary + 状态点，方向对；第二轮微调：**结束轮默认折叠成「思考过程 · N 步 · Xs」**，运行中才展开当前步。

### 1.5 Perplexity

- 答案上方横排来源小卡（序号+标题+域名），答案内文内嵌 `[1]` 角标可跳源。知脉来源卡已对齐此形态，保持、把 hover 出摘要做稳即可。

### 1.6 DeepSeek / Kimi / 豆包 / 元宝 / 通义（国内对话型）

- **DeepSeek**：AI 回复无气泡边框、宽幅文档流；「已思考」= 一条可折叠窄行。知脉第一轮已对齐（无框宽幅回复）。
- **Kimi**：联网搜索结果在**右侧栏**铺开成可浏览的网页列表——"检索结果外置到侧栏"的思路，对应知脉未来若做来源浏览可用。
- **元宝**：国内公认最干净的壳；**对话分组**是元宝先发——SessionList 若会话多了可以按「访谈中/已完成」自动分组。
- **豆包**：首屏强场景启动卡——知脉开场双卡已用此思路。
- **通义**：弱化气泡、文档流排版、左栏收敛历史——已对齐。

### 1.7 文档+AI 协作类（WPS AI / 飞书 Aily / Notion AI）

- 共同形态：**主区是文档，AI 是右侧常驻侧边栏**；选中文段可就地"问 AI"，AI 改完以批注/diff 形式落在文档上。
- 对应知脉 Wizard ②③：A4 预览是主区，右栏表单/优化面板是"AI 侧"。**点预览字段→右栏定位**的交互（第一轮已列）正是这个模式；第二轮可再加**预览上悬停出「问教练」小按钮**的反方向通路。

### 1.8 求职垂类大厂/准大厂

- **智联求职 AI**（ai.zhaopin.com）：核心展示就是「修改前简历 → 修改后简历 → 为什么 HR 更青睐」三段对照——与知脉 `coach-diff-card`（before/after/reason）完全同构，**验证了我们的 diff 卡方向是大厂共识**；可借的是它把「岗位关键词」做成显性 chips 贴在改写结果上（对应知脉 evidence/quote 的可视化）。
- **LinkedIn AI 教练**：chatbot 教练 + 岗位匹配度评分，形态是弹窗式轻对话，结构参考价值一般；证明「AI 职业教练对话」是大厂认可的产品形态。
- **求职咖**（浏览器扩展，非大厂但形态极近）：聊天写简历 + 不编造承诺 + 本机存储 + GitHub 备份——几乎是知脉价值观的镜像，它的**明文隐私承诺放首屏**值得借。

---

## 2. GitHub 可逆向 / 可参考开源清单

> 分层：**A 级 = 结构可直接借鉴（含 Vue 栈能读码的）**；**B 级 = React 项目，读结构不读码**；**C 级 = 零件参考**。星数为 2026-11 快照，许可逐条核对过，标注 ⚠ 的有坑。
> 项目纪律不变：**只读结构/抄思路，代码一律重写为纯 CSS/Vue3**；凡借鉴进入 `docs/开源许可.md` 登记。

### A 级 · 三栏工作台/产物面板（本轮最相关）

| 仓库 | ★ | 许可 | 栈 | 抄什么 |
|---|---|---|---|---|
| `lfnovo/open-notebook` | 42.1k | MIT | Vue + FastAPI | **首选**。NotebookLM 开源复刻：Sources/Chat/Studio 三栏、栏可折叠成图标轨、Studio 生成物列表+生成中占位、移动端三栏→三 Tab。Vue 栈，可直接读码逆向。 |
| `LRriver/NotebookLM-Lite` | 小 | MIT | — | 同样三栏 workspace，量级小更好读。 |
| `stackblitz-labs/bolt.diy` | 20.1k | MIT | React(Remix) | Chat｜Workbench 分屏的滑动展开（framer-motion width 变体）、Code/Diff/Preview 顶部切换 slider、步骤可折叠。读结构。 |
| `vercel/ai-chatbot` | 21k | 源码开放 | Next.js | Claude artifacts 的官方作业：artifact 侧栏、版本 diff、suggestion 批注、文档内联编辑。artifact prompt 契约设计值得读。 |

### B 级 · Agent/思考过程

| 仓库 | ★ | 许可 | 抄什么 |
|---|---|---|---|
| `kortix-ai/suna`（原 Suna→Kortix） | 20.7k | ⚠ Elastic 2.0（非 OSI） | Manus 式：任务线程 + 右栏 computer 视图 + 步骤流。**只能看结构，license 不允许自由抄码**。 |
| `langgenius/dify` | 大厂级开源 | Apache-2.0 附加条款 | agent_thought 事件协议的源头（v2 已沿用）；其"思考过程"折叠呈现可对照 ThinkingSteps。 |
| `CherryHQ/cherry-studio` | 41k | Apache-2.0 | 桌面 AI 客户端，kimi/deepseek 思维链折叠适配的实现可参考。 |

### C 级 · 聊天壳/来源卡/生成式 UI

| 仓库 | ★ | 许可 | 栈 | 抄什么 |
|---|---|---|---|---|
| `ChatGPTNextWeb/NextChat` | 88.9k | MIT | React | 会话列表、mask/场景卡、移动端适配的经典实现。 |
| `lobehub/lobe-chat` | 83k | ⚠ LobeHub 社区许可（衍生商用需授权） | React | 最精致的聊天 UI 之一，**只能看不能抄**。 |
| `Chanzhaoyu/chatgpt-web` | 31.3k | MIT | **Vue3** | ChatGPT 像素级复刻的 Vue 版本，已 archive 但代码可逆向——气泡/列表/输入框的 Vue 写法直接可读。 |
| `chatgpt-web-dev/chatgpt-web` | 1.9k | MIT | **Vue3+Naive UI** | 上面的活跃 fork，维护中，多了联网搜索开关与深度思考折叠。 |
| `ConnectAI-E/ChatGPT-PerfectUI` | 340 | MIT | **Vue3+Tailwind** | 小体量像素复刻，读 UI 结构最省时间。 |
| `miurla/morphic` | 9.4k | Apache-2.0 | Next.js | Perplexity 式生成式 UI：来源卡、答案内嵌组件、流式 JSON spec。 |
| `ItzCrazyKns/Vane`（原 Perplexica） | 37.4k | MIT | Next.js | Perplexity 复刻：focus 模式切换、来源横卡。 |
| `zaidmukaddam/scira` | 11.9k | ⚠ AGPL-3.0 | Next.js | UI 漂亮可看，**AGPL 传染，别抄码**。 |
| `assistant-ui/assistant-ui` | 13.4k | MIT | React | 聊天原语库：Thread/Composer/ActionBar 的交互细节清单（自动滚动、重试、分支）可当 checklist 用。 |

### C 级 · 简历/文档（沿用第一轮，补许可标注）

| 仓库 | ★ | 许可 | 栈 | 抄什么 |
|---|---|---|---|---|
| `reactive-resume/reactive-resume` | 45.9k | MIT | React | 三栏 Builder + Write/Design/Check 三模式 + autosave/undo。 |
| `xitanggg/open-resume` | ~9k | MIT | React | ATS 单栏版式 + 「数据只在你浏览器」明文标语。第一轮已登记。 |
| `JOYCEQL/magic-resume` | 11.4k | ⚠ Apache-2.0 **附加非商用限制** | React(TanStack) | 中文 AI 简历：Diff 逐条采纳、防编造护栏、动效细腻。**个人用免费，SaaS/商用禁止——只能看思路**。 |
| `Renovamen/oh-my-cv` | 1.1k | ⚠ GPLv3 | **Vue3/Nuxt** | Markdown→A4 实时预览、自动分页、本机存储、暗色。**GPL 传染，读结构勿搬码**。 |
| `srbhr/Resume-Matcher` | ~29k | Apache-2.0 | — | JD 关键词命中/缺口可视化，对应智联式关键词 chips。 |

---

## 3. 第二轮 UI 改动候选（按性价比排序，待拍板）

> 原则不变：纯 CSS 零依赖、契约渲染、A4 纸面零污染、一屏一个主按钮、占位用琥珀不用红。

| # | 动作 | 参照 | 估 | 为什么现在做 |
|---|---|---|---|---|
| R1 | **ProfilePanel →「素材工作室」**：在现有覆盖率环之下加 Studio 式「产出磁贴区」（一键生成简历大卡 + autofill/optimize 进度条目 + 历史产出列表）；待补充 chips 保持可点直聊 | NotebookLM Studio | 1–1.5d | 让右栏从"仪表盘"变"车间"，补齐 Inputs→Chat→Outputs 心智的最后一块 |
| R2 | **ThinkingSteps 收尾折叠**：结束的轮次收成单行「思考过程 · N 步 · Xs」，仅运行中展开当前步；思考与检索/工具调用分行（别挤一坨） | DeepSeek + Manus + DSH 社区共识 | 0.5d | 长访谈不刷屏，纯展示层无风险 |
| R3 | **访谈→简历的产物衔接**：`card(goto_resume)` 跳转时右侧产物面板滑入动效 + 简历页顶部出处条「由访谈 · {会话标题} 生成」 | Claude Artifacts | 0.5–1d | 强化"对话方向盘、面板是工件"的叙事，低成本高感知 |
| R4 | **Wizard 预览反向通路**：预览字段 hover 出「问教练」按钮→跳回访谈并带入字段上下文 | WPS AI/Notion AI 双向 | 1d | 打通编辑卡壳时的回路，注意别污染 A4 打印面（hover 才出现、print 隐藏） |
| R5 | **SessionList 分组**：按 stage（访谈中/已完成）自动分组 + 折叠 | 腾讯元宝 | 0.5d | 会话一多就值回票价 |
| R6 | **optimize 结果贴关键词 chips**：diff 卡上方展示命中/缺失的岗位关键词（如有 JD 字段） | 智联求职 AI | 0.5d | 若 v3.x 已有 JD 字段则顺手，无则降级为 evidence chips |
| R7 | 收尾：`npx impeccable audit` 复查 | — | 0.5d | 与第一轮验收口径一致 |

**不建议本轮做**：暗色主题（token 已预留 `[data-theme="dark"]`，但求职工具用户 95% 浅色，先欠着）、拖拽排序（重型依赖）、Manus 式 live computer 视窗（知脉没有远程执行环境，徒有其表）。

---

## 4. 调研方法与来源

- 产品 UI 形态核对：NotebookLM 官方 redesign 公告 + 设计负责人 Jason Spielman 案例文（3-Panel / Inputs→Chat→Outputs 心智模型原文）；Claude 官方 artifacts 文档 + Interface Lineage；OpenAI Canvas 公告；Manus 实测拆解（uisdc/screenrove/mobbin 截图库）；国内五件套对比来自 UIED 竞品分析长文与个人深度测评。
- 求职垂类：智联求职 AI 官网（ai.zhaopin.com）、LinkedIn 官方产品公告、Microsoft Store「求职咖」扩展说明。
- GitHub 数据：各仓库主页 star/license 快照（2026-11），三方榜单（promptquorum/repoquarry/repoloot）交叉核对许可与维护状态。
