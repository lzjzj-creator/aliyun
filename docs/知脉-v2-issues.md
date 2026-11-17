# 知脉 v2.0 · GitHub Issues 正文模板

> 用法：按 §12.3 总表顺序建 issue；`Blocked by:` 填实际 issue 编号；Milestone 统一 `v2.0`。
> 每条 = 标题 + Labels + 正文（背景/任务/边界/验收/依赖）。
> 分工：前后端分离——**A=后端**（#1–#3、#8、#9、#11–#13、#17、#18、#21、#23），**B=前端**（#5–#7、#10、#14–#16、#19、#20、#22、#24）；#4/#25 共同。`backend`/`frontend` label 即 Owner。
> 文件边界：后端 `backend/`+`supabase/`+`tests/` 全归 A；前端 `frontend/` 全归 B；唯一公共财产 `frontend/public/demo/v2/`（fixture）与 `docs/`。B 全程可按 fixture 先行开发 UI，Gate=真接口联调点。

---

## #1 [P0] Supabase v2 迁移：三表+约束+触发器+90天清理

Labels: `p0` `backend` ｜ Blocked by: — ｜ 估 1 人日 ｜ Owner: A

**背景**：v2 需持久化会话/消息/画像，FC 无状态全靠 Supabase（§3）。

**任务**：
- 新建 `supabase/migrations/00X_v2.sql`，含 §3 全部：`chat_sessions`（含 `session_token_hash`、`last_message_at`、stage check）、`chat_messages`（`client_msg_id`、status、steps/sources jsonb、用户消息幂等唯一索引）、`user_profiles`（coverage numeric 单源、coverage_detail、version 乐观锁）
- `touch_updated_at()` 触发器 ×2
- 90 天无活动会话清理：pg_cron（若可用）或注明 FC 定时调用清理函数

**边界**：不建 resumes 表；不动 documents/chunks。

**验收**：干净库迁移一次通过；check/唯一约束负向用例报错；清理函数 dry-run 命中预期行；回滚 SQL 可用。

---

## #2 [P0] memory.py：会话/消息/画像 CRUD + 令牌签发

Labels: `p0` `backend` ｜ Blocked by: #1 ｜ 估 1 人日 ｜ Owner: A

**背景**：§3 数据模型的服务端访问层，复用 `kb.py` 的 httpx Supabase REST 客户端模式。

**任务**：`backend/memory.py`：
- `create_session(anon_id) → {session, token}`：token=32 字节随机，只存 `sha256(token)` 进 `session_token_hash`，明文仅此一次返回
- `verify_session(session_id, anon_id, token) → bool`
- `list_sessions(anon_id, limit=50)`、`get_session_bundle(session_id)`（session+messages+profile）
- `append_message(...)`（写 status/steps/sources，更新 `last_message_at`）、`update_message_status(id,status)`
- `merge_profile(session_id, updates, expected_version)` 乐观锁 merge、`get_profile`
- `delete_session`（级联）、`cleanup_expired(days=90)`、`touch_title(session_id, first_msg)`（首条用户消息 20 字做标题）

**边界**：不做鉴权中间件（#3）；不写业务编排。

**验收**：`python -c` 冒烟全部 CRUD；伪造 token verify=False；幂等键重复写不重复行。

---

## #3 [P0] 归属校验中间件 + 三层限流 + 删除/messages 接口

Labels: `p0` `backend` ｜ Blocked by: #2 ｜ 估 1 人日 ｜ Owner: A

**背景**：匿名会话必须防越权读写（IDOR）；公开端点要防刷（§5、§14）。

**任务**：
- 校验依赖：读 `X-Anon-Id`/`X-Session-Token` 头，失败统一 **404**（不泄露存在性）
- 限流：IP 20 req/min、anon 100 轮/天、同 session 同时 1 条流；超限 `429 + Retry-After`（沿用 v1 的 XFF 链尾取真实 IP）
- `GET /api/v2/sessions`、`GET/DELETE /api/v2/sessions/{id}` 挂上校验
- `POST /api/v2/messages`（P0 过渡接口，§5）
- 日志只记 id/长度/耗时，不记正文与令牌

**边界**：不做访谈引擎；限流用内存桶即可（单实例 FC）。

