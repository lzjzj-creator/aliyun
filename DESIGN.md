# DESIGN.md — 知脉视觉系统（Porcelain Studio / 青瓷智识）

> 视觉权威来源：frontend/src/style.css `:root`（本文件为其摘要，冲突以 style.css 为准）。

## Tokens
- **表面**：`--bg-canvas #f7f8fa`（工作区）、`--bg-sidebar #f1f3f6`（导航/会话栏）、`--bg-surface #fff`（卡/A4）、`--bg-subtle #f4f6f8`（次级输入/思考块）、`--bg-hover #eaeef3`、点阵画布 `--bg-canvas-dots #eceff4` + `--dot-color #d1d7e2`
- **边框**：`--border #e5e8ee`、`--border-strong #cfd6e0`、`--border-subtle #eef0f4`
- **品牌**：`--brand #1b365d` 深靛墨蓝（主按钮/标题/激活）+ `--brand-soft #eef3f9`（激活底/用户气泡）
- **智能体**：`--agent #0d7c66` 玉石青（教练/证据/覆盖率/优化增量）+ `--agent-soft #ecfdf5` + `--agent-border #a7f3d0`
- **教练批注**：`--coach #b45309` 柔琥珀（讲解/缺口/stale/待补充）+ `--coach-soft #fffbeb` + `--coach-border #fde68a`
- **危险**：`--danger #b91c1c` + soft/border（仅真错误）
- **文字**：`--ink #111827`、`--ink-secondary #4b5563`、`--ink-muted #6b7280`（AA 4.5:1 担保）
- **圆角**：xs6/sm8/md12/lg16/pill999；**阴影**：xs/sm/md/paper（A4 用 `--shadow-paper`）
- **动效**：`--ease cubic-bezier(.2,.8,.2,1)`、`--dur-fast 150ms`、`--dur-base 220ms`；prefers-reduced-motion 全局兜底
- **布局**：`--sidebar-w 216px`（折叠64）、`--topbar-h 52px`、`--col-side 244px`、`--col-panel 280px`
- **暗色**：`:root[data-theme="dark"]` token 已备（A4 纸面恒白），默认浅色

## Type
Inter/PingFang/Noto/YaHei/system-ui 单族；Page title 20px/650、Section 15px/600、Body/Chat 14.5px/1.7、UI 13.5px/500、Caption 12px/500、Metric 22-24px/700 tabular-nums。

## Component vocabulary
- 按钮：`.btn-primary/.btn-agent/.btn-secondary/.btn-ghost/.btn-danger`（34px，sm 28px，8px 圆角）
- 教学 Diff：`.coach-diff-card`（before 红删线 / after 青 / reason 琥珀）
- A4：`.paper-stage` 点阵画布 + `.resume-preview-frame` 纸影；打印只渲染 `.print-area`
- 芯片：`.chip.hit`（青描边已收集）/`.chip.miss`（琥珀虚线缺口，可点）
- 图标：`UiIcon` 18 个 24×24 stroke1.75 Lucide 风线性图标；受测视图用内联 `<svg>`
- 导航：左栏激活=白底+阴影+左缘 3px `--agent` 竖条；移动=底部毛玻璃 Tab
- 消息：AI=无框宽幅文档流（28px `知`徽标）；用户=右对齐 `--brand-soft` 圆角泡；来源=横排序号小卡
- 思考流：details/summary 折叠 + 左竖时间线 + 状态点（running 脉冲）+ kind pill
- 隐私：`tb-privacy-pop` 胶囊（青底）展开说明卡

## Bans（项目自定 + 继承 craft-floor）
禁紫蓝渐变/玻璃拟态装饰、禁卡片套卡片、禁灰字彩底、禁 emoji/Unicode 当图标、A4 纸面零污染、占位不用红、kicker/eyebrow 不用、模态非必需不用（删除确认用行内展开）。

检测器 waiver：字体栈首位是 Inter 仅为拉丁字符回退，中文走 PingFang/Noto/YaHei 系统栈——刻意不引 webfont（离线可用、零外链、隐私卖点），`overused-font` 不适用。
