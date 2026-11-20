"""v3 incremental fact extraction pipeline (plan §6).

Extractive only: a persisted fact's value is a verbatim substring of the quote,
and the quote is a verbatim substring of a real user message in the increment.
Every server-side check is hard—one failure drops that fact entirely. Model or
storage failures only bump v3_extraction_state.attempts (chat never blocks);
three consecutive failures mark the session blocked instead of retry-storming.
"""
import json
import re
from pathlib import Path

try:
    from .llm import llm, QWEN_ROUTER
    from . import memory, memory_v3
    from .resume_template import load as load_template, TemplateError
except ImportError:  # FC runs with backend as root
    from llm import llm, QWEN_ROUTER
    import memory
    import memory_v3
    from resume_template import load as load_template, TemplateError

PROMPT = (Path(__file__).resolve().parent / "prompts" / "v3_extractor.md").read_text(
    encoding="utf-8")

AUTO_CONFIRM_KINDS = {"skill", "education", "identity"}
MAX_FACTS_PER_ROUND = 20
MAX_VALUE_CHARS = 512
MAX_ATTEMPTS = 3

# Sensitive-value denylist: matched anywhere in value/quote -> whole fact dropped.
DENY_PATTERNS = [
    re.compile(r"\d{17}[\dXx]"),                     # 身份证号
    re.compile(r"\d{15}(?!\d)"),                     # 旧版 15 位身份证
    re.compile(r"\d{16,19}"),                        # 银行卡号
    re.compile(r"身份证|银行卡|卡号"),
    re.compile(r"(省|自治区).{0,30}(市|州|盟).{0,30}(区|县|旗).{0,30}(路|街|巷|弄|号|栋|幢|单元|室|村)"),
    re.compile(r"(政治面貌|共青团员|中共党员|党员|民主党派|群众面貌)"),
    re.compile(r"(病历|确诊|抑郁症?|艾滋病|精神病|残疾证|慢性病|宗教信仰|信佛|基督教|伊斯兰教)"),
]


class ExtractionFailed(RuntimeError):
    """Model/pipeline failure after attempt bookkeeping; callers map to 502."""


def _norm(text) -> str:
    return re.sub(r"\s+", "", text or "")


def _sensitive(text: str) -> bool:
    return isinstance(text, str) and any(p.search(text) for p in DENY_PATTERNS)


def slot_whitelist() -> re.Pattern:
    """Allowed slot_path patterns derived from the template contract (§6 rule 3):
    kv `sec.field`; entries `sec.N.field` (+ index for bullets/tags);
    skillgroups `skills.groups.N.name|items(.N)`; lines `awards.items.N.text|period`;
    text `summary.text`; plus the free-form `goal` slot."""
    try:
        tpl = load_template()
    except TemplateError:
        tpl = None
    pats = [r"goal(?:\.[a-z_]+)?"]
    for sec in (tpl.sections if tpl else []):
        if sec.type == "kv":
            pats += [rf"{sec.key}\.{f.key}" for f in sec.fields]
        elif sec.type == "entries":
            for f in sec.entry_fields:
                if f.input in ("bullets", "tags"):
                    pats.append(rf"{sec.key}\.\d+\.{f.key}(?:\.\d+)?")
                else:
                    pats.append(rf"{sec.key}\.\d+\.{f.key}")
        elif sec.type == "skillgroups":
            pats += [rf"{sec.key}\.groups\.\d+\.name",
                     rf"{sec.key}\.groups\.\d+\.items(?:\.\d+)?"]
        elif sec.type == "lines":
            pats += [rf"{sec.key}\.items\.\d+\.(?:text|period)"]
        elif sec.type == "text":
            pats.append(rf"{sec.key}\.text")
    return re.compile(r"^(?:" + "|".join(pats) + r")$")


def _user_index(user_msgs):
    return {m["id"]: m for m in user_msgs
            if type(m.get("id")) is int and isinstance(m.get("content"), str)}


def _prompt_text(user_msgs, confirmed_slots):
    lines = [f"[msg_id={m['id']}] {m['content']}" for m in user_msgs]
    slots = "、".join(dict.fromkeys(confirmed_slots)) or "（无）"
    return (PROMPT + f"\n\n已知已确认槽位（仅槽位名，勿复述其值）：{slots}\n"
            f"用户消息：\n" + "\n".join(lines))


def _valid_candidate(item, fed_ids, user_by_id, whitelist):
    """Hard validation of one model candidate; returns normalized dict or None."""
    if not isinstance(item, dict):
        return None
    if not {"kind", "slot_path", "value", "msg_id", "quote"} <= set(item):
        return None
    kind, slot_path, value = item["kind"], item["slot_path"], item["value"]
    msg_id, quote = item["msg_id"], item["quote"]
    confidence = item.get("confidence", 0.5)
    if kind not in memory_v3.FACT_KINDS:
        return None
    if not isinstance(slot_path, str) or not whitelist.match(slot_path):
        return None
    if not isinstance(quote, str) or not quote.strip():
        return None
    if type(msg_id) is not int or msg_id not in fed_ids:
        return None
    source = user_by_id[msg_id]["content"]
    if quote not in source:                      # verbatim substring of THAT message
        return None
    value_json = json.dumps(value, ensure_ascii=False)
    if len(value_json) > MAX_VALUE_CHARS or not isinstance(value, (str, int, float, dict, list)) \
            or isinstance(value, bool):
        return None
    if isinstance(value, str):
        if not value.strip() or value not in quote:
            return None
    elif _norm(json.dumps(value, ensure_ascii=False)) not in _norm(quote):
        return None                     # non-string values must still be extractive
    if _sensitive(quote) or _sensitive(value_json):
        return None
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1):
        return None
    return {"kind": kind, "slot_path": slot_path, "value": value,
            "msg_id": msg_id, "quote": quote, "confidence": float(confidence)}


