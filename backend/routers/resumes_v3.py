"""v3 resume-library routes (/api/v3/resumes*) — contract §8, V3_MEMORY_ENABLED.

Documents persist server-side per §15-D1. Evidence stays extractive (builder),
manual edits are tracked in fields._meta.manual_override and survive regenerate.
"""
import json

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from .. import memory_v3, resume_builder_v3, model_budget
    from .. import session_security as security
    from .. import session_security_v3 as security_v3
    from ..resume_template import load
    from .resume import _iter_leaves
except ImportError:  # FC starts with backend on sys.path
    import memory_v3
    import resume_builder_v3
    import model_budget
    import session_security as security
    import session_security_v3 as security_v3
    from resume_template import load
    from routers.resume import _iter_leaves

router = APIRouter(prefix="/api/v3/resumes")
MAX_JD_TEXT = 4000


RESUME_GEN_PER_DAY = 20


def _gen_limit(owner_id: str):
    """Persistent per-owner daily cap on resume generations (§10);
    storage wobble degrades to allow, never to a user-facing 5xx."""
    try:
        if memory_v3.spend_daily_quota(owner_id, "resume_gen",
                                       RESUME_GEN_PER_DAY) < 0:
            raise security.throttled(86400)
    except memory_v3.MemoryStoreError:
        pass


def _owner(request: Request, x_owner_token) -> dict:
    security.enforce_ip(request)
    return security_v3.require_owner(x_owner_token)


def _storage(call):
    try:
        return call()
    except memory_v3.V3NotFound:
        raise security.not_found() from None
    except memory_v3.V3Conflict:
        raise HTTPException(409, {"code": "VERSION_CONFLICT",
                                  "message": "版本冲突，请刷新后重试"}) from None
    except memory_v3.MemoryStoreError:
        raise HTTPException(503, "存储暂时不可用") from None
    except ValueError:
        raise HTTPException(422, "请求参数无效") from None


def _doc(request, x_owner_token, doc_id) -> dict:
    owner = _owner(request, x_owner_token)
    doc = _storage(lambda: memory_v3.get_document(owner["id"], doc_id))
    if doc is None:
        raise security.not_found()
    return owner, doc


def _completeness(doc: dict) -> float:
    leaves = list(_iter_leaves(load(), doc.get("fields") or {}))
    if not leaves:
        return 0.0
    filled = sum(1 for _, v, _, _ in leaves
                 if isinstance(v, str) and v.strip())
    return round(filled / len(leaves), 2)


def _list_public(doc: dict) -> dict:
    return {"id": doc["id"], "title": doc["title"],
            "target_role": doc.get("target_role"), "status": doc["status"],
            "completeness": _completeness(doc),
            "updated_at": doc.get("updated_at")}


def _detail_public(owner_id: str, doc: dict) -> dict:
    stale = _storage(lambda: memory_v3.stale_sources(
        doc, memory_v3.facts_for_generation(owner_id)))
    out = dict(doc)
    out.pop("owner_id", None)
    out["stale_sources"] = stale
    return out


def _expected_version(if_match: str | None) -> int:
    if not isinstance(if_match, str) or not if_match.strip().isdigit():
        raise HTTPException(422, "请求参数无效")
    return int(if_match.strip())


def _manual_paths(old_fields: dict, new_fields: dict) -> list:
    tpl = load()
    old = {p: v for p, v, _, _ in _iter_leaves(tpl, old_fields or {})}
    new = {p: v for p, v, _, _ in _iter_leaves(tpl, new_fields or {})}
    return sorted(p for p in set(old) | set(new) if old.get(p) != new.get(p))


class CreateDocReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=80)
    target_role: str | None = Field(default=None, max_length=80)
    jd_text: str | None = Field(default=None, max_length=MAX_JD_TEXT)
    template_version: str | None = Field(default=None, max_length=64)


class PatchDocReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=80)
    fields: dict | None = None
    status: str | None = Field(default=None)

    @field_validator("status")
    @classmethod
    def status_choice(cls, value):
        if value is not None and value not in memory_v3.DOC_STATUSES:
            raise ValueError("invalid document status")
        return value

    @field_validator("fields")
    @classmethod
    def fields_bounded(cls, value):
        if value is not None and \
                len(json.dumps(value, ensure_ascii=False)) > 120000:
            raise ValueError("fields too large")
        return value


class BatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=80)
    target_role: str | None = Field(default=None, max_length=80)
    jd_text: str | None = Field(default=None, max_length=MAX_JD_TEXT)


class BatchReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[BatchItem] = Field(min_length=1, max_length=5)
    template_version: str | None = Field(default=None, max_length=64)


class RegenReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_role: str | None = Field(default=None, max_length=80)
    jd_text: str | None = Field(default=None, max_length=MAX_JD_TEXT)
    keep_manual: bool = True


@router.get("")
def list_resumes(request: Request,
                 x_owner_token: str | None = Header(default=None)):
    owner = _owner(request, x_owner_token)
    docs = _storage(lambda: memory_v3.list_documents(owner["id"]))
    return {"protocol": 3, "documents": [_list_public(d) for d in docs]}


@router.post("")
def create_resume(request: Request, req: CreateDocReq,
                  x_owner_token: str | None = Header(default=None)):
    owner = _owner(request, x_owner_token)
    _gen_limit(owner["id"])
    try:
        doc = resume_builder_v3.create_document(
            owner["id"], title=req.title, target_role=req.target_role,
            jd_text=req.jd_text, template_version=req.template_version)
    except model_budget.BudgetUnavailable:
        raise security.throttled(86400) from None
    except resume_builder_v3.BuildFailed:
        raise HTTPException(503, "简历生成服务暂时不可用") from None
    except ValueError:
        raise HTTPException(422, "请求参数无效") from None
    except memory_v3.MemoryStoreError:
        raise HTTPException(503, "存储暂时不可用") from None
    return {"protocol": 3, "document": _detail_public(owner["id"], doc)}


@router.post("/batch")
def create_batch(request: Request, req: BatchReq,
                 x_owner_token: str | None = Header(default=None)):
    """P3 多岗位批量生成：逐项独立，单项失败不拖垮整批；带 JD 时附覆盖报告。"""
    owner = _owner(request, x_owner_token)
    results = []
    for item in req.items:
        try:
            if memory_v3.spend_daily_quota(
                    owner["id"], "resume_gen", RESUME_GEN_PER_DAY) < 0:
                results.append({"title": item.title, "error": "daily_quota"})
                continue
        except memory_v3.MemoryStoreError:
            pass  # 降级放行，不阻断整批
        try:
            doc = resume_builder_v3.create_document(
                owner["id"], title=item.title, target_role=item.target_role,
                jd_text=item.jd_text, template_version=req.template_version)
            entry = {"title": item.title, "document": _detail_public(
                owner["id"], doc)}
            if item.jd_text:
                entry["jd_coverage"] = resume_builder_v3.jd_coverage(
                    doc.get("fields") or {}, item.jd_text)
            results.append(entry)
        except model_budget.BudgetUnavailable:
            results.append({"title": item.title, "error": "daily_quota"})
        except resume_builder_v3.BuildFailed:
            results.append({"title": item.title, "error": "build_failed"})
        except (ValueError, memory_v3.MemoryStoreError):
            results.append({"title": item.title, "error": "failed"})
    return {"protocol": 3, "results": results}


@router.get("/{doc_id}")
def get_resume(request: Request, doc_id: str,
               x_owner_token: str | None = Header(default=None)):
    owner, doc = _doc(request, x_owner_token, doc_id)
    return {"protocol": 3, **_detail_public(owner["id"], doc)}


