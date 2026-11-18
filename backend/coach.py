"""v2 访谈引擎（§6/§12）：L1 规则 → orchestrator → retrieve → interviewer → extractor。

多角色分离：编排(turbo JSON) / 访谈生成(plus 流式) / 抽取(turbo JSON) 分上下文。
- 所有模型 JSON 输出过 schema 校验，失败修复重试 1 次，再失败降级规则编排
- quote 必须是用户原话子串才可入画像（红线）；已确认字段不被模型覆盖
- reasoning_summary 由服务端按步骤生成，模型原始 CoT 不出服务端
- 产出 (event, data) 供 router 包 SSE；步骤形状见 §4/fixture
"""
import json
import os
import re
import time
from pathlib import Path

try:
    from .llm import llm, QWEN_CHAT, QWEN_ROUTER
    from . import kb, memory
    from .resume_template import load as _load_tpl, required_slots, adaptive_min_entries
except ImportError:  # FC 以 backend 目录为根启动
    from llm import llm, QWEN_CHAT, QWEN_ROUTER
    import kb
    import memory
    from resume_template import load as _load_tpl, required_slots, adaptive_min_entries

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
MIN_SCORE = float(os.getenv("MIN_RETRIEVE_SCORE", "0.25"))
MAX_TURNS = int(os.getenv("COACH_MAX_TURNS", "12"))
RECENT_ROUNDS = int(os.getenv("COACH_RECENT_ROUNDS", "8"))
RECENT_MSGS = RECENT_ROUNDS * 2   # "最近8轮"=8 个用户+助手对 = 16 条消息
MAX_OUTPUT_TOKENS = int(os.getenv("COACH_MAX_TOKENS", "800"))
WRAP_COVERAGE = float(os.getenv("COACH_WRAP_COVERAGE", "0.8"))
WRAP_STAR = float(os.getenv("COACH_WRAP_STAR", "0.6"))
THINKING_ENABLED = os.getenv("COACH_THINKING", "0") == "1"
ORCH_FAIL_WINDOW = 600          # 编排连续失败统计窗口（秒）
ORCH_FAIL_LIMIT = 3             # 窗口内失败次数上限 → 降级规则编排
_orch_failures = []             # 模块级失败时间戳（单实例 FC 够用）

_EXP_GROUPS = ("internships", "projects")   # 经历承载类槽位（skills 属条目组但非经历）

# Budget admission and reservations use the same counter at the model-call layer.
# Never charge only the final answer: failed/repair/fallback calls cost money too.
def budget_ok() -> bool:
    try:
        from . import model_budget
    except ImportError:  # FC flat-module entry point
        import model_budget
    return model_budget.available()


def _prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _path_get(obj, path):
    cur = obj
    for part in (path or "").split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _path_set(obj, path, value):
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


# ---------------- 覆盖度与画像（§6：单一事实源 = coverage_detail.filled + profile） ----------------

def _exp_types_for(slot):
    if slot.id == "internships":
        return {"internship"}
    if slot.id == "projects":
        return {"project"} | set(slot.equivalents or [])
    return set()


def _star_score(exp: dict) -> float:
    """情境/任务、行动、结果、量化 四项各 1 分归一化（§6）。"""
    hits = sum(1 for k in ("situation", "actions", "results", "metrics") if exp.get(k))
    return round(hits / 4, 3)


def _slot_filled(slot, profile, detail) -> bool:
    filled = (detail or {}).get("filled") or {}
    if slot.id in filled:
        return True
    if slot.kind == "entry_group":
        if slot.id == "skills":
            return bool(profile.get("skills"))
        types = _exp_types_for(slot)
        exps = [e for e in profile.get("experiences", []) if e.get("type") in types]
        need = slot.min_entries or 1
        if slot.id == "projects":
            # 无实习时项目要求上浮（与简历契约 adaptive_min 对齐）
            tpl = _load_tpl()
            need = max(need, adaptive_min_entries(tpl, "projects", profile))
        return len(exps) >= need
    return bool(_path_get(profile, slot.id))


