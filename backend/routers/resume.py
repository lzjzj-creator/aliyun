"""B 域：简历接口 v2（template / autofill / optimize）。

文件分片：本域路由单独文件，main.py 只 include（§12.1）。
契约唯一来源 = resume_template.json；版本不匹配一律 409 让前端重取。
"""
import json
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

try:
    from ..llm import llm, QWEN_CHAT
    from ..resume_pdf import SnapshotPdfReq, pdf_response
    from ..resume_template import (
        load, check_version, TemplateError, get_section,
        validate_fields, empty_fields, iter_field_paths, valid_path_syntax,
    )
    from .. import session_security as security
except ImportError:  # FC 以 backend 目录为根启动
    from llm import llm, QWEN_CHAT
    from resume_pdf import SnapshotPdfReq, pdf_response
    from resume_template import (
        load, check_version, TemplateError, get_section,
        validate_fields, empty_fields, iter_field_paths, valid_path_syntax,
    )
    import session_security as security

router = APIRouter(prefix="/api/v2/resume", tags=["resume-v2"])

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def _tpl():
    try:
        return load()
    except TemplateError:
        # 契约文件损坏属于部署事故：内部报错原文（含 JSON 位置/字段名）不下发给浏览器
        raise HTTPException(status_code=500, detail={
            "code": "TEMPLATE_CONTRACT_UNAVAILABLE",
            "message": "模板契约暂不可用，请联系维护者（服务端日志见 ingest 校验输出）"})


def _require_version(tpl, version: str):
    if not check_version(tpl, version):
        raise HTTPException(
            status_code=409,
            detail={"code": "TEMPLATE_VERSION_MISMATCH",
                    "server_version": tpl.template_version,
                    "message": "模板版本不匹配，请重新拉取 /api/v2/resume/template"})


@router.get("/template")
def get_template():
    """契约唯一出口：前后端读同一份，禁止双份硬编码。"""
    return _tpl().model_dump()


@router.post("/pdf")
def export_snapshot(request: Request, req: SnapshotPdfReq,
                    authorization: str | None = Header(default=None),
                    x_anon_id: str | None = Header(default=None),
                    x_session_token: str | None = Header(default=None)):
    security.enforce_ip(request)
    user, anon = security.require_identity(request, authorization, x_anon_id)
    if req.session_id:
        security.require_access(request, req.session_id, authorization,
                                anon if user is None else None, x_session_token)
    tpl = _tpl()
    _require_version(tpl, req.template_version)
    return pdf_response(tpl, req.fields, req.layout)


# ---------------- autofill：会话 → 字段 + evidence_map + gaps ----------------

class AutofillReq(BaseModel):
    session_id: Optional[str] = None       # A 域 #2/#3 就绪后走会话读取
    session_ids: Optional[list[str]] = None  # v5：多会话复选合并提取
    include_facts: bool = False              # v5：并入已确认随手记/事实
    template_version: str = ""
    messages: Optional[list[dict]] = None  # 降级路径：前端直传消息 [{role,content,id?}]
    profile: Optional[dict] = None


def _session_messages(sid: str):
    """Load one session's messages, ids namespaced s{idx}:{id} happens in caller."""
    try:
        from .. import memory
    except ImportError:
        try:
            import memory      # FC 以 backend 为根启动的裸模块形态
        except ImportError:
            memory = None
    if memory is None:
        raise HTTPException(status_code=501, detail={
            "code": "SESSION_STORE_NOT_READY",
            "message": "会话存储未就绪；请先以 messages 直传调用"})
    try:
        bundle = memory.get_session_bundle(sid)
    except memory.MemoryStoreError:
        raise HTTPException(status_code=503, detail="存储暂时不可用") from None
    if bundle is None or bundle.get("session") is None:
        raise security.not_found()
    return bundle["messages"], (bundle.get("profile") or {}).get("profile") or {}


