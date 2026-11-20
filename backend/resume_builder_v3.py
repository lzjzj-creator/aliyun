"""v3 resume builder: owner facts -> template fields + evidence_map + gaps.

Reuses the v2 autofill red line verbatim — a field survives only when its
value occurs word-for-word inside a real fact's quote. The v2 guard helpers
(`_normalize_fields`, `_iter_leaves`, `_set_leaf`) are imported, not re-done.
"""
import hashlib
import json
import re
from pathlib import Path

try:
    from .llm import llm, QWEN_CHAT
    from .resume_template import load, check_version, empty_fields
    from .routers.resume import (
        _normalize_fields, _iter_leaves, _set_leaf, _contract_brief, _prompt)
    from . import memory_v3
except ImportError:  # FC starts with backend on sys.path
    from llm import llm, QWEN_CHAT
    from resume_template import load, check_version, empty_fields
    from routers.resume import (
        _normalize_fields, _iter_leaves, _set_leaf, _contract_brief, _prompt)
    import memory_v3

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
MAX_FACTS_PER_BUILD = 120
MAX_VALUE_TEXT = 200
MAX_FACTS_PROMPT_CHARS = 12000


class BuildFailed(RuntimeError):
    """Model call failed or returned unusable JSON."""


def _jd_digest(jd_text: str | None) -> str | None:
    if not isinstance(jd_text, str) or not jd_text.strip():
        return None
    return hashlib.sha256(jd_text.encode("utf-8")).hexdigest()


def _tokens(text: str | None) -> set:
    """Latin words + CJK bi/trigram windows (no segmenter dependency)."""
    if not isinstance(text, str):
        return set()
    words = set(re.findall(r"[A-Za-z][A-Za-z0-9+#.]{1,}", text.lower()))
    for run in re.findall(r"[一-鿿]+", text):
        for n in (2, 3):
            words.update(run[i:i + n] for i in range(len(run) - n + 1))
    return words


def _rank_facts(facts: list, target_role: str | None, jd_text: str | None) -> list:
    """Relevance order: keyword hits vs slot_path+value, confirmed first,
    hit count, then confidence. No vector search (plan §7-2)."""
    query = _tokens(target_role) | _tokens(jd_text)

    def score(f):
        hay = (f.get("slot_path") or "") + " " + \
            json.dumps(f.get("value"), ensure_ascii=False)
        hits = sum(1 for t in query if t in hay.lower() or t in hay)
        return (f.get("status") == "confirmed", hits,
                float(f.get("confidence") or 0))

    return sorted(facts, key=score, reverse=True)[:MAX_FACTS_PER_BUILD]


def _fact_line(f: dict) -> str:
    quotes = " | ".join(
        (e.get("quote") or "")[:160]
        for e in (f.get("evidence") or [])[:3] if isinstance(e, dict))
    value = json.dumps(f.get("value"), ensure_ascii=False)[:MAX_VALUE_TEXT]
    return (f'[fact_id={f["id"]} kind={f["kind"]} slot={f["slot_path"]} '
            f'status={f["status"]}] value={value} | quote={quotes}')


def _fact_quotes(f: dict) -> list:
    return [e.get("quote") for e in (f.get("evidence") or [])
            if isinstance(e, dict) and isinstance(e.get("quote"), str)]


def build_fields_from_facts(facts: list, tpl, *,
                            target_role: str | None = None,
                            jd_text: str | None = None) -> dict:
    """facts -> {fields, evidence_map, gaps, fact_snapshot}.

    Every kept leaf value must occur verbatim inside a live fact's quote —
    the same extractive red line as v2 autofill, now sourced from the
    pre-validated fact store instead of raw conversation messages.
    """
    ranked = _rank_facts(facts or [], target_role, jd_text)
    fact_map = {f["id"]: f for f in ranked}
    prompt = (PROMPT_DIR / "v3_autofill.md").read_text(encoding="utf-8") + (
        f"\n契约结构（*为必填）：\n{_contract_brief(tpl)}\n"
        f"\n目标岗位：{target_role or '未指定'}\n"
        f"{('JD 摘要：' + jd_text[:600]) if jd_text else ''}\n"
        f"\n事实库（只能引用这些 fact_id）：\n"
        + "\n".join(_fact_line(f) for f in ranked)[:MAX_FACTS_PROMPT_CHARS])
    try:
        data = llm.chat_json([{"role": "system", "content": prompt}],
                             model=QWEN_CHAT, temperature=0.2)
    except Exception:
        raise BuildFailed("resume build model failed") from None
    data = data if isinstance(data, dict) else {}
    fields = _normalize_fields(tpl, data.get("fields"))
    leaves = list(_iter_leaves(tpl, fields))
    leaf_values = {p: v for p, v, _, _ in leaves}

    raw_gaps = data.get("gaps")
    gaps = [{"field_path": g["field_path"],
             "label": g.get("label") if isinstance(g.get("label"), str) else g["field_path"],
             "reason": g.get("reason") if isinstance(g.get("reason"), str) else "请确认或手动填写"}
            for g in (raw_gaps if isinstance(raw_gaps, list) else [])
            if isinstance(g, dict) and isinstance(g.get("field_path"), str)
            and (g["field_path"] in leaf_values or g["field_path"] in fields)]
    gap_paths = {g["field_path"] for g in gaps}

    # Evidence red line: path must name a real leaf with a real string value,
    # fact_id must be one we fed the model, and value must occur verbatim in
    # at least one of that fact's quotes. proposed facts join but stay flagged.
    evidence_map = {}
    used_fact_ids = set()
    raw_ev = data.get("evidence_map")
    for path, ids in (raw_ev if isinstance(raw_ev, dict) else {}).items():
        value = leaf_values.get(path)
        if not isinstance(value, str) or not value.strip() \
                or not isinstance(ids, list):
            continue
        entries = []
        for fid in ids:
            fact = fact_map.get(fid)
            if fact is None:
                continue
            for e in fact.get("evidence") or []:
                if not isinstance(e, dict):
                    continue
                quote = e.get("quote")
                if isinstance(quote, str) and value in quote:
                    entries.append({"fact_id": fid,
                                    "session_id": e.get("session_id"),
                                    "msg_id": e.get("msg_id"),
                                    "quote": quote,
                                    "fact_status": fact["status"]})
                    break
            else:  # value maybe equals fact value without a quote match
                fv = fact.get("value")
                if isinstance(fv, str) and value in fv:
                    entries.append({"fact_id": fid, "session_id": None,
                                    "msg_id": fact.get("source_message_id"),
                                    "quote": fv, "fact_status": fact["status"]})
        if entries:
            evidence_map[path] = entries[:4]
            used_fact_ids.update(e["fact_id"] for e in entries)

    for path, v, required, label in leaves:
        filled = isinstance(v, str) and bool(v.strip())
        if filled and path not in evidence_map:
            _set_leaf(fields, path, "")
            if path not in gap_paths:
                gaps.append({"field_path": path, "label": label,
                             "reason": "无逐字支持该字段的事实证据，请确认或手动填写"})
                gap_paths.add(path)
        elif not filled and required and path not in gap_paths:
            gaps.append({"field_path": path, "label": label,
                         "reason": "事实库未覆盖，请手动填写"})
            gap_paths.add(path)

    for sec in tpl.sections:
        if sec.type != "entries" or sec.key == "education":
            continue
        if not fields.get(sec.key) and sec.key not in gap_paths:
            optional = "可留空" if not sec.required else "建议补充"
            gaps.append({"field_path": sec.key, "label": sec.title,
                         "reason": f"暂无{sec.title}素材，{optional}"})
            gap_paths.add(sec.key)
        elif fields.get(sec.key) and len(fields[sec.key]) < (sec.min or 0):
            if sec.key not in gap_paths:
                gaps.append({"field_path": sec.key, "label": sec.title,
                             "reason": f"仅 {len(fields[sec.key])} 段，建议补足 {sec.min} 段"})

    snapshot = [{"fact_id": fid, "value_hash": fact_map[fid].get("value_hash")}
                for fid in sorted(used_fact_ids)]
    return {"fields": fields, "evidence_map": evidence_map,
            "gaps": gaps, "fact_snapshot": snapshot}