**验收**：伪造/缺失 token、跨 anon 读写删全部 404；三层限流各触发一次 429；DELETE 后消息/画像级联消失。

---

## #4 [P0] 契约 fixture：SSE 事件序列 + 接口响应 demo JSON（最先做）

Labels: `p0` `shared` ｜ Blocked by: — ｜ 估 0.5 人日 ｜ Owner: 共同 ｜ **两轨分叉点，B 拿到后可独立开发全部 UI**

**背景**：契约先行让 B 不等 A（§12.2）。

**任务**：`frontend/public/demo/v2/` 下产出一组 JSON：
- `stream-interview.json`：一轮完整访谈的 SSE 事件序列（session→step_start/end×3→token→profile→done，含真实样例数据）
- `stream-kbqa.json`、`stream-wrapup.json`（含 card:goto_resume）
- `session.json`（GET bundle 形状）、`template.json`（契约初稿，可随后被 #17 迭代）、`autofill.json`（fields+evidence_map+gaps）、`optimize-section.json`（steps+change_log+占位）
- `README.md` 一句话说明每个文件对应的接口/事件

**边界**：只写形状+样例，不写实现逻辑。

**验收**：B 确认能驱动 UI；A 确认能写成契约测试；双方在该 issue 下 ack 冻结。

---

## #5 [P0] storage.js + api 分模块协议层

Labels: `p0` `frontend` `shared` ｜ Blocked by: #4 ｜ 估 1 人日 ｜ Owner: B

**任务**：
- `storage.js`：`anon_id`（`crypto.randomUUID()` 惰性生成）、`session_tokens{sid:token}`、`resume_draft_v1`/`resume_draft_optimized`（带 schema_version+template_version）读写
- **api 分模块**：`api/core.js`（headers 注入/SSE 解析底座）+ `api/sessions.js`；`api/chat.js`、`api/resume.js` 先留空壳，后续按域扩展（`api/*.js` 全归 B，分模块仅为可维护性）
- 全部请求自动带 `X-Anon-Id`/`X-Session-Token` 头

**边界**：只做协议层，不碰 UI。

**验收**：对 fixture 回放事件序列解析正确；headers 注入正确；断线抛错可捕获。

---

## #6 [P0] SessionList 侧栏 + 删除确认 + 隐私文案

Labels: `p0` `frontend` ｜ Blocked by: #5 ｜ 估 1 人日 ｜ Owner: B

**任务**：`SessionList.vue` 抽屉式侧栏（移动端全屏）：会话卡（标题/时间/stage 徽标）、新建、删除（二次确认文案说明级联删除）；App.vue 接入。隐私条更新为 v2 文案："对话将保存用于历史记录，可随时删除；简历成稿只存本机"。

**边界**：接 `GET /api/v2/sessions` 真实接口；不做访谈 UI。

**验收**：列表/新建/删除全通；无会话时空态友好；移动端不横向滚动。

---

## #7 [P0] 过渡落库：POST /api/v2/messages + 旧 ChatView 接入（Gate P0）

Labels: `p0` `backend` `frontend` `shared` ｜ Blocked by: #3, #5 ｜ 估 1 人日 ｜ Owner: B（接口由 A 在 #3 交付；Gate 共同验收）

**背景**：平滑过渡——v1 问答链路不动，但消息先落新库，P1 切换无缝。

**任务**：旧 ChatView 每轮流结束后调 `POST /api/v2/messages` 回写 `{session_id, client_msg_id, user, assistant, meta?}`；`session_id` 为空时由服务端建会话并一次性返回 `session_token`（响应 `{session_id, message_ids[, session_token]}`，见 `demo/v2/messages.json`），此后客户端须复用 `session_id`+`X-Session-Token`。历史可回看、可删除。

**验收（Gate P0）**：旧问答在新存储下可用；刷新可回看；删除生效；伪造 token/越权 404；90 天清理 dry-run 命中；隐私文案与删除入口在线；`npm run build` 通过。

---

## #8 [P0.5] 编排 spike：turbo JSON / reasoning / 延迟报告

Labels: `p0.5` `backend` `spike` ｜ Blocked by: — ｜ 估 1 人日 ｜ Owner: A

