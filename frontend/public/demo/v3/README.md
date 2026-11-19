# v3 契约 fixture（issue #35）

前后端共用契约快照，对应 `docs/知脉-v3-开发计划.md` §8 接口表。**改动必须经相关 issue 评论 @对方 ack**（沿用 v2 契约纪律）。

| 文件 | 对应接口 | 说明 |
|---|---|---|
| `memory.json` | `GET /api/v3/memory` | 记忆总览：enabled/consent_at/fact_count/by_kind/by_status/last_extracted_at |
| `facts.json` | `GET /api/v3/facts` | 事实分页：`facts[]`（id/kind/slot_path/value/status/confidence/evidence[{session_id,msg_id,quote}]）+ `next_cursor`；含 confirmed/proposed 各例 |
| `resume-list.json` | `GET /api/v3/resumes` | 列表项不含全文：id/title/target_role/status/completeness/updated_at |
| `resume-detail.json` | `GET /api/v3/resumes/{id}` | 详情：fields + evidence_map（每条 evidence 带 `fact_id` + `fact_status`）+ gaps + fact_snapshot + version + stale_sources(空) |
| `stale.json` | `GET /api/v3/resumes/{id}`（stale 态） | `stale_sources` 黄条态：来源已更新(`updated`)/已删除(`deleted`)各一例 |

通用约定：所有响应顶层带 `protocol: 3`；错误形状 `{code, message, retryable}`；`X-Owner-Token` 缺失/错误一律 404。字段路径约定沿用 v2（`basics.name`、`education.0.school`、`projects.0.bullets.1`、`skills.groups.0.items.2`、`awards.items.0.text`、`summary.text`）；`fields._meta.manual_override` 为手改字段路径列表，重新生成时默认不覆盖。