def _namespaced(messages: list[dict], tag: str) -> list[dict]:
    """Prefix message ids with a session tag so cross-session evidence stays
    unambiguous and traceable (msg_id -> s{i}:{id})."""
    out = []
    for m in messages:
        m2 = dict(m)
        if "id" in m2 and m2["id"] is not None:
            m2["id"] = f"{tag}:{m2['id']}"
        out.append(m2)
    return out


def _facts_as_messages(facts: list[dict]) -> list[dict]:
    """Confirmed facts (含随手记 source_type=manual) as synthetic user messages
    with id fact:{id}; evidence quotes become citable content."""
    out = []
    for f in facts:
        quote = ""
        for ev in f.get("evidence") or []:
            if isinstance(ev, dict) and isinstance(ev.get("quote"), str) \
                    and ev["quote"].strip():
                quote = ev["quote"].strip()
                break
        value = f.get("value")
        text = quote or (value if isinstance(value, str) else
                         json.dumps(value, ensure_ascii=False)[:200])
        if text:
            out.append({"id": f"fact:{f['id']}", "role": "user",
                        "content": text})
    return out


def _resolve_conversation(req: AutofillReq, anon_id: str, token: str,
                          request: Request | None = None,
                          authorization: str | None = None):
    """返回 (messages, profile)。优先 messages 直传；session_id 路径依赖 A 域 memory.py。
    v5：session_ids[] 多会话合并（逐会话归属校验），include_facts 并入
    confirmed facts（含随手记 manual），Bearer 用户另并 init_profile 预填。"""
    if req.messages:
        return req.messages, req.profile or {}
    if req.session_ids:
        try:
            from .. import memory  # noqa: F401  (probe only)
        except ImportError:
            try:
                import memory  # noqa: F401
            except ImportError:
                memory = None
        if memory is None:
            raise HTTPException(status_code=501, detail={
                "code": "SESSION_STORE_NOT_READY",
                "message": "会话存储未就绪；请先以 messages 直传调用"})
        merged, profile = [], {}
        for i, raw_sid in enumerate(req.session_ids[:6]):
            sid, _ = security.require_access(
                request, raw_sid, authorization, anon_id, token)
            msgs, prof = _session_messages(sid)
            merged.extend(_namespaced(msgs, f"s{i}"))
            profile = prof or profile
        if req.include_facts:
            merged.extend(_owner_facts_messages(request, authorization))
        return merged, req.profile or profile
    if req.session_id:
        try:
            from .. import memory
        except ImportError:
            try:
                import memory      # FC 以 backend 为根启动的裸模块形态
            except ImportError:
                memory = None
        if memory is None:
            raise HTTPException(status_code=501, detail={
                "code": "SESSION_STORE_NOT_READY",
                "message": "会话存储未就绪；请先以 messages 直传调用"})
        sid, _ = security.require_access(
            request, req.session_id, authorization, anon_id, token)
        msgs, prof = _session_messages(sid)
        if req.include_facts:
            msgs = msgs + _owner_facts_messages(request, authorization)
        return msgs, req.profile or prof
    if req.include_facts:
        return _owner_facts_messages(request, authorization), req.profile or {}
    raise HTTPException(status_code=400, detail={
        "code": "CONVERSATION_REQUIRED",
        "message": "需要 session_id / session_ids / include_facts 之一"})


def _owner_facts_messages(request: Request | None,
                          authorization: str | None) -> list[dict]:
    """Bearer 用户 → 其 v3_owner 的 confirmed facts 转伪用户消息；无 Bearer 返回 []。
    模块不可用 → []；存储故障按全站口径上抛 503，不静默丢素材。"""
    if request is None:
        return []
    try:
        from .. import memory_v3
    except ImportError:
        try:
            import memory_v3
        except ImportError:
            return []
    try:
        user = security.current_user(authorization)
        if user is None:
            return []
        owner = memory_v3.get_or_create_owner_by_user(user["id"])
        facts = [f for f in memory_v3.facts_for_generation(owner["id"])
                 if f.get("status") == "confirmed"]
    except HTTPException:
        raise                      # current_user 已包装过的 503/404 直接上抛
    except memory_v3.MemoryStoreError:
        raise HTTPException(503, "存储暂时不可用") from None
    return _facts_as_messages(facts)