**任务**：离线脚本实测：
- qwen-turbo json_mode 多字段编排输出 `{act,need_kb,focus_slot,reason,deep}` 成功率（30 条样例输入）
- `decision-model-preview` 仅做 intent 的准确性（复用 route_cases）
- qwen-plus `enable_thinking` 是否可得 reasoning_content、增量格式、首 token/整轮延迟
- 产出 `docs/spike-编排.md` 数据与结论（启用/降级建议）

**验收（Gate Spike 之一）**：编排 JSON 成功率 ≥95%；不达标项给出降级方案（规则式编排）。

---

## #9 [P0.5] optimize 单段 p95 实测

Labels: `p0.5` `backend` `spike` ｜ Blocked by: — ｜ 估 0.5 人日 ｜ Owner: A

**任务**：FC 环境下实测 analyze+单 section 改写延迟 p95（10 次采样）；产出数据贴 issue。（打印导出可行性验证移至前端 spike #10）

**验收（Gate Spike 之二）**：单段 p95 距 FC 120s 限制有足够余量；否则缩单段范围或 P3 延期，结论写进 issue。

---

## #10 [P0.5] 契约渲染 demo + 打印导出可行性草样

Labels: `p0.5` `frontend` `spike` ｜ Blocked by: #4 ｜ 估 1 人日 ｜ Owner: B

**任务**：最小 Vue 组件按 `template.json` `v-for` 渲染 sections/fields 骨架（不美化），验证"契约驱动渲染"成立：改契约 JSON → 渲染跟随变。消费 #4 产出的 `template.json` 初稿并反馈可用性（slot 集深化由 A 在 #11/#17 迭代）；打印导出可行性草样（print CSS 试写，确认中文排版 CSS 量可控、移动端可行）。

**验收（Gate Spike 之三）**：契约字段增删能即时反映；确认不需要任何契约外硬编码。

---

## #11 [P1] coach.py：L1 + 状态机 + orchestrator + step 事件协议

Labels: `p1` `backend` ｜ Blocked by: #2, #8, #4(模板骨架) ｜ 估 2.5 人日 ｜ Owner: A

**背景**：§6 核心引擎。状态机 greeting→probing→wrapping→done。

**任务**：
- `coach.py` + `routers/coach.py`（路由一文件一模块，main.py 只 include）：L1 规则（招呼/直接提问/跳轮/收尾）→ orchestrator（turbo JSON，schema 校验+修复重试1次+3连败降级规则）→ retrieve 工具（need_kb 时，CRAG 打分+重写重试≤1）→ interviewer（qwen-plus 流式；deep 轮开 thinking）→ 事件按 §4 发出
- `llm.py`：reasoning 流支持（服务端生成 reasoning_summary，不透原始 CoT）
- `POST /api/v2/chat/stream`：建会话/令牌签发/消息落库（streaming→done/error）全流程
- `COACH_ENABLED=0` 开关

**边界**：extractor/profile merge 在 #12；招呼语内容在 #13。

**验收**：fixture 事件序与真实流一致（契约测试）；断线 status=error；无令牌建会话返回 token；编排失败降级不整轮报错。

---

## #12 [P1] extractor + quote 校验 + profile merge + coverage/收尾

Labels: `p1` `backend` ｜ Blocked by: #11 ｜ 估 1.5 人日 ｜ Owner: A
**产出共享 schema**：`user_profiles.profile` JSON 结构是本 issue 定稿的**跨端契约**（前端 ProfilePanel #15 按它渲染、autofill #18 按它消费），冻结后在 issue 评论区钉住。

**任务**：
- `prompts/extractor.md`：本轮对话→`{new_facts, slot_updates[{slot, value, quote}]}`
- 服务端校验：quote 必须是用户原话子串，否则丢弃该 update（红线 §6）
- profile merge 进 `user_profiles`（乐观锁；用户已确认字段不被覆盖；冲突生成确认问题）
- coverage 计算：有证据必填 slot/自适应必填总数（§6 公式），写 `coverage`+`coverage_detail` 唯一事实源
- 收尾判定：coverage≥0.8 且 ≥1 段经历 star_score≥0.6，或 max_turns=12 → wrap_up 事件+card(goto_resume)

