"""Decision Layer：L1 规则快通道 → L3 qwen-turbo JSON 分类（L2 语义层二期再建）。

输出契约（前后端共用）：
{intent, kb_scope, confidence, route_layer, action, clarify_question}
intent: kb_qa | resume_edit | resume_qa | chitchat | out_of_scope
action: answer | guide_resume | clarify | refuse
"""
import json
import re

from llm import llm, QWEN_ROUTER

RESUME_KW = re.compile(r"简历|改简历|写简历|resume|cv|CV|求职信|JD|岗位匹配")
ATTACH_HINT = re.compile(r"上传|文件|附件|docx|pdf", re.I)
SCOPES = ["freshman", "sophomore_junior", "senior", "cases", "major", "video", "all"]

FEW_SHOT = """你是意图分类器。根据用户输入输出 JSON：
{"intent":"kb_qa|resume_edit|resume_qa|chitchat|out_of_scope",
 "kb_scope":"freshman|sophomore_junior|senior|cases|major|video|all",
 "confidence":0~1,"clarify_question":null或一句话}
规则：
- resume_edit=要改/做/优化自己的简历；resume_qa=只问简历写法知识
- kb_scope 取最相关知识域：大一→freshman 大二大三→sophomore_junior
  大四/简历/秋招→senior 规划案例→cases 专业→major 视频复盘→video 不确定→all
- 两种意图都可能时 clarify_question 反问，不要瞎猜
只输出 JSON，不要解释。"""


def route(query: str, has_attachment: bool = False) -> dict:
    q = query.strip()

    # L1 规则快通道：0 成本命中即返回
    if has_attachment or (RESUME_KW.search(q) and ATTACH_HINT.search(q)):
        return _r("resume_edit", "all", 0.95, "rule", "guide_resume")
    if not q:
        return _r("chitchat", "all", 1.0, "rule", "answer")
    if len(q) <= 4 and not RESUME_KW.search(q):
        return _r("chitchat", "all", 0.8, "rule", "answer")

    # L3 LLM 分类兜底（L2 语义层二期再建）
    try:
        raw = llm.chat(
            [{"role": "system", "content": FEW_SHOT},
             {"role": "user", "content": q}],
            model=QWEN_ROUTER, json_mode=True, temperature=0)
        d = json.loads(raw)
    except Exception:
        return _r("kb_qa", "all", 0.5, "llm", "answer")  # 分类挂了按问答处理

    intent = d.get("intent", "kb_qa")
    scope = d.get("kb_scope", "all")
    conf = float(d.get("confidence", 0.5))
    clarify = d.get("clarify_question")

    if intent not in {"kb_qa", "resume_edit", "resume_qa", "chitchat", "out_of_scope"}:
        intent = "kb_qa"
    if scope not in SCOPES:
        scope = "all"
    if conf < 0.6 and clarify:
        return _r(intent, scope, conf, "llm", "clarify", clarify)
    if intent == "out_of_scope":
        return _r(intent, scope, conf, "llm", "refuse")
    if intent == "resume_edit":
        return _r(intent, scope, conf, "llm", "guide_resume")
    return _r(intent, scope, conf, "llm", "answer")


def _r(intent, scope, conf, layer, action, clarify=None):
    return {"intent": intent, "kb_scope": scope, "confidence": conf,
            "route_layer": layer, "action": action,
            "clarify_question": clarify}
