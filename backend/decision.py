"""Decision Layer：L1 规则快通道 → L3 decision-model-preview 结构化决策（L2 语义层二期再建）。

输出契约（前后端共用）：
{intent, kb_scope, confidence, route_layer, action, clarify_question}
intent: kb_qa | resume_edit | resume_qa | chitchat | out_of_scope
action: answer | guide_resume | clarify | refuse
"""
import re
import uuid

from llm import llm

RESUME_KW = re.compile(r"简历|改简历|写简历|resume|cv|CV|求职信|JD|岗位匹配")
ATTACH_HINT = re.compile(r"上传|文件|附件|docx|pdf", re.I)
GREETING = re.compile(r"^(你好|您好|hi|hello|嗨|哈喽|在吗|在不在|谢谢|thanks|早|晚上好)[。！!~？?]*$", re.I)

CLARIFY_THRESHOLD = 0.7   # need_clarify 的 noul 概率超过此值 → 反问
CLARIFY_MAX_INTENT_CONF = 0.8  # 意图已足够明确时直接回答，不被高澄清概率带偏

QUESTIONS = {
    "intent": {
        "type": "choice",
        "instructions": "用户这句话的真实意图是什么？",
        "criteria": {
            "kb_qa": "询问大学规划、实习、求职、学习相关的知识",
            "resume_edit": "要求你动手修改、优化、润色、翻译或生成简历内容（包括要上传简历）",
            "resume_qa": "只问简历写法知识：怎么写、放不放、怎么排版（如要不要放照片、一页还是两页、项目经历怎么排序、没实习怎么写）；即使句子里出现实习/项目等词，只要是写法咨询就算此类",
            "chitchat": "闲聊寒暄",
            "out_of_scope": "与大学规划/求职无关的请求（如推荐电影美食、天气、代写代码或作业）或敏感违规内容",
        },
    },
    "kb_scope": {
        "type": "choice",
        "instructions": "这个问题与哪个知识域最相关？",
        "criteria": {
            "freshman": "大一：入学、规划、绩点、竞赛",
            "sophomore_junior": "大二大三：实习、科研、方向分化",
            "senior": "大四：简历、秋招、保研、毕业去向",
            "cases": "大学四年规划真实案例",
            "major": "专业课程、能力与出路",
            "video": "直播/视频主题复盘",
            "all": "无法判断或跨多个域",
        },
    },
    "need_clarify": {
        "type": "noul",
        "instructions": "意图是否模糊到必须向用户反问澄清才能继续？",
    },
}

INTENT_LABEL = {"kb_qa": "查资料问答", "resume_edit": "修改简历",
                "resume_qa": "简历写法咨询", "chitchat": "闲聊",
                "out_of_scope": "无关问题"}


def route(query: str, has_attachment: bool = False) -> dict:
    q = query.strip()

    # L1 规则快通道：0 成本命中即返回
    if has_attachment or (RESUME_KW.search(q) and ATTACH_HINT.search(q)):
        return _r("resume_edit", "all", 0.95, "rule", "guide_resume")
    if not q:
        return _r("chitchat", "all", 1.0, "rule", "answer")
    if GREETING.match(q):
        return _r("chitchat", "all", 0.9, "rule", "answer")

    # L3 decision-model-preview 结构化决策（~50ms）
    try:
        ans = llm.decide(q, QUESTIONS, ticket_id=str(uuid.uuid4())[:8])
        intent = ans["intent"].get("choice", "kb_qa")
        conf = float(ans["intent"].get("confidence", 0.5))
        scope = ans["kb_scope"].get("choice", "all")
        clarify_p = float(ans.get("need_clarify", {}).get("noul", 0))
        probs = ans["intent"].get("probabilities", {})
    except Exception:
        return _r("kb_qa", "all", 0.5, "llm", "answer")  # 决策服务挂了按问答处理

    if intent not in INTENT_LABEL:
        intent = "kb_qa"

    if clarify_p >= CLARIFY_THRESHOLD and conf < CLARIFY_MAX_INTENT_CONF:
        top2 = sorted(probs, key=probs.get, reverse=True)[:2]
        cq = (f"想确认一下：你是要「{INTENT_LABEL.get(top2[0], '问答')}」，"
              f"还是「{INTENT_LABEL.get(top2[1], '其他')}」？" if len(top2) == 2
              else "能再说具体一点吗？")
        return _r(intent, scope, conf, "decision-model", "clarify", cq)
    if intent == "out_of_scope":
        return _r(intent, scope, conf, "decision-model", "refuse")
    if intent == "resume_edit":
        return _r(intent, scope, conf, "decision-model", "guide_resume")
    return _r(intent, scope, conf, "decision-model", "answer")


def _r(intent, scope, conf, layer, action, clarify=None):
    return {"intent": intent, "kb_scope": scope, "confidence": conf,
            "route_layer": layer, "action": action,
            "clarify_question": clarify}