**验收**：捏造 quote 被丢弃；已确认字段不被覆盖；coverage 单调可解释；12 轮兜底必出 gaps。

---

## #13 [P1] 招呼流 + 直接提问分流

Labels: `p1` `backend` ｜ Blocked by: #11 ｜ 估 1 人日 ｜ Owner: A

**任务**：新会话空 message → 流式发招呼+「开始访谈 / 直接提问」两 chips（card:suggest_reply）；`prompts/coach_system.md` 完稿（人设/每轮一问/先回应再追问/KB 钩子/不报分）；知识提问分支走 retrieve+answer 后自然带回；`interview_plan` 由契约 slot 生成。

**验收（部分 Gate P1）**：新会话 AI 先开口；直接提问不被强行拉回；访谈中问知识答完能带回。

---

## #14 [P1] ThinkingSteps 组件 + SSE 事件流接入

Labels: `p1` `frontend` ｜ Blocked by: #4, #5 ｜ 估 1.5 人日 ｜ Owner: B

**任务**：`CoachThinkingSteps.vue`：折叠思考流（icon+title+detail，默认收起显示"N 步"），渲染 step_start/end、reasoning_summary；接 `api/chat.js` 事件流，对 fixture 与真实接口均可工作；sources 卡片沿用现有样式。

**验收**：事件乱序/缺失不崩；折叠/展开流畅；真实接口与 fixture 渲染一致。

---

## #15 [P1] ProfilePanel 画像面板

Labels: `p1` `frontend` ｜ Blocked by: #5 ｜ 估 1 人日 ｜ Owner: B

**任务**：`CoachProfilePanel.vue` 侧栏：覆盖率环 + 已收集维度（experiences/skills/goal 分组）+ gaps 提示；`profile` 事件实时更新；可折叠。

**验收**：profile 事件驱动增量更新；空画像显示引导文案；移动端抽屉可用。

---

## #16 [P1] InterviewView 整合（Gate 访谈）

Labels: `p1` `frontend` ｜ Blocked by: #11(真接口), #14, #15 ｜ 估 1 人日 ｜ Owner: B

**任务**：ChatView 重构为 InterviewView：`#chat/:id` 路由直开→GET bundle 恢复（含 steps 回放）；消息流+思考流+画像+来源+suggest chips 全整合；新建/切换会话；断线提示与重发。

**验收（Gate 访谈）**：新会话 AI 先开口；每轮有真实 step 无原始 CoT；画像随对话增长；12 轮内达标或输出 gaps；直接提问不被强行拉回；会话直链可打开。

---

## #17 [P2] resume_template.json 定稿 + loader + /template 接口

Labels: `p2` `backend` ｜ Blocked by: #4(骨架), #10(反馈) ｜ 估 1 人日 ｜ Owner: A（契约监护人）

**任务**：`backend/resume_template.json` 按 §8 契约定稿（fields/rubric/slot 映射/adaptive 规则）；`resume_template.py` 加载+JSON Schema 校验；`GET /api/v2/resume/template` 唯一出口；slot 推导函数（orchestrator 与 #12 coverage 复用）。

**验收**：契约过 schema 校验；版本不匹配 409；前后端读同一份（契约测试断言字段集一致）。

---

## #18 [P2] autofill：会话 → 字段 + evidence_map + gaps

Labels: `p2` `backend` ｜ Blocked by: #17, #12 ｜ 估 1.5 人日 ｜ Owner: A
**契约产出**：按 #12 冻结的 profile JSON 编码；响应形状即 #19 的开发依据，必须与 #4 fixture 一致。

**任务**：`routers/resume.py` 内实现 `POST /api/v2/resume/autofill`：`prompts/autofill.md`（qwen-plus json_mode）profile+消息→模板字段；`evidence_map` 每条 quote 服务端校验在消息中存在，否则降级 gap；无证据字段进 gaps 不脑补（红线 §8）；`template_version` 校验 409。

**验收**：golden profile→字段断言通过；quote 100% 可溯源；编造字段=0；gaps 无漏报。

---

## #19 [P2] Wizard① 模板选择 + 一键填写 + 表单编辑

Labels: `p2` `frontend` ｜ Blocked by: #17, #18(真接口；可先按 #4 fixture 开发) ｜ 估 1.5 人日 ｜ Owner: B