def coverage_of(profile, detail):
    """→ (coverage, gaps[slot_id], filled_n, total_n)。coverage 唯一事实源由此计算。"""
    tpl = _load_tpl()
    req = required_slots(tpl, profile)
    filled_n = sum(1 for s in req if _slot_filled(s, profile, detail))
    gaps = [s.id for s in req if not _slot_filled(s, profile, detail)]
    total = max(1, len(req))
    return round(filled_n / total, 3), gaps, filled_n, len(req)


def _best_star(profile) -> float:
    return max((_star_score(e) for e in profile.get("experiences", [])), default=0)


def _no_exp_possible(profile, detail) -> bool:
    """经历类必填槽位全部"已确认无"或不适用 → star 深挖无从谈起，豁免 star 门槛。"""
    tpl = _load_tpl()
    filled = (detail or {}).get("filled") or {}
    for s in required_slots(tpl, profile):
        if s.id not in _EXP_GROUPS:
            continue
        if not (filled.get(s.id) or {}).get("denied"):
            return False
    return any(s.id in _EXP_GROUPS for s in tpl.interview_slots)


def should_wrap(profile, detail, turn_no: int) -> bool:
    """§6：coverage≥0.8 且（star≥0.6 或经历槽位全否认豁免），或 12 轮上限。"""
    cov, gaps, _, _ = coverage_of(profile, detail)
    if cov >= WRAP_COVERAGE and (_best_star(profile) >= WRAP_STAR
                               or _no_exp_possible(profile, detail)):
        return True
    return turn_no >= MAX_TURNS


# ---------------- L1 规则（命中即走，不调编排模型） ----------------

CHIP_START, CHIP_ASK = "开始访谈", "直接提问"
_SKIP = re.compile(r"^(跳过|下一个|换话题|不想说|不知道|略过)")
_QUESTION = re.compile(r"[？?]|怎么|如何|什么|哪|为什么|有没有|是否|能不能|可以.*吗|吗[。！!]?$")
_ACK = re.compile(r"^(你好|您好|hi|hello|嗨|哈喽|在吗|谢谢|thanks|早|晚上好|好的?|嗯嗯?|ok|行|可以)[。！!~？?]*$", re.I)


def _first_gap_slot(profile, detail):
    tpl = _load_tpl()
    for s in required_slots(tpl, profile):
        if not _slot_filled(s, profile, detail):
            return s
    return None


def _l1(session, text, profile, detail):
    """命中返回决策 dict（含 l1 标记）；未命中返回 None 交编排器。"""
    t = (text or "").strip()
    stage = session.get("stage", "greeting")
    if t == CHIP_START:
        s = _first_gap_slot(profile, detail)
        return {"act": "ask", "need_kb": False, "focus_slot": s.id if s else None,
                "reason": "用户选择开始访谈", "deep": True, "l1": "chip_start"}
    if t == CHIP_ASK:
        return {"act": "answer", "need_kb": False, "focus_slot": None,
                "reason": "用户选择直接提问", "deep": False, "l1": "chip_ask"}
    if _SKIP.match(t):
        s = _first_gap_slot(profile, detail)
        return {"act": "ask", "need_kb": False, "focus_slot": s.id if s else None,
                "reason": "用户要求跳过", "deep": False, "l1": "skip"}
    if _ACK.match(t) and len(t) <= 10:
        return {"act": "ack", "need_kb": False, "focus_slot": None,
                "reason": "寒暄/确认", "deep": False, "l1": "ack"}
    if stage == "done":
        return {"act": "answer", "need_kb": bool(_QUESTION.search(t)),
                "focus_slot": None, "reason": "访谈已收尾，按问答处理", "deep": False, "l1": "done"}
    return None


def _rule_decide(text, profile, detail):
    if _QUESTION.search(text or ""):
        return {"act": "answer", "need_kb": True, "focus_slot": None,
                "reason": "规则判定为提问", "deep": False, "degraded": True}
    s = _first_gap_slot(profile, detail)
    if s is None:
        return {"act": "wrap_up", "need_kb": False, "focus_slot": None,
                "reason": "必填槽位已齐", "deep": False, "degraded": True}
    return {"act": "ask", "need_kb": False, "focus_slot": s.id,
            "reason": "规则选取最缺槽位", "deep": True, "degraded": True}