def _contract_brief(tpl) -> str:
    """给模型的契约摘要：各区块字段与必填标记（压缩 token）。"""
    lines = []
    for s in tpl.sections:
        fs = s.spec_fields()
        fl = "、".join(f"{f.key}{'*' if f.required else ''}" for f in fs)
        lines.append(f"{s.key}({s.type})[{s.title}]: {fl}")
    return "\n".join(lines)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _message_id(message: dict, index: int):
    # Only absent ids fall back to the displayed index; bool is not an integer id.
    mid = message.get("id", index)
    return mid if type(mid) in (int, str) and mid != "" else None


def _conversation_text(messages: list[dict]) -> str:
    """Use JSON ids in the prompt so string and integer ids remain distinct."""
    out = []
    for i, m in enumerate(messages):
        mid = _message_id(m, i)
        role = "用户" if m.get("role") == "user" else "知脉"
        content = m.get("content", "")
        if isinstance(content, str):
            out.append(f"[msg_id={json.dumps(mid, ensure_ascii=False)}] {role}：{content}")
    return "\n".join(out)


def _user_quote_ok(quote, messages: list[dict], msg_id=None) -> bool:
    if not isinstance(quote, str) or not quote.strip() or type(msg_id) not in (int, str):
        return False
    # Duplicate ids are ambiguous, even if one duplicate is an assistant message.
    matched = [m for i, m in enumerate(messages)
               if type(_message_id(m, i)) is type(msg_id) and _message_id(m, i) == msg_id]
    return (len(matched) == 1 and matched[0].get("role") == "user"
            and isinstance(matched[0].get("content"), str)
            and quote in matched[0]["content"])


def _get_leaf(fields: dict, path: str):
    """按字段路径取值：projects.0.bullets.1 → fields[projects][0][bullets][1]"""
    cur = fields
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _set_leaf(fields: dict, path: str, value):
    parts = path.split(".")
    cur = fields
    for part in parts[:-1]:
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def _blank_like(v):
    return [] if isinstance(v, list) else ""


def _iter_leaves(tpl, fields: dict):
    """枚举全部叶子：(path, value, required, label)。非空叶子都要证据兜底。"""
    for sec in tpl.sections:
        data = fields.get(sec.key)
        if sec.type == "kv":
            for f in sec.fields:
                yield f"{sec.key}.{f.key}", (data or {}).get(f.key), f.required, f.label
        elif sec.type == "entries":
            for i, e in enumerate(data or []):
                for f in sec.entry_fields:
                    v = (e or {}).get(f.key)
                    base = f"{sec.key}.{i}.{f.key}"
                    if f.input in ("bullets", "tags") and v:
                        for j, item in enumerate(v):
                            yield f"{base}.{j}", item, f.required, f"{f.label} {j + 1}"
                    else:
                        yield base, v, f.required, f.label
        elif sec.type == "skillgroups":
            req_flag = bool(sec.spec_fields() and sec.spec_fields()[0].required)
            for gi, g in enumerate((data or {}).get("groups", [])):
                yield f"{sec.key}.groups.{gi}.name", g.get("name"), False, "技能分组"
                for ii, it in enumerate((g or {}).get("items", [])):
                    yield f"{sec.key}.groups.{gi}.items.{ii}", it, req_flag, f"{g.get('name','技能')}项"
        elif sec.type == "lines":
            for i, it in enumerate((data or {}).get("items", [])):
                yield f"{sec.key}.items.{i}.text", (it or {}).get("text"), False, sec.title
                yield f"{sec.key}.items.{i}.period", (it or {}).get("period"), False, "获奖时间"
        elif sec.type == "text":
            yield f"{sec.key}.text", (data or {}).get("text"), False, sec.title