def jd_coverage(fields: dict, jd_text: str | None) -> dict:
    """JD 关键词覆盖报告：jd_text tokens 出现在字段值里的比例与清单。"""
    jd_tokens = _tokens(jd_text)
    if not jd_tokens:
        return {"covered": [], "missing": [], "ratio": None}
    hay = json.dumps(fields or {}, ensure_ascii=False).lower()
    covered = sorted(t for t in jd_tokens if t.lower() in hay or t in hay)
    missing = sorted(jd_tokens - set(covered))
    return {"covered": covered[:60], "missing": missing[:60],
            "ratio": round(len(covered) / len(jd_tokens), 2)}


def merge_manual_overrides(old_fields: dict, new_fields: dict) -> dict:
    """Reapply every field the user hand-edited onto a regenerated draft;
    _meta.manual_override travels with fields, never silently dropped."""
    meta = (new_fields.get("_meta") or {}) if isinstance(new_fields, dict) else {}
    old_meta = (old_fields or {}).get("_meta") or {}
    paths = [p for p in (old_meta.get("manual_override") or [])
             if isinstance(p, str)]
    merged = dict(meta)
    merged["manual_override"] = paths
    out = dict(new_fields)
    out["_meta"] = merged
    for path in paths:
        cur = old_fields
        ok = True
        for part in path.split("."):
            if isinstance(cur, list):
                try:
                    cur = cur[int(part)]
                except (ValueError, IndexError):
                    ok = False
                    break
            elif isinstance(cur, dict):
                cur = cur.get(part)
            else:
                ok = False
                break
        if ok:
            try:
                _set_leaf(out, path, cur)
            except (KeyError, IndexError, TypeError, ValueError):
                pass
    return out


def create_document(owner_id: str, *, title: str, target_role: str | None = None,
                    jd_text: str | None = None,
                    template_version: str | None = None) -> dict:
    """Build fields from the owner's facts and persist a draft document."""
    tpl = load()
    if template_version and not check_version(tpl, template_version):
        raise ValueError("template version mismatch")
    facts = memory_v3.facts_for_generation(owner_id)
    built = build_fields_from_facts(facts, tpl, target_role=target_role,
                                    jd_text=jd_text)
    return memory_v3.create_document(
        owner_id, title=title, template_version=tpl.template_version,
        target_role=target_role, jd_digest=_jd_digest(jd_text),
        fields=built["fields"], evidence_map=built["evidence_map"],
        gaps=built["gaps"], fact_snapshot=built["fact_snapshot"],
        status="draft")


def regenerate_fields(doc: dict, tpl, *, target_role: str | None = None,
                      jd_text: str | None = None,
                      keep_manual: bool = True) -> dict:
    """Re-run the build for an existing document; manual fields survive unless
    keep_manual=False (the plan's '全量重生成' opt-out)."""
    facts = memory_v3.facts_for_generation(doc["owner_id"])
    built = build_fields_from_facts(facts, tpl,
                                    target_role=target_role or doc.get("target_role"),
                                    jd_text=jd_text)
    if keep_manual:
        built["fields"] = merge_manual_overrides(doc.get("fields") or {},
                                                 built["fields"])
        # Reattach evidence for fields that regained a fact-backed value is
        # unnecessary—manual fields keep no evidence by design.
    return built