# ---------------- 编排器（turbo JSON + 校验 + 修复重试 + 连败降级） ----------------

def _orch_degraded() -> bool:
    now = time.monotonic()
    recent = [t for t in _orch_failures if now - t < ORCH_FAIL_WINDOW]
    del _orch_failures[:]
    _orch_failures.extend(recent)
    return len(recent) >= ORCH_FAIL_LIMIT


def _validate_plan(data, valid_slots):
    if not isinstance(data, dict):
        raise ValueError("plan not object")
    act = data.get("act")
    if act not in ("ask", "answer", "wrap_up"):
        raise ValueError("bad act")
    focus = data.get("focus_slot")
    if focus is not None and focus not in valid_slots:
        focus = None
    return {"act": act, "need_kb": bool(data.get("need_kb")),
            "focus_slot": focus, "reason": str(data.get("reason", ""))[:120],
            "deep": bool(data.get("deep"))}


def _orchestrate(text, profile, detail, recent):
    if _orch_degraded():
        return {**_rule_decide(text, profile, detail), "degraded": True, "circuit": True}
    tpl = _load_tpl()
    slots = required_slots(tpl, profile)
    slot_lines = "\n".join(
        f"- {s.id}（{s.label}）：{'已收集' if _slot_filled(s, profile, detail) else '缺口'} · 提示:{s.ask or s.label}"
        for s in slots)
    ctx = "\n".join(f"{m.get('role')}:{(m.get('content') or '')[:120]}" for m in recent[-4:])
    prompt = [
        {"role": "system", "content":
         _prompt("orchestrator.md") +
         f"\n\n必填 slot 列表与状态：\n{slot_lines}\n\n"
         f"当前画像：{json.dumps(profile, ensure_ascii=False)[:1500]}\n"
         f"最近对话：\n{ctx or '（无）'}"},
        {"role": "user", "content": text},
    ]
    try:
        data = llm.chat_json(prompt, model=QWEN_ROUTER, retries=1, max_tokens=300)
        return _validate_plan(data, {s.id for s in tpl.interview_slots})
    except Exception:
        _orch_failures.append(time.monotonic())
        return _rule_decide(text, profile, detail)


# ---------------- 检索（CRAG：低分重写 query 重试 1 次） ----------------

def _search(query):
    try:
        qvec = llm.embed(query)
    except Exception:
        qvec = None
    return kb.search(qvec, query, "all", 8)


def _retrieve(text):
    hits = _search(text)
    best = max((h.get("score", 0) for h in hits), default=0)
    retried = False
    if hits and best < MIN_SCORE:
        retried = True
        try:
            q2 = llm.chat(
                [{"role": "user", "content":
                  f"把这个问题改写成更适合知识库检索的短句（≤20字，只输出句子）：{text[:200]}"}],
                model=QWEN_ROUTER, temperature=0, max_tokens=40).strip() or text
        except Exception:
            q2 = text
        hits2 = _search(q2[:64])
        if hits2 and max(h.get("score", 0) for h in hits2) > best:
            hits, best = hits2, max(h.get("score", 0) for h in hits2)
    return hits, best, retried


# ---------------- 抽取与画像合并（红线：quote 须为用户原话） ----------------

SCALAR_SLOT_PATH = {
    "identity.grade": "identity.grade",
    "identity.school": "identity.school",
    "identity.major": "identity.major",
    "basics.target_role": "goal.target_role",
    "summary": "summary_material",
}


def _current_user_text(messages):
    """The pure merge API treats only the last user message as this turn's evidence."""
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            return m.get("content") if isinstance(m.get("content"), str) else ""
    return ""


def _user_quote_ok(quote, messages) -> bool:
    return (isinstance(quote, str) and bool(_norm(quote))
            and _norm(quote) in _norm(_current_user_text(messages)))


