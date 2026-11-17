"""B 域：简历接口 v2（template / autofill / optimize）。

文件分片：本域路由单独文件，main.py 只 include（§12.1）。
契约唯一来源 = resume_template.json；版本不匹配一律 409 让前端重取。
"""
import json
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

try:
    from ..llm import llm, QWEN_CHAT
    from ..resume_template import (
        load, check_version, TemplateError, get_section,
        validate_fields, empty_fields, iter_field_paths, valid_path_syntax,
    )
except ImportError:  # FC 以 backend 目录为根启动
    from llm import llm, QWEN_CHAT
    from resume_template import (
        load, check_version, TemplateError, get_section,
        validate_fields, empty_fields, iter_field_paths, valid_path_syntax,
    )

router = APIRouter(prefix="/api/v2/resume", tags=["resume-v2"])

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def _tpl():
    try:
        return load()
    except TemplateError as e:
        raise HTTPException(status_code=500, detail=str(e))


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


# ---------------- autofill：会话 → 字段 + evidence_map + gaps ----------------

class AutofillReq(BaseModel):
    session_id: Optional[str] = None       # A 域 #2/#3 就绪后走会话读取
    template_version: str = ""
    messages: Optional[list[dict]] = None  # 降级路径：前端直传消息 [{role,content,id?}]
    profile: Optional[dict] = None


def _resolve_conversation(req: AutofillReq, anon_id: str, token: str):
    """返回 (messages, profile)。优先 messages 直传；session_id 路径依赖 A 域 memory.py。"""
    if req.messages:
        return req.messages, req.profile or {}
    if req.session_id:
        try:
            import memory  # A 域 #2 交付后生效（FC 冷启动后动态可用）
        except ImportError:
            memory = None
        if memory is None:
            raise HTTPException(status_code=501, detail={
                "code": "SESSION_STORE_NOT_READY",
                "message": "会话存储未就绪；请先以 messages 直传调用"})
        if not memory.verify_session(req.session_id, anon_id, token):
            raise HTTPException(status_code=404, detail="not found")
        bundle = memory.get_session_bundle(req.session_id)
        return bundle["messages"], (bundle.get("profile") or {}).get("profile") or {}
    raise HTTPException(status_code=400, detail={
        "code": "CONVERSATION_REQUIRED",
        "message": "需要 session_id 或 messages 之一"})


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


def _conversation_text(messages: list[dict]) -> str:
    """[m<id>] 角色：内容 —— quote 校验只允许 role=user 的消息。"""
    out = []
    for i, m in enumerate(messages):
        mid = m.get("id", i)
        role = "用户" if m.get("role") == "user" else "知脉"
        out.append(f"[m{mid}] {role}：{m.get('content', '')}")
    return "\n".join(out)


def _user_quote_ok(quote: str, messages: list[dict]) -> bool:
    q = _norm(quote)
    if not q:
        return False
    return any(q in _norm(m.get("content", ""))
               for m in messages if m.get("role") == "user")


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
                for ii, it in enumerate((g or {}).get("items", [])):
                    yield f"{sec.key}.groups.{gi}.items.{ii}", it, req_flag, f"{g.get('name','技能')}项"
        elif sec.type == "lines":
            for i, it in enumerate((data or {}).get("items", [])):
                yield f"{sec.key}.items.{i}.text", (it or {}).get("text"), False, sec.title
        elif sec.type == "text":
            yield f"{sec.key}.text", (data or {}).get("text"), False, sec.title


def _normalize_fields(tpl, raw: dict) -> dict:
    """把模型输出收敛到契约形状：未知键丢弃，类型偏差置空。"""
    out = empty_fields(tpl)
    raw = raw or {}
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
                if not isinstance(e, dict):
                    continue
                norm = {}
                for f in sec.entry_fields:
                    v = e.get(f.key)
                    if f.input in ("bullets", "tags"):
                        norm[f.key] = [str(x) for x in v] if isinstance(v, list) else []
                    else:
                        norm[f.key] = v if isinstance(v, str) else ""
                entries.append(norm)
            out[sec.key] = entries
        elif sec.type == "skillgroups" and isinstance(data, dict):
            groups = []
            for g in (data.get("groups") or [])[:4]:
                if isinstance(g, dict):
                    items = g.get("items")
                    groups.append({"name": str(g.get("name", "")),
                                   "items": [str(x) for x in items] if isinstance(items, list) else []})
            out[sec.key] = {"groups": groups}
        elif sec.type == "lines" and isinstance(data, dict):
            items = []
            for it in (data.get("items") or [])[:10]:
                if isinstance(it, dict):
                    items.append({"text": str(it.get("text", "")), "period": str(it.get("period", ""))})
                elif isinstance(it, str):
                    items.append({"text": it, "period": ""})
            out[sec.key] = {"items": items}
        elif sec.type == "text" and isinstance(data, dict):
            out[sec.key] = {"text": data.get("text") if isinstance(data.get("text"), str) else ""}
    return out


