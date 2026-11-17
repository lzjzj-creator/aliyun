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

# §10 全局日 token 预算：进程内近似计数（token≈字/3）。单实例近似闸门，
# 多实例下为每实例口径——精确全局预算需共享计数器，部署清单已注明。
DAILY_TOKEN_BUDGET = int(os.getenv("COACH_DAILY_TOKEN_BUDGET", "200000"))
_day_budget = {"date": "", "used": 0}


def budget_ok() -> bool:
    today = time.strftime("%Y-%m-%d")
    return _day_budget["date"] != today or _day_budget["used"] < DAILY_TOKEN_BUDGET


def charge_tokens(approx_chars: int) -> None:
    today = time.strftime("%Y-%m-%d")
    if _day_budget["date"] != today:
        _day_budget["date"] = today
        _day_budget["used"] = 0
    _day_budget["used"] += max(0, approx_chars // 3)


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


def _user_quote_ok(quote, messages) -> bool:
    q = _norm(quote)
    return bool(q) and any(q in _norm(m.get("content", ""))
                           for m in messages if m.get("role") == "user")


def _merge_experience(exps, item):
    """按标题归并：同题经历并入补细节；新经历追加。返回 (exps, 是否新增)。"""
    title = _norm(item.get("title"))
    if not title:
        return exps, False
    for e in exps:
        if _norm(e.get("title")) == title:
            for k in ("type", "role", "period", "situation"):
                if item.get(k):
                    e[k] = e.get(k) or item[k]
            for k in ("actions", "results", "metrics"):
                v = item.get(k)
                if isinstance(v, list):
                    e[k] = list(dict.fromkeys((e.get(k) or []) + [str(x) for x in v]))
                elif isinstance(v, str) and v.strip():
                    e[k] = list(dict.fromkeys((e.get(k) or []) + [v]))
            e["star_score"] = _star_score(e)
            return exps, False
    exp = {k: item.get(k) for k in
           ("type", "title", "role", "period", "situation", "actions", "results", "metrics")}
    exp["star_score"] = _star_score(exp)
    return exps + [exp], True


def apply_extraction(profile, detail, data, messages, valid_slots):
    """纯函数：抽取结果经 quote 校验后合并进 (profile, detail)。返回 (profile, detail, updates)。"""
    profile = json.loads(json.dumps(profile or {}, ensure_ascii=False))
    detail = dict(detail or {})
    filled = dict(detail.get("filled") or {})
    updates = []

    for su in data.get("slot_updates") or []:
        if not isinstance(su, dict):
            continue
        slot, value, quote = su.get("slot"), su.get("value"), su.get("quote", "")
        if slot not in valid_slots or value in (None, "") or not _user_quote_ok(quote, messages):
            continue
        ent = filled.get(slot)
        if ent and not (isinstance(ent, dict) and ent.get("denied")):
            continue            # 已确认字段不被覆盖（§6）；否认过的可被新值更正
        filled[slot] = {"value": value, "quote": quote}
        if slot in SCALAR_SLOT_PATH:
            _path_set(profile, SCALAR_SLOT_PATH[slot], value)
        updates.append({"slot": slot, "value": value})

    for d_ in data.get("denials") or []:
        # denials 也要 quote 证据（用户明确说过"没有"），防模型虚构缺口抬覆盖率
        if not isinstance(d_, dict):
            continue
        slot, quote = d_.get("slot"), d_.get("quote", "")
        if slot in valid_slots and slot not in filled and _user_quote_ok(quote, messages):
            filled[slot] = {"value": "无", "denied": True, "quote": quote}
            updates.append({"slot": slot, "value": "（已确认无）"})

    for item in data.get("experiences") or []:
        if not isinstance(item, dict) or not _user_quote_ok(item.get("quote", ""), messages):
            continue
        exps = list(profile.get("experiences") or [])
        exps, is_new = _merge_experience(exps, item)
        profile["experiences"] = exps
        updates.append({"slot": "experiences",
                        "value": ("新增" if is_new else "补充") + f"经历:{item.get('title', '')[:20]}"})
    # 经历可推翻"已确认无"：按当前经历内容重算经历类槽位 filled（用户原话写的值不覆盖）
    tpl = _load_tpl()
    for s in tpl.interview_slots:
        if s.id not in _EXP_GROUPS:
            continue
        types = _exp_types_for(s)
        n = sum(1 for e in profile.get("experiences", []) if e.get("type") in types)
        ent = filled.get(s.id)
        ent = ent if isinstance(ent, dict) else {}
        label = f"{n}段经历"
        stale_counter = "quote" not in ent and ent.get("value") != label
        if n and (ent.get("denied") or stale_counter):
            filled[s.id] = {"value": label, "derived": True}
            updates.append({"slot": s.id, "value": label})

    for g in data.get("skills") or []:
        if not isinstance(g, dict) or not g.get("items"):
            continue
        # 逐条红线：技能词必须能在用户原话中找到，防模型塞未提及技能
        items = [str(x) for x in g["items"]
                 if _user_quote_ok(str(x), messages)]
        if not items:
            continue
        skills = [s for s in (profile.get("skills") or []) if isinstance(s, dict)]
        name = g.get("group") or "其他"
        grp = next((s for s in skills if s.get("group") == name), None)
        if grp is None:
            grp = {"group": name, "items": []}
            skills.append(grp)
        grp["items"] = list(dict.fromkeys(grp.get("items", []) + items))
        profile["skills"] = skills
        filled["skills"] = {"value": "、".join(grp["items"][:8])}
        updates.append({"slot": "skills", "value": "、".join(items[:4])})

    for a in data.get("awards") or []:
        if (isinstance(a, dict) and a.get("text")
                and _user_quote_ok(str(a["text"]), messages)):
            awards = profile.setdefault("awards", [])
            if not any(x.get("text") == a["text"] for x in awards):
                awards.append({"text": str(a["text"]), "period": str(a.get("period", ""))})
                filled["awards"] = {"value": str(a["text"])}
                updates.append({"slot": "awards", "value": str(a["text"])[:20]})

    if profile.get("summary_material"):
        filled.setdefault("summary", {"value": profile["summary_material"][:20]})
    detail["filled"] = filled
    return profile, detail, updates


def _extract(user_text, profile, detail, messages):
    """→ (new_profile, new_detail, updates, raw_data)。raw_data 供 CAS 冲突时免模型重放。"""
    tpl = _load_tpl()
    slot_ids = "\n".join(f"- {s.id}（{s.label}）" for s in tpl.interview_slots)
    prompt = [
        {"role": "system", "content":
         _prompt("extractor.md") +
         f"\n\n可选 slot：\n{slot_ids}\n\n当前画像（勿覆盖用户已确认字段）："
         f"{json.dumps(profile, ensure_ascii=False)[:1200]}"},
        {"role": "user", "content": user_text},
    ]
    try:
        data = llm.chat_json(prompt, model=QWEN_ROUTER, retries=1, max_tokens=500)
    except Exception:
        return profile, detail, [], {}
    valid = {s.id for s in tpl.interview_slots}
    p, d, u = apply_extraction(profile, detail, data, messages, valid)
    return p, d, u, data


# ---------------- 生成（interviewer / answer / wrapup） ----------------

def _profile_brief(profile):
    parts = []
    ident = profile.get("identity") or {}
    if any(ident.values()):
        parts.append("身份:" + "/".join(str(v) for v in ident.values() if v))
    goal = (profile.get("goal") or {}).get("target_role")
    if goal:
        parts.append(f"目标:{goal}")
    exps = profile.get("experiences") or []
    if exps:
        parts.append("经历:" + "；".join(e.get("title", "?") for e in exps))
    if profile.get("skills"):
        parts.append("技能:" + "/".join(g.get("group", "") for g in profile["skills"]))
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

    hits = []
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
    kb_ctx = "\n\n---\n\n".join(h.get("text", "") for h in hits[:4]) if hits else ""
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