def _validate_extraction(data):
    """Strict, bounded JSON schema, with no coercion or unknown properties.

    Validation is atomic: a malformed model response is never partially applied.
    Bounds also cap work/storage before any evidence checks or profile changes.
    """
    schemas = {
        "slot_updates": ({"slot": "text", "value": "text", "quote": "text"},
                         {"slot", "value", "quote"}),
        "denials": ({"slot": "text", "quote": "text"}, {"slot", "quote"}),
        "experiences": ({"type": "type", "title": "text", "quote": "text",
                         "role": "text", "period": "text", "situation": "text",
                         "actions": "texts", "results": "texts", "metrics": "texts"},
                        {"title", "quote"}),
        "skills": ({"group": "group", "items": "texts"}, {"items"}),
        "awards": ({"text": "text", "period": "text", "quote": "text"}, {"text"}),
    }
    types = {"internship", "project", "campus", "competition", "opensource", "research"}
    if type(data) is not dict or not set(data) <= set(schemas):
        raise ValueError("extraction must be a known-key object")
    total = 0
    for key, rows in data.items():
        if type(rows) is not list or len(rows) > 32:
            raise ValueError("extraction list bound/type")
        fields, required = schemas[key]
        for row in rows:
            if type(row) is not dict or not required <= set(row) or not set(row) <= set(fields):
                raise ValueError("extraction object fields")
            for field, value in row.items():
                kind = fields[field]
                values = value if kind == "texts" else [value]
                if kind == "texts" and (type(value) is not list or len(value) > 32):
                    raise ValueError("extraction text list bound/type")
                for text in values:
                    bound = 80 if kind == "group" else 2000
                    if type(text) is not str or len(text) > bound:
                        raise ValueError("extraction text bound/type")
                    if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
                        raise ValueError("extraction control character")
                    if kind == "group" and any(ord(c) < 32 for c in text):
                        raise ValueError("unsafe group")
                    total += len(text)
                if kind == "type" and value not in types:
                    raise ValueError("extraction experience type")
                if total > 32000:
                    raise ValueError("extraction total text bound")
    return data


def _merge_experience(exps, item):
    """Merge already evidence-filtered fields; confirmed records remain immutable."""
    title = _norm(item.get("title"))
    if not title:
        return exps, False
    for e in exps:
        if not isinstance(e, dict) or not isinstance(e.get("title"), str):
            continue
        if _norm(e["title"]) == title:
            if e.get("confirmed"):
                return exps, False
            for k in ("type", "role", "period", "situation"):
                if item.get(k) and not e.get(k):
                    e[k] = item[k]
            for k in ("actions", "results", "metrics"):
                previous = e.get(k) or []
                if isinstance(previous, str):
                    previous = [previous]
                if isinstance(previous, list) and all(isinstance(x, str) for x in previous):
                    e[k] = list(dict.fromkeys(previous + item.get(k, [])))
            e["star_score"] = _star_score(e)
            return exps, False
    if not item.get("type"):
        return exps, False
    exp = {k: item[k] for k in
           ("type", "title", "role", "period", "situation", "actions", "results", "metrics")
           if k in item}
    exp["star_score"] = _star_score(exp)
    return exps + [exp], True


