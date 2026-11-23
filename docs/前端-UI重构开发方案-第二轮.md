# 知脉 前端 UI 重构开发方案 · 第二轮（NotebookLM 式产出叙事）

> 依据：`docs/前端-UI调研-第二轮-大厂对标与开源参考.md`（2026-11-23）。基线：第一轮工作台壳已落地。
> 本轮目标一句话：**把右栏从"仪表盘"升级成"产出车间"，补齐 Inputs → Chat → Outputs 心智的最后一段。**
> 不变红线：纯 CSS 零依赖、契约驱动渲染、A4 纸面零污染、一屏一个主按钮、占位琥珀非红。

---

## 0. 范围与取舍

| # | 动作 | 参照 | 本轮 |
|---|---|---|---|
| R1 | ProfilePanel →「素材工作室」 | NotebookLM Studio | ✅ |
| R2 | ThinkingSteps 收尾折叠 + 耗时 | DeepSeek / Manus / DSH 共识 | ✅ |
| R3 | 访谈→简历产物衔接（出处条 + 入场） | Claude Artifacts | ✅ |
| R5 | SessionList 按阶段分组 | 腾讯元宝 | ✅ |
| R7 | impeccable detect 机械验收 | — | ✅ |
| R4 | 预览字段「问教练」反向通路 | WPS AI | ⏸ backlog（跨视图带上下文，收益待验证） |
| R6 | diff 卡贴 JD 关键词 chips | 智联求职 AI | ⏸ backlog（依赖 JD 字段落库） |

---

## R1 · ProfilePanel → 素材工作室（改动核心）

现状问题：右栏只展示状态（环/chips/缺口），「一键生成简历」CTA 在 coverage ≥80% 才出现——**产出动作被进度条绑架**，且没有"生成过什么"的踪迹。

NotebookLM Studio 形态映射（上半产出磁贴 + 下半生成物列表）：

```
┌─ 素材工作室 ───────────── [⇤] ┐   <- 头部 + 收起为窄轨按钮
│ ┌────┐ 已收集 · N 项          │
│ │65% │ [chip][chip][chip]     │
│ └────┘                        │
│ 待补充 · 点击直聊              │
│ [gap chip][gap chip]          │
├─ 产出 ────────────────────────┤   <- 新增分区
│ ┌──────────────────────────┐  │
│ │ ✦ 一键生成简历            →│  │   <- 产出磁贴（常显）
│ │   素材 65% · 先去试填也行  │  │      副文案随覆盖率变化
│ └──────────────────────────┘  │
│ ▸ 简历草稿 · 3 分钟前  去查看→│   <- 产出记录（localStorage 草稿）
│ ▸ 优化稿 · 昨天         去查看→│
└───────────────────────────────┘
```

### 规格

1. **分区结构**（`ProfilePanel.vue` 内部，不新增组件）：
   - 上部保持现有：环 + 已收集 chips + 待补充 chips + `pct===0` 空态提示。
   - 新增 `<div class="pp-studio">`：`<span class="pp-label">产出</span>` + 产出磁贴 + 产出记录列表。
2. **产出磁贴**（替换现有 `pp-cta`，常显，不再等 ≥80%）：
   - `<a href="#/resume">` 全宽卡，`agent` 实心（产出动作是本面板唯一主按钮）。
   - 标题固定「一键生成简历 →」；副文案按覆盖率分档：
     - `pct >= 80`：`素材已够，去生成 →`（同现状）
     - `40 <= pct < 80`：`素材 ${pct}%，可先试填再回来补`
     - `pct < 40 || null`：`先聊聊更好，也可直接去试填`
   - 点击前写 `sessionStorage` 出处标记（见 R3）。
3. **产出记录**：读 `localStorage` 两个草稿 key（`resume_draft_v1` / `resume_draft_optimized`，用 `storage.js` 的 `loadDraft`/`DRAFT_KEY`/`OPT_DRAFT_KEY`），取 `saved_at` 显示相对时间（复用 SessionList 的 `fmtTime` 逻辑，抽成 `utils/time.js` 两处共用）：
   - `简历草稿` → `#/resume`；`优化稿` → `#/resume`。
   - 无草稿时该行不渲染（不显示空列表标题）。
   - 面板每次挂载时读一次（InterviewView 切走即销毁，返回时自然刷新；不做轮询）。
4. **窄轨折叠**：面板右上角 `⇤` 按钮（UiIcon `panel-left`，旋转 180° 或新增 `panel-right`），点击切 `rail` 态：宽 56px，只留竖排环（SVG 同元件缩小至 44px）+ `素材` 二字竖排；再点恢复。状态存 `localStorage zm_pp_rail`。移动端（≤980px）禁用 rail。
5. **文案收束**：删现有 `pp-hint` 两条（副文案已承担引导），`pct===0` 的空态提示保留但并入"产出"区上方。
6. 样式全部走既有 token：`--agent` 系产出、`--coach` 系缺口；磁贴 hover `translateY(-1px)` + `--shadow-sm`（与 hero 卡同语言）。

### 数据流

