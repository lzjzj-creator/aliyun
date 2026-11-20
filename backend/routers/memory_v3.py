"""v3 owner-memory routes (/api/v3/*) — contract §8, guarded by V3_MEMORY_ENABLED.

Auth is X-Owner-Token only (never anon_id). Invalid owner credentials share the
v2 IDOR convention: a uniform 404, never distinguishing absent vs forbidden.
"""
import time

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from .. import fact_extractor, memory_v3
    from .. import session_security as security
    from .. import session_security_v3 as security_v3
except ImportError:  # FC starts with backend on sys.path
    import fact_extractor
    import memory_v3
    import session_security as security
    import session_security_v3 as security_v3

router = APIRouter(prefix="/api/v3")

# Per-owner extraction cap (in-process; the model budget still gates cost).
EXTRACT_PER_HOUR = 30
_extract_hits: dict[str, list] = {}


def _fact_public(row: dict) -> dict:
    return {"id": row["id"], "kind": row["kind"], "slot_path": row["slot_path"],
            "value": row["value"], "status": row["status"],
            "evidence": row.get("evidence") or [],
            "created_at": row.get("created_at")}


def _owner(request: Request, x_owner_token) -> dict:
    security.enforce_ip(request)
    return security_v3.require_owner(x_owner_token)


def _storage(call):
    try:
        return call()
    except memory_v3.V3NotFound:
        raise security.not_found() from None
    except memory_v3.MemoryStoreError:
        raise HTTPException(503, "存储暂时不可用") from None
    except ValueError:
        raise HTTPException(422, "请求参数无效") from None


def _extract_limit(owner_id: str):
    now = time.monotonic()
    hits = [t for t in _extract_hits.get(owner_id, []) if now - t < 3600]
    if len(hits) >= EXTRACT_PER_HOUR:
        raise security.throttled(int(3600 - (now - hits[0])))
    hits.append(now)
    _extract_hits[owner_id] = hits[-EXTRACT_PER_HOUR:]


class EnableReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recovery: bool = True


class DeleteAllReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str

    @field_validator("confirm")
    @classmethod
    def exact_phrase(cls, value):
        if value != "DELETE_ALL":
            raise ValueError("confirm must be DELETE_ALL")
        return value


class PatchFactReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str | None = Field(default=None)
    value: object | None = Field(default=None)

    @field_validator("status")
    @classmethod
    def status_choice(cls, value):
        if value is not None and value not in ("confirmed", "rejected"):
            raise ValueError("status must be confirmed or rejected")
        return value


class ExtractReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=36, max_length=36)


class BackfillReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 不传则用 owner.anon_id 名下的全部 v2 会话；显式传入时上限 30
    session_ids: list[str] | None = Field(default=None, max_length=30)

    @field_validator("session_ids")
    @classmethod
    def ids_bounded(cls, value):
        import uuid
        for sid in value or []:
            try:
                uuid.UUID(sid)
            except (ValueError, AttributeError):
                raise ValueError("invalid session_id") from None
        return value


BACKFILL_PER_HOUR = 6
_backfill_hits: dict[str, list] = {}


def _backfill_limit(owner_id: str):
    now = time.monotonic()
    hits = [t for t in _backfill_hits.get(owner_id, []) if now - t < 3600]
    if len(hits) >= BACKFILL_PER_HOUR:
        raise security.throttled(int(3600 - (now - hits[0])))
    hits.append(now)
    _backfill_hits[owner_id] = hits[-BACKFILL_PER_HOUR:]


@router.post("/memory/enable")
def enable_memory(request: Request, req: EnableReq | None = None,
                  x_owner_token: str | None = Header(default=None),
                  x_anon_id: str | None = Header(default=None)):
    """First enable mints a token+recovery code (returned once); a disabled
    owner presenting its old token reactivates instead of forking identity."""
    security.enforce_ip(request)
    issued = _storage(
        lambda: memory_v3.get_owner_by_key_hash(
            security_v3.hash_token(x_owner_token)) if x_owner_token else None)
    if issued is not None:
        if not issued.get("memory_enabled"):
            issued = _storage(
                lambda: memory_v3.set_memory_enabled(issued["id"], True))
        return {"enabled": True, "owner_id": issued["id"], "reactivated": True}
    bundle = _storage(lambda: security_v3.issue_owner(
        x_anon_id if isinstance(x_anon_id, str) else None))
    return {"enabled": True, "owner_id": bundle["owner"]["id"],
            "owner_token": bundle["owner_token"],
            "recovery_code": bundle["recovery_code"]}


RETENTION_DAYS = 365
RETENTION_WARN_DAYS = 30


@router.get("/memory")
def memory_status(request: Request,
                  x_owner_token: str | None = Header(default=None)):
    owner = _owner(request, x_owner_token)
    counts = _storage(lambda: memory_v3.fact_counts(owner["id"]))
    # 保留期站内提示（§9/D4）：last_seen_at 距今 ≥335 天提示将到期
    warn = False
    last_seen = owner.get("last_seen_at") or owner.get("updated_at")
    if isinstance(last_seen, str):
        try:
            from datetime import datetime, timezone
            seen = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - seen).days
            warn = age >= RETENTION_DAYS - RETENTION_WARN_DAYS
        except ValueError:
            pass
    return {"protocol": 3, "enabled": owner["memory_enabled"],
            "consent_at": owner.get("consent_at"),
            "fact_count": counts["total"], "by_kind": counts["by_kind"],
            "by_status": counts["by_status"],
            "last_extracted_at": _storage(
                lambda: memory_v3.last_extracted_at(owner["id"])),
            "retention_days": RETENTION_DAYS, "retention_warning": warn}