def apply_extraction(profile, detail, data, messages, valid_slots):
    """Pure merge API; only this turn's extractive evidence may introduce facts."""
    profile = json.loads(json.dumps(profile or {}, ensure_ascii=False))
    detail = json.loads(json.dumps(detail or {}, ensure_ascii=False))
    filled = dict(detail.get("filled") or {})
    detail["filled"] = filled
    updates = []
    try:
        _validate_extraction(data)
    except ValueError:
        return profile, detail, updates
    current = _current_user_text(messages)

    def supported(value):
        return isinstance(value, str) and bool(_norm(value)) and _norm(value) in _norm(current)

    def locked(slot):
        # User-confirmed (quote-backed) values are immutable. A denied slot can be
        # overturned by real evidence, and derived counters are only display state:
        # they must not freeze the whole group against later turns.
        entry = filled.get(slot)
        if isinstance(entry, dict):
            return not entry.get("denied") and not entry.get("derived")
        return slot in filled

    for su in data.get("slot_updates", []):
        slot, value, quote = su["slot"], su["value"], su["quote"]
        # Group evidence is handled by its own typed branch, not scalar updates.
        if (slot not in valid_slots or slot not in SCALAR_SLOT_PATH or locked(slot)
                or not _user_quote_ok(quote, messages) or not supported(value)
                or _norm(value) not in _norm(quote)):
            continue
        path = SCALAR_SLOT_PATH[slot]
        parent = path.split(".")[0]
        if "." in path and parent in profile and not isinstance(profile[parent], dict):
            continue  # Legacy malformed containers are not silently migrated.
        filled[slot] = {"value": value, "quote": quote}
        _path_set(profile, path, value)
        updates.append({"slot": slot, "value": value})

    for denial in data.get("denials", []):
        slot, quote = denial["slot"], denial["quote"]
        if (slot in valid_slots and slot not in filled and _user_quote_ok(quote, messages)
                and re.search(r"没有|没做过|不熟悉|无|\bno\b|\bnever\b", quote, re.I)):
            filled[slot] = {"value": "无", "denied": True, "quote": quote}
            updates.append({"slot": slot, "value": "（已确认无）"})

    for item in data.get("experiences", []):
        if not _user_quote_ok(item["quote"], messages) or not supported(item["title"]):
            continue
        old_exps = profile.get("experiences") or []
        if not isinstance(old_exps, list):
            continue
        existing = next((e for e in old_exps if isinstance(e, dict)
                         and isinstance(e.get("title"), str)
                         and _norm(e["title"]) == _norm(item["title"])), None)
        exp_type = (existing or {}).get("type") or item.get("type")
        group = "internships" if exp_type == "internship" else "projects"
        if locked(group) or locked("experiences") or (existing or {}).get("confirmed"):
            continue
        safe = {"title": item["title"]}
        if item.get("type"):
            safe["type"] = item["type"]  # Classification enum, not a claimed user phrase.
        for k in ("role", "period", "situation"):
            if supported(item.get(k)):
                safe[k] = item[k]
        for k in ("actions", "results", "metrics"):
            safe[k] = [v for v in item.get(k, []) if supported(v)]
        before = json.dumps(old_exps, ensure_ascii=False)
        exps, is_new = _merge_experience(old_exps, safe)
        if json.dumps(exps, ensure_ascii=False) != before:
            profile["experiences"] = exps
            updates.append({"slot": "experiences",
                            "value": ("新增" if is_new else "补充") + f"经历:{item['title'][:20]}"})

    # Only derived counters / denials can change; explicit filled evidence is immutable.
    tpl = _load_tpl()
    for s in tpl.interview_slots:
        if s.id not in _EXP_GROUPS:
            continue
        exps = profile.get("experiences") or []
        if not isinstance(exps, list):
            continue
        n = sum(1 for e in exps if isinstance(e, dict) and e.get("type") in _exp_types_for(s))
        ent = filled.get(s.id)
        ent = ent if isinstance(ent, dict) else {}
        label = f"{n}段经历"
        if n and (s.id not in filled or ent.get("denied") or ent.get("derived")):
            if ent.get("value") != label or ent.get("denied"):
                filled[s.id] = {"value": label, "derived": True}
                updates.append({"slot": s.id, "value": label})

    if not locked("skills") and "skills" in valid_slots:
        for g in data.get("skills", []):
            items = [x for x in g["items"] if supported(x)]
            if not items:
                continue
            skills = profile.get("skills") or []
            if not isinstance(skills, list):
                continue
            skills = [s for s in skills if isinstance(s, dict)]
            name = g.get("group") or "其他"
            grp = next((s for s in skills if s.get("group") == name), None)
            if grp is None:
                grp = {"group": name, "items": []}
                skills.append(grp)
            previous = grp.get("items", [])
            if not isinstance(previous, list) or not all(isinstance(x, str) for x in previous):
                continue
            grp["items"] = list(dict.fromkeys(previous + items))
            profile["skills"] = skills
            filled["skills"] = {"value": "、".join(grp["items"][:8]), "derived": True}
            updates.append({"slot": "skills", "value": "、".join(items[:4])})

    if not locked("awards") and "awards" in valid_slots:
        for a in data.get("awards", []):
            if (not supported(a["text"]) or (a.get("period") and not supported(a["period"]))
                    or (a.get("quote") and not _user_quote_ok(a["quote"], messages))):
                continue
            awards = profile.setdefault("awards", [])
            if not isinstance(awards, list):
                continue
            if not any(isinstance(x, dict) and x.get("text") == a["text"] for x in awards):
                awards.append({"text": a["text"], "period": a.get("period", "")})
                filled["awards"] = {"value": a["text"], "quote": a["text"]}
                updates.append({"slot": "awards", "value": a["text"][:20]})

    summary = profile.get("summary_material")
    if isinstance(summary, str) and summary:
        filled.setdefault("summary", {"value": summary[:20], "derived": True})
    return profile, detail, updates