def _normalize_fields(tpl, raw: dict) -> dict:
    """把模型输出收敛到契约形状：未知键丢弃，类型偏差置空。"""
    out = empty_fields(tpl)
    raw = raw if isinstance(raw, dict) else {}
    for sec in tpl.sections:
        data = raw.get(sec.key)
        if data is None:
            continue
        if sec.type == "kv" and isinstance(data, dict):
            for f in sec.fields:
                v = data.get(f.key)
                out[sec.key][f.key] = v if isinstance(v, str) else ""
        elif sec.type == "entries" and isinstance(data, list):
            entries = []
            for e in data[: (sec.max or 5)]:
                # Keep source indexes, including malformed/empty rows. Evidence paths
                # must never silently move to a different model row.
                e = e if isinstance(e, dict) else {}
                norm = {}
                for f in sec.entry_fields:
                    v = e.get(f.key)
                    if f.input in ("bullets", "tags"):
                        norm[f.key] = [x if isinstance(x, str) else "" for x in v] if isinstance(v, list) else []
                    else:
                        norm[f.key] = v if isinstance(v, str) else ""
                entries.append(norm)
            out[sec.key] = entries
        elif sec.type == "skillgroups" and isinstance(data, dict):
            groups = []
            raw_groups = data.get("groups")
            for g in (raw_groups if isinstance(raw_groups, list) else [])[:4]:
                g = g if isinstance(g, dict) else {}
                items = g.get("items")
                groups.append({"name": g.get("name") if isinstance(g.get("name"), str) else "",
                               "items": [x if isinstance(x, str) else "" for x in items] if isinstance(items, list) else []})
            out[sec.key] = {"groups": groups}
        elif sec.type == "lines" and isinstance(data, dict):
            items = []
            raw_items = data.get("items")
            for it in (raw_items if isinstance(raw_items, list) else [])[:10]:
                if isinstance(it, str):
                    it = {"text": it}
                it = it if isinstance(it, dict) else {}
                items.append({k: it.get(k) if isinstance(it.get(k), str) else ""
                              for k in ("text", "period")})
            out[sec.key] = {"items": items}
        elif sec.type == "text" and isinstance(data, dict):
            out[sec.key] = {"text": data.get("text") if isinstance(data.get("text"), str) else ""}
    return out


def _init_prefill(tpl, fields: dict, auth_user: dict | None,
                  evidence_map: dict) -> dict:
    """v5: merge init_profile basics/education into empty field slots; mark
    provenance with msg_id='init_profile' (validated below without a message)."""
    init = (auth_user or {}).get("init_profile") or {}
    if not isinstance(init, dict) or not init:
        return fields
    basics = init.get("basics") or {}
    target = fields.setdefault("basics", {})
    for key in ("name", "phone", "email", "target_role", "city", "github", "blog"):
        v = basics.get(key)
        if isinstance(v, str) and v.strip() and not str(target.get(key) or "").strip():
            target[key] = v.strip()
            evidence_map[f"basics.{key}"] = [{"msg_id": "init_profile",
                                              "quote": v.strip()}]
    edu_init = init.get("education")
    if isinstance(edu_init, list) and edu_init and isinstance(edu_init[0], dict):
        edu = fields.setdefault("education", [])
        src = edu_init[0]
        if not edu:
            edu.append({f.key: ([] if f.input in ("bullets", "tags") else "")
                        for f in (get_section(tpl, "education").entry_fields
                                  if get_section(tpl, "education") else [])})
        for key in ("school", "major", "degree", "period", "gpa_or_rank"):
            v = src.get(key)
            if isinstance(v, str) and v.strip() and edu \
                    and not str(edu[0].get(key) or "").strip():
                edu[0][key] = v.strip()
                evidence_map[f"education.0.{key}"] = [
                    {"msg_id": "init_profile", "quote": v.strip()}]
    # 初始化照片同步进简历（data-url 字符串，与 render.isValidResumePhoto 对齐）
    photo = init.get("photo")
    url = photo.get("data_url") if isinstance(photo, dict) \
        else photo if isinstance(photo, str) else None
    meta = fields.setdefault("_meta", {})
    if (isinstance(url, str) and url.startswith("data:image/jpeg;base64,")
            and not meta.get("photo")):
        meta["photo"] = url
    return fields


