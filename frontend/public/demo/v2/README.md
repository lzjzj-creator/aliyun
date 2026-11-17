# v2 契约 fixture（issue #4）

前后端共用契约快照。**A 用这些形状写契约测试，B 用它们驱动全部 UI 开发**；改动必须经相关 issue 评论 @对方 ack（§12.2）。

| 文件 | 对应接口/事件 | 说明 |
|---|---|---|
| `template.json` | `GET /api/v2/resume/template` | 简历模板契约快照（源文件 `backend/resume_template.json`，B 监护） |
| `stream-interview.json` | `POST /api/v2/chat/stream` | 一轮访谈的 SSE 事件序列（数组顺序回放） |
| `stream-kbqa.json` | 同上 | 知识问答轮（retrieve+sources+带回访谈） |
| `stream-wrapup.json` | 同上 | 收尾轮（wrap_up + card:goto_resume） |
| `session.json` | `GET /api/v2/sessions/{id}` | 会话 bundle：session+messages(含steps)+profile |
| `sessions.json` | `GET /api/v2/sessions` | 会话列表（仅元数据，无正文） |
| `messages.json` | `POST /api/v2/messages` | P0 过渡回写形状（草案，待 A ack） |
| `autofill.json` | `POST /api/v2/resume/autofill` | `{fields, evidence_map, gaps}` 完整样例（**真实链路录制**：输入=session.json 的 messages+profile） |
| `optimize-plan.json` | `POST /api/v2/resume/optimize/plan` | 分段优化计划（真实链路录制） |
| `optimize-section.json` | `POST /api/v2/resume/optimize/section` | 单段结果（真实链路录制，steps+change_log+占位） |

字段路径约定（evidence_map/gaps/change_log 通用）：`basics.name`、`education.0.school`、`projects.0.bullets.1`、`skills.groups.0.items.2`、`awards.items.0.text`、`summary.text`。