def _extract(user_text, profile, detail, messages):
    """→ (new_profile, new_detail, updates, raw_data); invalid output degrades closed."""
    tpl = _load_tpl()
    slot_ids = "\n".join(f"- {s.id}（{s.label}）" for s in tpl.interview_slots)
    prompt = [
        {"role": "system", "content":
         _prompt("extractor.md") +
         "\n严格使用上述字段和类型；每数组至多32项，每字符串至多2000字。"
         "所有事实值必须逐字来自本轮用户消息，不可改写或引用历史轮次。"
         f"\n\n可选 slot：\n{slot_ids}\n\n当前画像（勿覆盖用户已确认字段）："
         f"{json.dumps(profile, ensure_ascii=False)[:1200]}"},
        {"role": "user", "content": user_text},
    ]
    try:
        data = _validate_extraction(
            llm.chat_json(prompt, model=QWEN_ROUTER, retries=1, max_tokens=500))
    except Exception:
        return profile, detail, [], {}
    valid = {s.id for s in tpl.interview_slots}
    p, d, u = apply_extraction(profile, detail, data,
                               [{"role": "user", "content": user_text}], valid)
    return p, d, u, data


# ---------------- 生成（interviewer / answer / wrapup） ----------------

def _profile_brief(profile):
    """Best-effort display of legacy data, not a migration or schema guarantee."""
    if not isinstance(profile, dict):
        return "画像为空"
    parts = []
    ident = profile.get("identity")
    if isinstance(ident, dict):
        values = [v for v in ident.values() if isinstance(v, str) and v]
        if values:
            parts.append("身份:" + "/".join(values))
    goal = profile.get("goal")
    goal = goal.get("target_role") if isinstance(goal, dict) else None
    if isinstance(goal, str) and goal:
        parts.append(f"目标:{goal}")
    for key, label, field, sep in (("experiences", "经历:", "title", "；"),
                                    ("skills", "技能:", "group", "/")):
        rows = profile.get(key)
        if isinstance(rows, list):
            values = [row[field] for row in rows if isinstance(row, dict)
                      and isinstance(row.get(field), str) and row[field]]
            if values:
                parts.append(label + sep.join(values))
    return "；".join(parts) or "画像为空"


def _sys_for(plan, profile, detail, kb_ctx, tpl):
    if plan["act"] == "wrap_up":
        return _prompt("wrapup.md") + f"\n\n用户素材：{_profile_brief(profile)}\n画像JSON：{json.dumps(profile, ensure_ascii=False)[:1200]}"
    if plan["act"] == "answer":
        extra = _prompt("answer.md")
        if kb_ctx:
            extra += f"\n\n知识库资料：\n{kb_ctx}"
        return extra + f"\n\n用户画像：{json.dumps(profile, ensure_ascii=False)[:800]}"
    # ask / ack → 访谈人格
    focus = plan.get("focus_slot")
    slot = next((s for s in tpl.interview_slots if s.id == focus), None)
    focus_txt = f"\n本轮重点：围绕「{slot.label}」追问（参考问法：{slot.ask}）。一次只问一个点。" if slot else ""
    kb_txt = f"\n可用知识钩子（自然带出，不要硬背）：\n{kb_ctx}" if kb_ctx else ""
    gaps = [s.label for s in required_slots(tpl, profile)
            if not _slot_filled(s, profile, detail)]
    return (_prompt("coach_system.md") +
            f"\n\n当前用户画像：{json.dumps(profile, ensure_ascii=False)[:1200]}"
            f"\n还缺的信息：{'、'.join(gaps) or '基本齐了'}"
            f"{focus_txt}{kb_txt}")