@router.post("/autofill")
def autofill(req: AutofillReq, request: Request,
             authorization: str | None = Header(default=None),
             x_anon_id: str = Header(default=""),
             x_session_token: str = Header(default="")):
    # §5/§10：IP+anon 校验+配额（messages 直传路径同样要求 X-Anon-Id）
    security.enforce_ip(request)
    auth_user, anon = security.require_identity(request, authorization, x_anon_id)
    security.charge_turn(anon)
    tpl = _tpl()
    _require_version(tpl, req.template_version)
    messages, profile = _resolve_conversation(
        req, anon, x_session_token, request, authorization)
    if not any(m.get("role") == "user" and m.get("content") for m in messages):
        return {"template_version": tpl.template_version,
                "fields": empty_fields(tpl), "evidence_map": {},
                "gaps": [{"field_path": k, "label": k, "reason": "对话为空，请手动填写"}
                         for k in ("basics", "education", "projects", "skills")]}

    profile_txt = (f"\n已确认画像（可作为事实依据）："
                   f"{json.dumps(profile, ensure_ascii=False)[:800]}\n") if profile else ""
    prompt = _prompt("autofill.md") + (
        f"\n契约结构（*为必填）：\n{_contract_brief(tpl)}\n{profile_txt}\n"
        f"会话：\n{_conversation_text(messages)}\n")
    try:
        data = llm.chat_json([{"role": "system", "content": prompt}],
                             model=QWEN_CHAT, temperature=0.2)
    except Exception:
        raise HTTPException(status_code=502, detail={
            "code": "AUTOFILL_MODEL_FAILED", "message": "填写生成服务暂时不可用"})

    data = data if isinstance(data, dict) else {}
    fields = _normalize_fields(tpl, data.get("fields"))
    evidence_map = {}
    fields = _init_prefill(tpl, fields, auth_user, evidence_map)
    leaves = list(_iter_leaves(tpl, fields))
    leaf_values = {path: value for path, value, _, _ in leaves}
    raw_gaps = data.get("gaps")
    gaps = [{"field_path": g["field_path"],
             "label": g.get("label") if isinstance(g.get("label"), str) else g["field_path"],
             "reason": g.get("reason") if isinstance(g.get("reason"), str) else "请确认或手动填写"}
            for g in (raw_gaps if isinstance(raw_gaps, list) else [])
            if isinstance(g, dict) and isinstance(g.get("field_path"), str)
            and (g["field_path"] in leaf_values or g["field_path"] in fields)]
    gap_paths = {g["field_path"] for g in gaps}

    # Exact concrete leaf only. A genuine but unrelated quote cannot support a
    # value: retain only extractive strings occurring verbatim inside that quote.
    # init_profile-sourced values validate against the init record itself, not
    # a chat message (msg_id='init_profile' is the provenance marker).
    raw_evidence = data.get("evidence_map")
    for path, evs in (raw_evidence if isinstance(raw_evidence, dict) else {}).items():
        value = leaf_values.get(path)
        if not isinstance(value, str) or not value.strip() or not isinstance(evs, list):
            continue
        ok = [{"msg_id": e["msg_id"], "quote": e["quote"]}
              for e in evs if isinstance(e, dict)
              and _user_quote_ok(e.get("quote"), messages, e.get("msg_id"))
              and value in e["quote"]]
        if ok:
            evidence_map.setdefault(path, []).extend(ok)

    for path, v, required, label in leaves:
        filled = isinstance(v, str) and bool(v.strip())
        if filled and path not in evidence_map:
            _set_leaf(fields, path, "")
            if path not in gap_paths:
                gaps.append({"field_path": path, "label": label,
                             "reason": "无逐字支持该字段的用户消息证据，请确认或手动填写"})
                gap_paths.add(path)
        elif not filled and required and path not in gap_paths:
            gaps.append({"field_path": path, "label": label,
                         "reason": "对话中未提及，请手动填写"})
            gap_paths.add(path)

    # 模型若从事实库补出了基础信息却没有逐字消息证据，上面的校验会清空它；
    # 账号初始化画像是权威来源，最后回填一次并撤掉对应缺口。
    fields = _init_prefill(tpl, fields, auth_user, evidence_map)
    init_paths = {p for p in evidence_map
                  if p.startswith(("basics.", "education.0."))}
    if init_paths:
        gaps = [g for g in gaps if g["field_path"] not in init_paths]

    # Preserve indexes, including empty entries/list slots. Removing them would
    # silently retarget evidence and gaps. Clients must accept sparse blank rows;
    # any later compaction must remap fields, evidence_map and gaps together.

    # 区块级缺口：必填 entries 区块为空 → section 级 gap
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

    return {"template_version": tpl.template_version,
            "fields": fields, "evidence_map": evidence_map, "gaps": gaps}


