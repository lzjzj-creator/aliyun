# Spike #8 · 编排层实测（真实 DashScope）

> 实测时间：本地开发机 → DashScope（OpenAI 兼容端点）· 脚本 `tests/spike_orchestrate.py` 可复跑
> 说明：均为真实 API 计时（含网络往返），非 FC 环境数据；FC 同区域部署通常只会更快。

## A. 编排 JSON：schema 合法率

5 条固定输入走 `coach._orchestrate`（`QWEN_MODEL_ROUTER`，当前 .env 配置为 qwen-plus + `response_format=json_object`）：

| 输入（截断） | 结果 | 延迟 |
|---|---|---|
| 我是大一新生… | act=ask · focus=basics.target_role | 3.9s |
| 实习怎么找啊… | act=answer · need_kb | 3.0s |
| 做过一个二手书小程序… | act=ask · focus=projects · deep | 2.6s |
| 嗯嗯 | act=ask · focus=basics.target_role | 3.0s |
| 差不多就这样吧… | act=wrap_up | 3.4s |

- **schema 合法率 5/5**（含字段类型/act 枚举/focus_slot 白名单校验），未触发修复重试
- act 语义全对：提问→answer、经历陈述→ask+deep、收尾意图→wrap_up
- turbo vs plus 对比（同一 prompt，各 2 次）：turbo 2.7–3.3s，plus 2.3–3.8s，**均合法**，延迟同档
- 结论：**编排用 turbo（`QWEN_MODEL_ROUTER`）即可**，plus 无质量刚需；连续失败 3 次熔断降级规则已在 `_orchestrate` 实现

## B. enable_thinking（qwen-plus 流式）

同一 STAR 深问 prompt：

| 模式 | 首 token | 总时延 | 输出长度 |
|---|---|---|---|
| thinking=false | 1.6s | 5.3s | 250 字 |
| thinking=true | 10.7s | 12.1s | 209 字 |

- thinking 首 token 慢 ~9s、总时延 +128%，输出反而更短
- `reasoning_content` 在 `llm.chat_stream` 内被丢弃，不出服务端（符合"原始 CoT 不透出"契约）
- 结论：**deep 轮默认不开 thinking**。`enable_thinking` 已参数化保留，仅作预留；若启用需接受 ~10s 首 token

## C. extractor JSON

单条富文本（身份 3 槽 + 项目 + 双技能）：7.2s（plus），产出 `slot_updates×3 + experiences×1(star=1.0) + skills×2`，全部过 quote 红线写入画像。

- 抽取放在 token 流之后执行（step 事件可见但不阻塞回复），7s 量级可接受
- 若后续要压延迟：抽取可降到 turbo，或"每 N 轮批量抽取"（实现已支持 ack 轮跳过）

## 对引擎设计的落地结论

1. L1 规则（chips/跳过/寒暄/已收尾）跳过编排模型 —— 0 延迟命中路径已生效
2. 编排 = turbo + json_mode + 修复重试 1 次 + 连败熔断 → 规则兜底，实测合法率有保障
3. 访谈生成走 plus 流式（首 token ~1.6s 量级），thinking 默认关
4. 抽取 turbo JSON 后置执行，quote 红线在服务端强制