def _reasoning(plan, profile, detail):
    if plan["act"] == "ack":
        return "寒暄回应。"
    if plan["act"] == "answer":
        return "用户在提问，先给答案，答完顺势带回访谈。"
    if plan["act"] == "wrap_up":
        return "素材足够成稿，做收尾并引导建简历。"
    focus = plan.get("focus_slot")
    return f"已收集：{_profile_brief(profile)}；接下来聚焦 {focus or '收尾确认'}。"


# ---------------- 主循环（产出事件元组） ----------------

GREETING_TEXT = (
    "嗨，我是知脉，你的简历成长教练。我可以陪你聊一段短短的访谈，"
    "把能写进简历的经历一点点挖出来；你也可以跳过访谈直接提问，我照常回答。"
)


def greeting_events():
    """新会话招呼流（#13）：招呼文 + 两枚引导 chips。"""
    yield ("token", GREETING_TEXT)
    yield ("card", {"type": "suggest_reply",
                    "payload": {"replies": [CHIP_START, CHIP_ASK]}})


def _persist_merge(session_id, new_profile, new_detail, version):
    cov = coverage_of(new_profile, new_detail)[0]
    merged = memory.merge_profile(
        session_id, new_profile, version, coverage=cov,
        coverage_detail=new_detail)
    return merged