# ---------------- expand：STAR 扩写为一段话（v5 §9.6 R4） ----------------

class ExpandReq(BaseModel):
    template_version: str = ""
    fields: dict = {}
    confirmed_facts: str = ""
    jd: str | None = None


def _entry_source(sec, entry: dict) -> str:
    """该 entry 的全部非空素材（事实来源语料：名字/角色/时间/技术/bullets）。"""
    parts = []
    for f in sec.entry_fields:
        v = entry.get(f.key)
        if f.input in ("bullets", "tags"):
            parts += [x for x in (v or []) if isinstance(x, str) and x.strip()]
        elif isinstance(v, str) and v.strip():
            parts.append(v.strip())
    return "\n".join(parts)


@router.post("/expand")
def expand_entries(req: ExpandReq, request: Request,
                   authorization: str | None = Header(default=None),
                   x_anon_id: str = Header(default=None)):
    """把 entries 区块每条经历按 STAR 扩写为「一段话」（单条 bullet 承载）。
    数字守卫：paragraph 里出现的数字必须全部来自该 entry 的素材语料，
    否则整条回退为原始 bullets（宁可不扩写，绝不编造）。"""
    security.enforce_ip(request)
    _, anon = security.require_identity(request, authorization, x_anon_id)
    security.charge_turn(anon)
    try:
        from .. import kb as _kb
    except ImportError:
        import kb as _kb
    import os as _os
    _min_score = float(_os.getenv("MIN_RETRIEVE_SCORE", "0.25"))
    tpl = _tpl()
    _require_version(tpl, req.template_version)
    fields = json.loads(json.dumps(req.fields or {}, ensure_ascii=False))
    prompt = _prompt("v2_expand.md")
    if req.confirmed_facts:
        prompt += f"\n用户已确认事实（可作结果佐证）：{req.confirmed_facts[:1500]}\n"
    if req.jd:
        prompt += f"\n目标岗位 JD（突出相关素材，不新增事实）：{req.jd[:1500]}\n"

    expanded, kept, placeholders, expanded_secs = 0, 0, [], set()
    for sec in tpl.sections:
        if sec.type != "entries":
            continue
        entries = fields.get(sec.key)
        if not isinstance(entries, list):
            continue
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            source = _entry_source(sec, entry)
            if not source:
                continue
            # KB 检索：素材单薄时以命中内容为基底补写；检索失败静默降级。
            kb_hints = ""
            try:
                hits = _kb.search(llm.embed(source[:200]), source[:200], "all", 3)
                hits = [h for h in hits if h.get("score", 0) >= _min_score]
                if hits:
                    kb_hints = ("\n知识库参考（素材单薄时以其为基底按 STAR 补写）：\n"
                                + "\n".join(h.get("text", "")[:150]
                                            for h in hits[:3]))
            except Exception:
                kb_hints = ""
            try:
                data = llm.chat_json(
                    [{"role": "system", "content": prompt},
                     {"role": "user", "content":
                      f"经历类型：{sec.title}\n素材：\n{source[:3000]}{kb_hints}"}],
                    model=QWEN_CHAT, temperature=0.3)
            except Exception:
                kept += 1
                continue
            paragraph = (data or {}).get("paragraph")
            if not isinstance(paragraph, str) or not paragraph.strip():
                kept += 1
                continue
            # 用户拍板：素材不足也按知识库+STAR 直接扩写，不做数字/占位拦截；
            # 模型万一漏出占位标记则清掉兜底。
            paragraph = re.sub(r"【[^】]*待补充[^】]*】", "", paragraph).strip()
            if not paragraph:
                kept += 1
                continue
            entry["bullets"] = [paragraph]
            expanded += 1
            expanded_secs.add(sec.key)
    return {"template_version": tpl.template_version, "fields": fields,
            "expanded": expanded, "kept": kept,
            "sections": sorted(expanded_secs), "placeholders": placeholders}