@router.patch("/{doc_id}")
def patch_resume(request: Request, doc_id: str, req: PatchDocReq,
                 if_match: str | None = Header(default=None),
                 x_owner_token: str | None = Header(default=None)):
    owner, doc = _doc(request, x_owner_token, doc_id)
    expected = _expected_version(if_match)
    updates = {}
    if req.title is not None:
        updates["title"] = req.title
    if req.status is not None:
        updates["status"] = req.status
    if req.fields is not None:
        fields = dict(req.fields)
        changed = _manual_paths(doc.get("fields") or {}, fields)
        meta = dict((fields.get("_meta") or {}))
        prior = [p for p in (meta.get("manual_override") or [])
                 if isinstance(p, str)]
        meta["manual_override"] = sorted(set(prior) | set(changed))
        fields["_meta"] = meta
        updates["fields"] = fields
    if not updates:
        raise HTTPException(422, "请求参数无效")
    doc = _storage(lambda: memory_v3.patch_document(
        owner["id"], doc_id, updates, expected, snapshot=doc))
    return {"protocol": 3, "document": _detail_public(owner["id"], doc)}


@router.get("/{doc_id}/versions")
def list_versions(request: Request, doc_id: str,
                  x_owner_token: str | None = Header(default=None)):
    owner, doc = _doc(request, x_owner_token, doc_id)
    versions = _storage(lambda: memory_v3.list_document_versions(
        owner["id"], doc_id))
    return {"protocol": 3, "versions": versions}


@router.post("/{doc_id}/versions/{version}/restore")
def restore_version(request: Request, doc_id: str, version: int,
                    x_owner_token: str | None = Header(default=None)):
    """Restore a kept snapshot as a NEW version (snapshot first → undoable)."""
    owner, doc = _doc(request, x_owner_token, doc_id)
    row = _storage(lambda: memory_v3.get_document_version(
        owner["id"], doc_id, version))
    if row is None:
        raise security.not_found()
    snap = row.get("snapshot") or {}
    updates = {k: snap[k] for k in
               ("title", "target_role", "status", "fields",
                "evidence_map", "gaps", "fact_snapshot")
               if k in snap}
    if not updates:
        raise HTTPException(422, "请求参数无效")
    doc = _storage(lambda: memory_v3.patch_document(
        owner["id"], doc_id, updates, doc["version"], snapshot=doc))
    return {"protocol": 3, "document": _detail_public(owner["id"], doc)}


@router.post("/{doc_id}/duplicate")
def duplicate_resume(request: Request, doc_id: str,
                     x_owner_token: str | None = Header(default=None)):
    """P3 一稿多投：整稿复制为新草稿（含字段/证据/快照），version 归零。"""
    owner, doc = _doc(request, x_owner_token, doc_id)
    dup = _storage(lambda: memory_v3.duplicate_document(owner["id"], doc))
    return {"protocol": 3, "document": _detail_public(owner["id"], dup)}


@router.delete("/{doc_id}")
def delete_resume(request: Request, doc_id: str,
                  x_owner_token: str | None = Header(default=None)):
    owner = _owner(request, x_owner_token)
    ok = _storage(lambda: memory_v3.soft_delete_document(owner["id"], doc_id))
    if not ok:
        raise security.not_found()
    return {"protocol": 3, "deleted": doc_id}


@router.post("/{doc_id}/regenerate")
def regenerate_resume(request: Request, doc_id: str, req: RegenReq,
                      x_owner_token: str | None = Header(default=None)):
    owner, doc = _doc(request, x_owner_token, doc_id)
    _gen_limit(owner["id"])
    tpl = load()
    try:
        built = resume_builder_v3.regenerate_fields(
            doc, tpl, target_role=req.target_role, jd_text=req.jd_text,
            keep_manual=req.keep_manual)
    except model_budget.BudgetUnavailable:
        raise security.throttled(86400) from None
    except resume_builder_v3.BuildFailed:
        raise HTTPException(503, "简历生成服务暂时不可用") from None
    updates = {"fields": built["fields"],
               "evidence_map": built["evidence_map"],
               "gaps": built["gaps"],
               "fact_snapshot": built["fact_snapshot"],
               "status": "draft"}
    if req.target_role:
        updates["target_role"] = req.target_role
    if req.jd_text:
        updates["jd_digest"] = resume_builder_v3._jd_digest(req.jd_text)
    doc = _storage(lambda: memory_v3.patch_document(
        owner["id"], doc_id, updates, doc["version"], snapshot=doc))
    return {"protocol": 3, "document": _detail_public(owner["id"], doc)}