@router.get("/memory/export")
def memory_export(request: Request,
                  x_owner_token: str | None = Header(default=None)):
    owner = _owner(request, x_owner_token)
    payload = _storage(lambda: memory_v3.export_owner(owner["id"]))
    payload["protocol"] = 3
    from fastapi.responses import JSONResponse
    return JSONResponse(
        payload,
        headers={"Content-Disposition":
                 'attachment; filename="zhimai-memory.json"'})


@router.post("/memory/disable")
def disable_memory(request: Request,
                   x_owner_token: str | None = Header(default=None)):
    owner = _owner(request, x_owner_token)
    row = _storage(lambda: memory_v3.set_memory_enabled(owner["id"], False))
    return {"enabled": row["memory_enabled"], "owner_id": row["id"]}


@router.delete("/memory")
def delete_memory(request: Request, req: DeleteAllReq,
                  x_owner_token: str | None = Header(default=None)):
    owner = _owner(request, x_owner_token)
    deleted = _storage(lambda: memory_v3.delete_owner(owner["id"]))
    return {"deleted": deleted}


@router.get("/facts")
def list_facts(request: Request,
               kind: str | None = Query(default=None),
               status: str | None = Query(default=None),
               cursor: str | None = Query(default=None, max_length=200),
               limit: int = Query(default=50, ge=1,
                                  le=memory_v3.FACT_LIST_MAX),
               x_owner_token: str | None = Header(default=None)):
    owner = _owner(request, x_owner_token)
    page = _storage(lambda: memory_v3.list_facts(
        owner["id"], kind=kind, status=status, cursor=cursor, limit=limit))
    return {"facts": [_fact_public(f) for f in page["facts"]],
            "next_cursor": page["next_cursor"]}


@router.patch("/facts/{fact_id}")
def patch_fact(request: Request, fact_id: str, req: PatchFactReq,
               x_owner_token: str | None = Header(default=None)):
    owner = _owner(request, x_owner_token)
    if req.status is None and req.value is None:
        raise HTTPException(422, "请求参数无效")
    row = _storage(lambda: memory_v3.get_fact(owner["id"], fact_id))
    if row is None:
        raise security.not_found()
    if req.value is not None:  # edit = new fact + old superseded
        row = _storage(lambda: memory_v3.supersede_fact(
            owner["id"], fact_id, req.value, status=req.status))
    elif req.status is not None:
        row = _storage(lambda: memory_v3.set_fact_status(
            owner["id"], fact_id, req.status))
    return {"fact": _fact_public(row)}


@router.delete("/facts/{fact_id}")
def delete_fact(request: Request, fact_id: str,
                x_owner_token: str | None = Header(default=None)):
    owner = _owner(request, x_owner_token)
    ok = _storage(lambda: memory_v3.soft_delete_fact(owner["id"], fact_id))
    if not ok:
        raise security.not_found()
    return {"deleted": fact_id}


@router.post("/facts/extract")
def extract_facts(request: Request, req: ExtractReq,
                  x_owner_token: str | None = Header(default=None)):
    owner = _owner(request, x_owner_token)
    _extract_limit(owner["id"])
    try:
        result = fact_extractor.extract_incremental(owner["id"], req.session_id)
    except memory_v3.V3NotFound:
        raise security.not_found() from None
    except memory_v3.MemoryStoreError:
        raise HTTPException(503, "存储暂时不可用") from None
    except fact_extractor.ExtractionFailed:
        raise HTTPException(503, "抽取暂时不可用") from None
    return result


@router.post("/memory/backfill")
def backfill_memory(request: Request, req: BackfillReq,
                  x_owner_token: str | None = Header(default=None)):
    """P3 历史回填：逐会话跑增量抽取；每会话独立水位，失败单列不阻断整体。"""
    owner = _owner(request, x_owner_token)
    _backfill_limit(owner["id"])
    session_ids = req.session_ids
    if session_ids is None:
        anon = owner.get("anon_id")
        if not anon:
            raise HTTPException(422, "请求参数无效")
        try:
            import memory
            session_ids = [s["id"] for s in
                           memory.list_sessions(anon)[:30]]
        except Exception:
            raise HTTPException(503, "存储暂时不可用") from None
    results = []
    for sid in session_ids:
        try:
            r = fact_extractor.extract_incremental(owner["id"], sid)
            results.append({"session_id": sid, **r})
        except memory_v3.V3NotFound:
            results.append({"session_id": sid, "error": "not_found"})
        except (memory_v3.MemoryStoreError, fact_extractor.ExtractionFailed):
            results.append({"session_id": sid, "error": "failed"})
    return {"protocol": 3, "processed": len(results), "results": results}