# ---------------- optimize：分段黑箱优化（§9） ----------------

class OptimizePlanReq(BaseModel):
    template_version: str = ""
    fields: dict = {}


class OptimizeSectionReq(BaseModel):
    template_version: str = ""
    section: str = ""
    fields: dict = {}
    confirmed_facts: str = ""


def _sec_counts(sec, data) -> tuple[int, int]:
    """(条目数, bullet 总数)；text 型按 1/1 计。"""
    if sec.type == "entries":
        entries = data or []
        return len(entries), sum(len(e.get("bullets") or []) for e in entries if isinstance(e, dict))
    if sec.type == "text":
        return (1, 1) if str((data or {}).get("text", "")).strip() else (0, 0)
    if sec.type == "kv":
        return 0, 0
    return 0, 0


@router.post("/optimize/plan")
def optimize_plan(req: OptimizePlanReq, request: Request,
                  authorization: str | None = Header(default=None),
                  x_anon_id: str = Header(default=None)):
    security.enforce_ip(request)
    _, anon = security.require_identity(request, authorization, x_anon_id)
    security.charge_turn(anon)
    tpl = _tpl()
    _require_version(tpl, req.template_version)
    errs = validate_fields(tpl, req.fields)
    if errs:
        raise HTTPException(status_code=400, detail={"code": "FIELDS_INVALID", "message": "; ".join(errs)})

    rewrite_keys = set((tpl.optimize or {}).get("rewrite_sections", []))
    out = []
    for sec in sorted(tpl.sections, key=lambda s: (s.priority or 99, s.order)):
        data = req.fields.get(sec.key)
        ec, bc = _sec_counts(sec, data)
        optimizable = sec.key in rewrite_keys
        if not optimizable:
            status, reason = ("check", "与目标岗位相关性人工确认即可") if sec.key == "skills" else ("ok", "静态字段，无需改写")
        elif ec == 0:
            status, reason = "skipped", "本节为空，跳过"
        else:
            status, reason = "pending", ""
        out.append({"key": sec.key, "title": sec.title,
                    "priority": sec.priority or 99, "optimizable": optimizable,
                    "status": status, "reason": reason,
                    "entry_count": ec, "bullet_count": bc})
    return {"template_version": tpl.template_version, "sections": out}


def _facts_for_section(sec, data, confirmed: str) -> str:
    """守卫语料 = 确认事实 + 本区块非 bullet 字段（公司/角色/技术栈名都可被引用）。"""
    parts = [confirmed] if confirmed else []
    if sec.type == "entries":
        for e in data or []:
            for f in sec.entry_fields:
                if f.input == "bullets":
                    continue
                v = e.get(f.key)
                parts.append("、".join(v) if isinstance(v, list) else str(v or ""))
    elif sec.type == "text":
        pass
    return "\n".join(p for p in parts if p)