def extract_incremental(owner_id: str, session_id: str) -> dict:
    """Run one incremental extraction for (owner, session).

    Returns {added, merged, conflicts, skipped, denied, blocked?}.
    Raises V3NotFound (owner/session) or ExtractionFailed (model/storage)."""
    owner = memory_v3.get_owner(owner_id)
    if owner is None or not owner.get("memory_enabled"):
        raise memory_v3.V3NotFound("Owner not found")
    state = memory_v3.get_extraction_state(owner_id, session_id) or {}
    if int(state.get("attempts") or 0) >= MAX_ATTEMPTS:
        return {"added": 0, "merged": 0, "conflicts": [],
                "skipped": 0, "denied": 0, "blocked": True}
    bundle = memory.get_session_bundle(session_id)
    if bundle is None:
        raise memory_v3.V3NotFound("Session not found")
    watermark = int(state.get("last_message_id") or 0)
    new_msgs = [m for m in bundle["messages"] if type(m.get("id")) is int
                and m["id"] > watermark]
    user_msgs = [m for m in new_msgs if m.get("role") == "user"]
    latest_id = max([m["id"] for m in new_msgs], default=watermark)
    if not user_msgs:
        if latest_id > watermark:
            memory_v3.upsert_watermark(owner_id, session_id, latest_id)
        return {"added": 0, "merged": 0, "conflicts": [],
                "skipped": 0, "denied": 0}

    existing = memory_v3.facts_for_generation(owner_id)
    confirmed = [f for f in existing if f.get("status") == "confirmed"]
    confirmed_slots = [f["slot_path"] for f in confirmed]
    user_by_id = _user_index(user_msgs)
    whitelist = slot_whitelist()

    try:
        data = llm.chat_json(
            [{"role": "system", "content": _prompt_text(user_msgs, confirmed_slots)}],
            model=QWEN_ROUTER, temperature=0.1)
    except Exception:
        memory_v3.mark_extraction_error(owner_id, session_id, "MODEL_FAILED")
        raise ExtractionFailed("extraction model failed") from None
    data = data if isinstance(data, dict) else {}

    result = {"added": 0, "merged": 0, "conflicts": [], "skipped": 0,
              "denied": 0}
    conflicted_slots = set()
    confirmed_by_slot = {(f["kind"], f["slot_path"]): f for f in confirmed}
    candidates = data.get("facts") if isinstance(data.get("facts"), list) else []
    for item in candidates[:MAX_FACTS_PER_ROUND]:
        f = _valid_candidate(item, set(user_by_id), user_by_id, whitelist)
        if f is None:
            result["skipped"] += 1
            continue
        status = ("confirmed" if f["kind"] in AUTO_CONFIRM_KINDS else "proposed")
        old = confirmed_by_slot.get((f["kind"], f["slot_path"]))
        if old is not None and _norm(json.dumps(old.get("value"), ensure_ascii=False)) \
                != _norm(json.dumps(f["value"], ensure_ascii=False)):
            # Never overwrite a confirmed fact: new value goes in as proposed
            # and the conflict is surfaced for a human decision.
            status = "proposed"
            hint = json.dumps(old.get("value"), ensure_ascii=False)[:30]
            conflict = {"slot_path": f["slot_path"], "existing_hint": hint,
                        "new_value": f["value"]}
            result["conflicts"].append(conflict)
            conflicted_slots.add(f["slot_path"])
        vhash = memory_v3.fact_value_hash(f["kind"], f["slot_path"], f["value"])
        same = memory_v3.get_fact_by_hash(owner_id, vhash)
        if same is not None and same.get("status") in ("rejected", "superseded"):
            result["skipped"] += 1      # a rejected/superseded twin must not revive
            continue
        evidence = [{"session_id": session_id, "msg_id": f["msg_id"],
                     "quote": f["quote"]}]
        row, created = memory_v3.insert_fact(
            owner_id, f["kind"], f["slot_path"], f["value"], status=status,
            confidence=f["confidence"], source_session_id=session_id,
            source_message_id=f["msg_id"], evidence=evidence)
        if created:
            result["added"] += 1
        elif row is not None and same is not None \
                and len(row.get("evidence") or []) > len(same.get("evidence") or []):
            result["merged"] += 1       # new source appended to the live twin
        else:
            result["skipped"] += 1      # identical fact, nothing new to record

    # Model-reported conflicts that validation itself did not already surface.
    for c in (data.get("conflicts") if isinstance(data.get("conflicts"), list) else []):
        if (isinstance(c, dict) and c.get("slot_path") not in conflicted_slots
                and isinstance(c.get("slot_path"), str)):
            result["conflicts"].append({
                "slot_path": c["slot_path"],
                "existing_hint": str(c.get("existing_hint") or "")[:30],
                "new_value": c.get("new_value")})
            conflicted_slots.add(c["slot_path"])
    denied = data.get("denied") if isinstance(data.get("denied"), list) else []
    result["denied"] = sum(1 for d in denied if isinstance(d, dict))
    memory_v3.upsert_watermark(owner_id, session_id, latest_id)
    return result