@router.post("/autofill")
def autofill(req: AutofillReq,
             x_anon_id: str = Header(default=""),
             x_session_token: str = Header(default="")):
    tpl = _tpl()
    _require_version(tpl, req.template_version)
    messages, _profile = _resolve_conversation(req, x_anon_id, x_session_token)
    if not any(m.get("role") == "user" and m.get("content") for m in messages):
        return {"template_version": tpl.template_version,
                "fields": empty_fields(tpl), "evidence_map": {},
                "gaps": [{"field_path": k, "label": k, "reason": "对话为空，请手动填写"}
                         for k in ("basics", "education", "projects", "skills")]}

    prompt = _prompt("autofill.md") + (
        f"\n契约结构（*为必填）：\n{_contract_brief(tpl)}\n\n"
        f"会话：\n{_conversation_text(messages)}\n")
    try:
        raw = llm.chat([{"role": "system", "content": prompt}],
                       model=QWEN_CHAT, json_mode=True, temperature=0.2)
        data = json.loads(raw)
    except Exception as e:
        raise HTTPException(status_code=502, detail={
            "code": "AUTOFILL_MODEL_FAILED", "message": str(e)[:200]})

    fields = _normalize_fields(tpl, data.get("fields"))
    gaps = list(data.get("gaps") or [])
    gap_paths = {g.get("field_path") for g in gaps if isinstance(g, dict)}

    # 证据校验：quote 必须是用户原话子串，否则证据作废（§8 红线）
    evidence_map = {}
    for path, evs in (data.get("evidence_map") or {}).items():
        if not valid_path_syntax(path) or not isinstance(evs, list):
            continue
        ok = [{"msg_id": e.get("msg_id"), "quote": e.get("quote", "")}
              for e in evs if isinstance(e, dict)
              and _user_quote_ok(e.get("quote", ""), messages)]
        if ok:
            evidence_map[path] = ok

    # 红线：非空叶子必须至少一条有效证据（自身或祖先路径），否则置空 + gap；
    # 空叶子仅在必填时补 gap。
    def _has_evidence(path: str) -> bool:
        return path in evidence_map or any(
            path.startswith(k + ".") for k in evidence_map)

    for path, v, required, label in _iter_leaves(tpl, fields):
        filled = bool(v) if not isinstance(v, list) else bool([x for x in v if str(x).strip()])
        if filled and not _has_evidence(path):
            _set_leaf(fields, path, _blank_like(v))
            if path not in gap_paths:
                gaps.append({"field_path": path, "label": label,
                             "reason": "无有效证据来源，请确认或手动填写"})
                gap_paths.add(path)
        elif not filled and required and path not in gap_paths:
            gaps.append({"field_path": path, "label": label,
                         "reason": "对话中未提及，请手动填写"})
            gap_paths.add(path)

    # 清掉被置空的空项/空条目/空 bullets
    for sec in tpl.sections:
        if sec.type == "skillgroups":
            for g in fields[sec.key].get("groups", []):
                g["items"] = [x for x in g.get("items", []) if str(x).strip()]
            fields[sec.key]["groups"] = [g for g in fields[sec.key]["groups"] if g["items"]]
        elif sec.type == "lines":
            fields[sec.key]["items"] = [
                it for it in fields[sec.key].get("items", []) if str(it.get("text", "")).strip()]
        elif sec.type == "entries":
            for e in fields[sec.key]:
                for f in sec.entry_fields:
                    if f.input in ("bullets", "tags") and isinstance(e.get(f.key), list):
                        e[f.key] = [x for x in e[f.key] if str(x).strip()]
            fields[sec.key] = [
                e for e in fields[sec.key]
                if any(str(v).strip() for v in e.values() if not isinstance(v, list))
                or any(bool(v) for v in e.values() if isinstance(v, list))]

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
def optimize_plan(req: OptimizePlanReq):
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
def optimize_section(req: OptimizeSectionReq):
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
        result = rewrite_bullets(originals, facts)
    except Exception as e:
        steps.append({"kind": "rewrite", "title": "STAR 改写",
                      "detail": f"失败：{str(e)[:120]}", "status": "error"})
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
        if suggested != original:
            reason = "STAR 改写：动词开头+量化结果" if not item.get("needs_user_data") \
                else "改写后仍缺数据，守卫落占位"
            change_log.append({"field_path": path, "before": original,
                               "after": suggested, "reason": reason})
        if item and item.get("needs_user_data"):
            blocked += 1
            placeholders.append({"field_path": path, "label": "待补充",
                                 "placeholder": extract_placeholder(suggested) or "【待补充】"})
        # 写回 optimized
        parts = path.split(".")
        if sec.type == "text":
            optimized["text"] = suggested
        else:
            optimized[int(parts[1])]["bullets"][int(parts[3])] = suggested

    steps.append({"kind": "guard", "title": "数字与事实守卫",
                  "detail": f"{blocked} 处疑似编造已拦截→占位" if blocked else "全部通过",
                  "status": "done"})
    base.update({"status": "done", "optimized": optimized, "change_log": change_log,
                 "placeholders": placeholders,
                 "pending_questions": result.get("questions") or [],
                 "needs_input": bool(placeholders or result.get("questions"))})
    return base