@router.post("/optimize/section")
def optimize_section(req: OptimizeSectionReq, request: Request,
                     authorization: str | None = Header(default=None),
                     x_anon_id: str = Header(default=None)):
    security.enforce_ip(request)
    _, anon = security.require_identity(request, authorization, x_anon_id)
    security.charge_turn(anon)
    try:
        from ..resume_optimizer import rewrite_bullets, extract_placeholder
    except ImportError:
        from resume_optimizer import rewrite_bullets, extract_placeholder

    tpl = _tpl()
    _require_version(tpl, req.template_version)
    sec = get_section(tpl, req.section)
    if not sec:
        raise HTTPException(status_code=400, detail={"code": "SECTION_UNKNOWN", "message": f"未知区块 {req.section}"})
    if sec.key not in set((tpl.optimize or {}).get("rewrite_sections", [])):
        raise HTTPException(status_code=400, detail={"code": "SECTION_NOT_OPTIMIZABLE", "message": f"{sec.title} 不在可改写区块内"})

    data = req.fields.get(sec.key)
    steps = []

    # metas: [(field_path, original_text)] —— text 型当一条 bullet 处理
    if sec.type == "entries":
        metas = [(f"{sec.key}.{i}.bullets.{j}", b)
                 for i, e in enumerate(data or [])
                 for j, b in enumerate(e.get("bullets") or []) if str(b).strip()]
    elif sec.type == "text":
        metas = [(f"{sec.key}.text", (data or {}).get("text", ""))]
        metas = [(p, t) for p, t in metas if str(t).strip()]
    else:
        metas = []

    ec, bc = _sec_counts(sec, data)
    no_num = sum(1 for _, t in metas if not re.search(r"\d", t))
    too_long = sum(1 for _, t in metas if len(t) > 60)
    issues = ([f"{no_num} 条缺量化结果"] if no_num else []) + \
             ([f"{too_long} 条过长(>60字)"] if too_long else [])
    steps.append({"kind": "diagnose", "title": "字段诊断",
                  "detail": f"{ec} 段 · {bc} 条 bullet · {'；'.join(issues) or '未发现明显问题'}",
                  "status": "done"})

    base = {"template_version": tpl.template_version, "section": sec.key,
            "steps": steps, "optimized": data, "change_log": [],
            "placeholders": [], "pending_questions": [], "needs_input": False}
    if not metas:
        base["status"] = "skipped"
        return base

    originals = [t for _, t in metas]
    facts = _facts_for_section(sec, data, req.confirmed_facts)
    try:
        rubric = "\n".join(
            f.rubric or f.rules or "" for f in sec.spec_fields()
            if f.key in ("bullets", "text"))
        result = rewrite_bullets(originals, facts, rubric=rubric)
    except Exception:
        steps.append({"kind": "rewrite", "title": "STAR 改写",
                      "detail": "改写失败，请稍后重试", "status": "error"})
        base["status"] = "error"
        return base
    steps.append({"kind": "rewrite", "title": "STAR 改写",
                  "detail": f"改写 {len(metas)} 条（不动事实）", "status": "done"})

    rewritten = result.get("rewritten") or []
    blocked = 0
    optimized = json.loads(json.dumps(data, ensure_ascii=False)) if data is not None else data
    change_log, placeholders = [], []

    for idx, (path, original) in enumerate(metas):
        item = rewritten[idx] if idx < len(rewritten) else None
        suggested = (item or {}).get("suggested") or original
        write = suggested
        reason = "STAR 改写：动词开头+量化结果"
        if item and item.get("needs_user_data"):
            blocked += 1
            # A placeholder elsewhere does not sanitize unsupported claims.
            # Conservatively retain the user's original until facts are supplied.
            write = f"{original}【待补充】"
            reason = "建议含未确认事实，保留原文并待补充"
            placeholders.append({"field_path": path, "label": "待补充",
                                 "placeholder": "【待补充】"})
        if write != original:
            change_log.append({"field_path": path, "before": original,
                               "after": write, "reason": reason})
        # 写回 optimized
        parts = path.split(".")
        if sec.type == "text":
            optimized["text"] = write
        else:
            optimized[int(parts[1])]["bullets"][int(parts[3])] = write

    steps.append({"kind": "guard", "title": "数字与事实守卫",
                  "detail": f"{blocked} 处疑似编造已拦截→占位" if blocked else "全部通过",
                  "status": "done"})
    base.update({"status": "done", "optimized": optimized, "change_log": change_log,
                 "placeholders": placeholders,
                 "pending_questions": result.get("questions") or [],
                 "needs_input": bool(placeholders or result.get("questions"))})
    return base