def turn_events(session, profile_row, messages, user_text):
    """一轮访谈事件流。产出 (event,data)：
    step_*/profile/card/sources/reasoning_summary/token 供 router 转 SSE；
    ("stage", x) 为内部控制信号（router 拦截后写库，不透出）。"""
    profile = dict(profile_row.get("profile") or {})
    detail = dict(profile_row.get("coverage_detail") or {})
    version = int(profile_row.get("version") or 0)
    seq = 0
    cov, gaps, _, _ = coverage_of(profile, detail)

    seq += 1
    yield ("step_start", {"id": "s1", "seq": seq, "kind": "recall", "title": "回顾记忆"})
    yield ("step_end", {"id": "s1", "seq": seq, "status": "done",
                        "detail": _profile_brief(profile)[:120]})

    plan = _l1(session, user_text, profile, detail)
    if plan is None:
        plan = _orchestrate(user_text, profile, detail, messages)
    seq += 1
    yield ("step_start", {"id": "s2", "seq": seq, "kind": "plan", "title": "判断本轮动作"})
    plan_detail = f"act={plan['act']}" + (f" · focus={plan['focus_slot']}" if plan.get("focus_slot") else "") \
        + (" · 规则兜底" if plan.get("degraded") else "")
    yield ("step_end", {"id": "s2", "seq": seq, "status": "done", "detail": plan_detail})

    hits, ok = [], False
    if plan["act"] != "ack" and plan.get("need_kb") and user_text:
        seq += 1
        yield ("step_start", {"id": "s3", "seq": seq, "kind": "retrieve", "title": "检索知识库"})
        hits, best, retried = _retrieve(user_text)
        yield ("step_end", {"id": "s3", "seq": seq, "status": "done",
                            "detail": f"命中 {len(hits)} 段 · 最高分 {round(best, 3)}"
                                      + (" · 已重写重试" if retried else "")})
        seq += 1
        yield ("step_start", {"id": "s4", "seq": seq, "kind": "grade", "title": "相关性校验"})
        ok = best >= MIN_SCORE
        yield ("step_end", {"id": "s4", "seq": seq, "status": "done",
                            "detail": "相关性达标" if ok else "相关性偏低，谨慎作答"})
        if ok and hits:
            yield ("sources", [{"title": h.get("title"), "url": h.get("source_url"),
                                "seq": h.get("seq"), "score": round(h.get("score", 0), 3)}
                               for h in hits[:5]])

    yield ("reasoning_summary", _reasoning(plan, profile, detail))

    tpl = _load_tpl()
    # 低分命中不进 answer 上下文（grade 校验否则形同虚设），sources 同步不出
    kb_ctx = "\n\n---\n\n".join(h.get("text", "") for h in hits[:4]) if ok and hits else ""
    llm_msgs = [{"role": "system", "content": _sys_for(plan, profile, detail, kb_ctx, tpl)}]
    llm_msgs += [{"role": m.get("role", "user"), "content": m.get("content", "")}
                 for m in messages[-RECENT_MSGS:] if m.get("role") in ("user", "assistant")]
    if user_text == CHIP_ASK:
        yield ("token", "好，直接问吧——规划、实习、简历写法都可以，我答完不打岔。")
    elif plan["act"] == "ack":
        yield ("token", "在呢。想继续聊经历，还是问点什么？")
    else:
        # thinking 实测首 token +9s（spike-编排.md）——默认关，COACH_THINKING=1 才在深轮开
        for tok in llm.chat_stream(
                llm_msgs, model=QWEN_CHAT, max_tokens=MAX_OUTPUT_TOKENS,
                enable_thinking=THINKING_ENABLED and bool(plan.get("deep"))):
            yield ("token", tok)

    # 抽取与画像合并（流后跑；ack 轮跳过省 token）
    updates = []
    if plan["act"] != "ack":
        seq += 1
        yield ("step_start", {"id": "s5", "seq": seq, "kind": "profile", "title": "更新画像"})
        new_profile, new_detail, updates, raw = _extract(user_text, profile, detail, messages)
        merged = None
        try:
            merged = _persist_merge(session["id"], new_profile, new_detail, version)
        except memory.ProfileConflict:
            prof2 = memory.get_profile(session["id"]) or {}
            # 复用同一抽取结果在新基线上重放，不再调模型
            new_profile, new_detail, updates = apply_extraction(
                prof2.get("profile") or {}, prof2.get("coverage_detail") or {},
                raw, messages, {s.id for s in tpl.interview_slots})
            try:
                merged = _persist_merge(session["id"], new_profile, new_detail,
                                        prof2.get("version", 0))
            except memory.ProfileConflict:
                updates = []      # 二次冲突：放弃本轮画像更新，不阻塞回复
        if merged is not None:
            profile = merged["profile"]
            detail = merged.get("coverage_detail") or {}
        cov, gaps, _, _ = coverage_of(profile, detail)
        yield ("step_end", {"id": "s5", "seq": seq, "status": "done",
                            "detail": " · ".join("+" + u["slot"] + "=" + str(u["value"])[:24]
                                                for u in updates) if updates else "无新事实"})
        yield ("profile", {"coverage": cov, "gaps": gaps, "updates": updates})

        turn_no = sum(1 for m in messages if m.get("role") == "user")
        if plan["act"] != "wrap_up" and should_wrap(profile, detail, turn_no):
            plan = {"act": "wrap_up", "reason": "覆盖达标或轮数到顶", "auto": True}
            seq += 1
            yield ("step_start", {"id": "s6", "seq": seq, "kind": "guide", "title": "收尾引导"})
            yield ("step_end", {"id": "s6", "seq": seq, "status": "done",
                                "detail": f"coverage={cov} · turn={turn_no}"})
            yield ("token", "\n\n")
            for tok in llm.chat_stream(
                    [{"role": "system", "content": _sys_for(plan, profile, detail, "", tpl)},
                     {"role": "user", "content": "访谈到这里，帮我做个收尾总结。"}],
                    model=QWEN_CHAT, max_tokens=MAX_OUTPUT_TOKENS):
                yield ("token", tok)

    if plan["act"] == "wrap_up":
        yield ("stage", "wrapping")
        yield ("card", {"type": "goto_resume",
                        "payload": {"label": "去一键填写简历", "session_id": session["id"]}})
        yield ("stage", "done")
    elif session.get("stage") == "greeting":
        yield ("stage", "probing")