```
chat SSE profile 事件 → coverage/gaps/updates（不变）
localStorage drafts   → studioEntries  [{key, label, savedAt, href}]
sessionStorage        → zm_resume_from（R3 出处标记，点击磁贴时写）
```

---

## R2 · ThinkingSteps 收尾折叠

现状：`<details :open="live">` 结束后已自动折叠，但 (a) 流式期间无标题行，(b) 无耗时，(c) detail 折行撑高。

1. **流式中给标题**：live 时也渲染 `<summary>`，文案 `正在思考…`（脉冲点 + `· {{steps.length}} 步`），details 保持 `open`。
2. **结束文案带耗时**：`思考过程 · N 步 · Xs`。耗时在 InterviewView 量：首个 `step_start` 记 `turn.thinkStart = Date.now()`，`done`/`error` 时 `turn.thinkSecs = round((now-start)/100)/10`；props 传 `:elapsed`，有值才显示 `· Xs`。恢复的历史消息 steps 无耗时（不显示，ok）。
3. **detail 单行截断**：`.tstep-detail` 加 `flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;` + `:title="s.detail"` 悬停看全文。
4. 其余（时间线、状态点、kind pill）不动。

---

## R3 · 访谈 → 简历产物衔接（Artifacts 式"产物诞生"）

1. **出处标记**：`sessionStorage.zm_resume_from = JSON.stringify({sid, t})`。
   - 写入点 1：`InterviewView.onCard(goto_resume)` → 写 `{sid: sessionId.value, t: Date.now()}`。
   - 写入点 2：`ProfilePanel` 产出磁贴 click → emit 事件给父级写（或直接写，组件内 import storage 无妨——为少改父级，组件内写，sid 用 `getCurrentSessionId()`）。
   - 10 分钟外的标记忽略（防陈旧）。
2. **出处条**（`ResumeWizardView.vue`，挂在 `.wizard` 顶部、步骤条之上）：
   - `v-if="originFrom"` 显示 `.wz-origin`：`<UiIcon sparkles> 由访谈素材一键填写 · 字段带来源标注` + `<a :href="'#/chat/'+sid">返回访谈</a>` + `×` 关闭。
   - 读取后 `sessionStorage.removeItem`（一次性，不持久横幅）；× 关闭同义。
   - 仅 step 1（一键填写页）与进入时显示——切 step 后隐藏即可（`step===1 && originFrom`）。
3. **产物入场动画**：标记存在时给 `<section class="wizard">` 加 `.wz-from-chat`，CSS 让 `.paper-stage` `transform: translateX(14px); opacity:0 → 正常`，`var(--dur-base)` 一次性、prefers-reduced-motion 已全局兜底。
4. 不引新组件、不改路由、不动 `ResumePreview` props（红线）。

---

## R5 · SessionList 阶段分组（元宝式）

1. `sessions` → computed 两组：`inProgress`（stage ∈ greeting/probing/wrapping）、`finished`（done），各组内仍按 `last_message_at` 降序（沿用接口顺序，不重排）。
2. 两组都非空时渲染分组头（`进行中` / `已完成`，12px muted，可点击折叠，组折叠态 `ref<Set>`）；只有一组有内容时不渲染分组头直接平铺。
3. `demoMode`、loading、errMsg 分支不动。

---

## 1. 红线沿用（摘要，全文见《知脉-前端UI重构开发方案》§1）

- `ResumePreview` props 契约只许 `:layout="layout"`；不出现 `标绿|downloadElementAsPdf`；evidence-panel 结构/默认闭合/`出处` 文案/无 `<a><script><img>` 不变。
- Tab 显隐用 `v-show`/`data-*` 不用 `v-if` 销毁受测 DOM（本轮 R3 的 origin 条不在受测 DOM 内，但保险起见仍用 `v-show`）。
- `ResumeFieldsForm` 恰好 2 处 + 绑定表达式不变；`InterviewView` 的 `async function boot()` 紧邻 `onMounted(boot)` 之前；`MemoryView` 15 个标识符不碰。
- 按钮纯文本正则：`导出 PDF`、`放弃优化稿`、`中文经典`、`中文现代`、`编辑`、`预览`、`aria-pressed` 不变。
- 零新依赖；新图标走 `UiIcon` 内联 SVG；非受测视图可用 UiIcon 组件。
- 隐私文案不删改；新写 sessionStorage key `zm_resume_from` 属前端本地态（不落库、不进 URL 参数），需在 PR 向契约监护人声明。

## 2. 验收

1. `node frontend/tests/resumeExport.test.mjs` 等 5 个测试全绿（含 `tests/review_frontend_regressions.mjs`）。
2. `.devin/skills/impeccable/scripts/impeccable detect --json --changed` 无新增 P0/P1。
3. `vite build` 通过；dev server 手验：访谈页右栏=素材工作室（磁贴常显、产出记录有草稿时显示）、thinking 结束轮折叠带 Xs、点磁贴/引导卡跳 #/resume 见到出处条+预览入场、会话列表分组、375px 下无横向溢出。