**任务**：`ResumeWizard.vue` Step①：会话选择 + [一键填写] → autofill 结果写 `resume_draft_v1`；契约驱动表单编辑（v-for sections）；gaps 黄标。

**验收**：一键填出成稿；无证据字段全部黄标；人工编辑即改即存 localStorage。

---

## #20 [P2] Wizard② 契约预览 + 打印导出 + 出处悬停（Gate 简历Ⅰ）

Labels: `p2` `frontend` ｜ Blocked by: #17 ｜ 估 1.5 人日 ｜ Owner: B

**任务**：Step②：契约渲染器实现单栏中文校招版式（按 sb2nov/billryan 风格写普通 CSS，含 print 媒体查询）；字段悬停显示出处 quote；浏览器打印导出。

**验收（Gate P2）**：预览与契约结构一致；打印一页不溢出；出处可见；模板版本不匹配触发 409 重取。

---

## #21 [P3] optimize/plan|section 流水线

Labels: `p3` `backend` ｜ Blocked by: #17 ｜ 估 2 人日 ｜ Owner: A

**任务**：`routers/resume.py` 内实现 `optimize/plan`（section 优先级）与 `optimize/section`：analyze(vendor)→STAR 改写（STAR_PROMPT+bullet_rubric）→`_guard_fabricated_numbers`→claim-evidence（公司/角色/技术须原文依据）→未过则占位+待确认问题→change_log；响应带 steps。

**验收**：0 编造数字；claim 无依据必拦；单段 steps 完整；单段超时不影响其他段。

---

## #22 [P3] Wizard③ 逐段进度 + 对比 + 占位 + 导出（Gate 简历Ⅱ）

Labels: `p3` `frontend` ｜ Blocked by: #20, #21(真接口；可先按 fixture 开发) ｜ 估 1.5 人日 ｜ Owner: B

**任务**：Step③：[一键优化]→plan→逐段调用（进度流展示 steps）→前后对比视图（逐字段采纳）→占位黄标清单二次确认→优化稿存 `resume_draft_optimized`→导出。

**验收（Gate P3）**：单段失败不阻塞；原稿不被覆盖；占位全部可见且导出前二次确认。

---

## #23 [P4] tests/coach_sim.py 脚本化回放评测

Labels: `p4` `backend` `test` ｜ Blocked by: #12 ｜ 估 1 人日 ｜ Owner: A（同时断言跨端契约：profile schema 与 SSE 事件序，供 B 校验前端假设）

**任务**：5 个固定回答序列 persona（大一无经历/大二有项目/大三有实习/文科/故意跑偏）走真实接口回放；断言：收敛轮数、act 序列合理性、profile 准确≥90%/0 虚构、coverage 单调、越权/限流/幂等负向用例、SSE 事件序与 done/error 终结。报告入 `docs/评测报告-v2.md`。

**验收**：5/5 收敛无死循环；负向用例全过；报告生成。

---

## #24 [P4] 演示快照 v2 + 移动端/打印复查 + 开源许可登记

Labels: `p4` `frontend` `docs` ｜ Blocked by: #19, #22 ｜ 估 1 人日 ｜ Owner: B（演示快照含全链路，A 配合提供真实接口/样例数据）

**任务**：`public/demo/` 新快照（访谈/填写/优化各一，真实链路录制）；移动端 375px 全流程复查；打印输出复查；所有借鉴来源+LICENSE 登记 `docs/开源许可.md`。

**验收**：演示离线可跑；移动端无横向滚动；许可清单完整。

---

## #25 [P4] 联调回归 + 部署 + Gate 总复核

Labels: `p4` `backend` `frontend` `shared` ｜ Blocked by: 全部 ｜ 估 1 人日 ｜ Owner: 共同（A 管 FC 部署/回归脚本，B 管 Sites 发布/UI 验收）

**任务**：`route_cases` 50 条 + `rag_e2e` 回归不退化；§13 评测表全项过；FC 重部署（含 `COACH_ENABLED` 开关）+ Sites 发布；v1→v2 切流与回滚演练；打 tag。

**验收（Gate P4）**：评测表全绿；线上访谈→填写→优化全链路真机走通；回滚演练成功。
