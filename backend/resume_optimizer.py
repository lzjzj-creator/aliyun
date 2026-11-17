"""简历优化内核（B 域）：STAR 改写 + 数字守卫 + claim-evidence 校验。

v1 `/api/resume/rewrite` 与 v2 `optimize/section` 共用（§9）。
红线：无人可问时，编造数字/无依据表述一律拦成【待补充】占位，绝不静默编造。
"""
import json
import re

try:
    from .llm import llm, QWEN_CHAT
except ImportError:  # FC 以 backend 目录为根启动
    from llm import llm, QWEN_CHAT

STAR_PROMPT = """你是简历改写助手。把下面的简历条目逐条改成 STAR 结构：
动词开头、突出行动与结果、每条尽量有量化点、不超过 3 行。
红线：
1. 不得新增用户没提供的事实、职责、技能或成果；
2. 缺量化数据时，在 suggested 里写【待补充：需要的数据】占位符，严禁编造具体数字；
3. 同时把需要用户确认的数据列入 questions 反问。
用户已确认的事实：{facts}
输出 JSON：{{"rewritten":[{{"original":"...","suggested":"...","needs_user_data":false}}],"questions":["..."]}}
只输出 JSON。"""

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
# 英文技术/公司类 token：REST、Spring Boot、K8s、C++、v2 等
_CLAIM_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,19}")
_PLACEHOLDER_RE = re.compile(r"【待补充：[^】]+】")


def guard_fabricated_numbers(data: dict, originals: list[str], facts: str) -> dict:
    """确定性守卫：建议文本里的数字必须能在原文/已确认事实中找到，
    找不到的强制 needs_user_data 并生成确认问题（不依赖模型自觉）。"""
    allowed = set(_NUM_RE.findall(" ".join(originals) + " " + facts))
    questions = list(data.get("questions") or [])
    for item in data.get("rewritten") or []:
        suggested = item.get("suggested", "")
        novel = [n for n in dict.fromkeys(_NUM_RE.findall(suggested)) if n not in allowed]
        if novel:
            item["needs_user_data"] = True
            shown = "、".join(novel[:5])
            questions.append(
                f"建议条目「{item.get('original', '')[:20]}…」中出现了原文没有的数字（{shown}），"
                "是真实数据还是占位估计？请提供准确数字。")
    if questions:
        data["questions"] = questions
    return data


def guard_new_claims(data: dict, originals: list[str], facts: str) -> dict:
    """claim-evidence 校验：改写新增的技术/公司类 token 必须有原文依据（§9）。
    覆盖数字守卫拦不住的"用了 K8s""为美团做了 X"型事实性新增。"""
    corpus_set = set(_CLAIM_RE.findall(" ".join(originals) + " " + facts))
    questions = list(data.get("questions") or [])
    for item in data.get("rewritten") or []:
        # 占位符内部说明文字不校验
        sug = _PLACEHOLDER_RE.sub(" ", item.get("suggested", ""))
        novel = [t for t in dict.fromkeys(_CLAIM_RE.findall(sug))
                 if t not in corpus_set and len(t) >= 2]
        if novel:
            item["needs_user_data"] = True
            questions.append(
                f"建议条目「{item.get('original', '')[:20]}…」出现了原文没有的表述"
                f"（{'、'.join(novel[:5])}），是否属实？")
    if questions:
        data["questions"] = questions
    return data


def rewrite_bullets(bullets: list[str], facts: str = "") -> dict:
    """一次 STAR 改写（≤12 条）。返回模型 JSON（已过两道守卫），失败抛异常。"""
    raw = llm.chat(
        [{"role": "system", "content": STAR_PROMPT.format(facts=facts or "（无）")},
         {"role": "user", "content": "\n".join(f"{i + 1}. {b}" for i, b in enumerate(bullets[:12]))}],
        model=QWEN_CHAT, json_mode=True, temperature=0.4)
    data = json.loads(raw)
    data = guard_fabricated_numbers(data, bullets, facts)
    data = guard_new_claims(data, bullets, facts)
    return data


def extract_placeholder(text: str) -> str:
    m = _PLACEHOLDER_RE.search(text or "")
    return m.group(0) if m else ""
