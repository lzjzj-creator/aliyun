"""v3 owner-scoped storage (Supabase service-role REST).

Trusted storage primitives, NOT an authorization boundary. Routes must verify
X-Owner-Token via session_security_v3.require_owner before any call here.
Owner rows never expose owner_key_hash/recovery_hash to callers; the raw
owner_token and recovery_code are returned exactly once at enable/rotate.

Fact dedup is by value_hash (sha256 of kind+slot_path+normalized value): a
repeated value only appends its new source to evidence (capped at 8 entries).
stale_sources are computed on read by diffing fact_snapshot against live
facts—no write amplification when facts change.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID

try:
    from . import memory
except ImportError:  # FC runs with backend as root
    import memory

_request = memory._request
_json = memory._json
_one = memory._one
MemoryStoreError = memory.MemoryStoreError

OWNER_FIELDS = "id,anon_id,memory_enabled,consent_at,created_at,last_seen_at"
FACT_KINDS = {"identity", "goal", "education", "experience", "skill",
              "award", "metric", "constraint"}
FACT_STATUSES = {"proposed", "confirmed", "rejected", "superseded"}
GENERATION_STATUSES = ("confirmed", "proposed")
DOC_STATUSES = {"draft", "ready", "archived"}
EVIDENCE_CAP = 8
FACT_LIST_MAX = 200


class V3NotFound(MemoryStoreError):
    """No matching owner/fact/document exists (or it is deleted)."""


class V3Conflict(MemoryStoreError):
    """CAS version mismatch on a resume document; reload before retrying."""


def _uuid(value):
    return str(UUID(str(value)))


def _check_owner_id(owner_id):
    try:
        return _uuid(owner_id)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("owner_id must be a uuid")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _now() -> str:
    """PostgREST clients send real timestamps; 'now()' is not a timestamptz literal."""
    return datetime.now(timezone.utc).isoformat()


# --- owner -------------------------------------------------------------------

def _public_owner(row):
    return {key: row[key] for key in OWNER_FIELDS.split(",") if key in row}


def enable_owner(anon_id: str | None, owner_key_hash: str,
                 recovery_hash: str | None) -> dict:
    """Insert an enabled owner; token/recovery hashes are precomputed by caller."""
    payload = {"owner_key_hash": owner_key_hash,
               "recovery_hash": recovery_hash,
               "memory_enabled": True, "consent_at": _now()}
    if anon_id:
        payload["anon_id"] = anon_id
    rows = _json(_request("POST", "/v3_owner", params={"select": OWNER_FIELDS},
                          headers={"Prefer": "return=representation"},
                          json=payload))
    if not rows:
        raise MemoryStoreError("Owner insert returned no row")
    return rows[0]


def get_owner_by_key_hash(owner_key_hash: str) -> dict | None:
    if not isinstance(owner_key_hash, str) or len(owner_key_hash) != 64:
        return None
    return _one(_json(_request("GET", "/v3_owner", params={
        "owner_key_hash": f"eq.{owner_key_hash}", "deleted_at": "is.null",
        "select": "id,owner_key_hash,recovery_hash," + OWNER_FIELDS, "limit": 1,
    })))


def get_owner_by_recovery_hash(recovery_hash: str) -> dict | None:
    if not isinstance(recovery_hash, str) or len(recovery_hash) != 64:
        return None
    return _one(_json(_request("GET", "/v3_owner", params={
        "recovery_hash": f"eq.{recovery_hash}", "deleted_at": "is.null",
        "select": "id,recovery_hash," + OWNER_FIELDS, "limit": 1,
    })))


def get_owner(owner_id: str) -> dict | None:
    return _one(_json(_request("GET", "/v3_owner", params={
        "id": f"eq.{_check_owner_id(owner_id)}", "deleted_at": "is.null",
        "select": "id,recovery_hash," + OWNER_FIELDS, "limit": 1,
    })))


def touch_owner(owner_id: str) -> bool:
    """Best-effort last_seen_at bump for the 365-day retention clock; never raises."""
    try:
        rows = _json(_request("PATCH", "/v3_owner", params={
            "id": f"eq.{_uuid(owner_id)}", "select": "id",
        }, headers={"Prefer": "return=representation"}, json={"last_seen_at": _now()}))
        return bool(rows)
    except MemoryStoreError:
        return False


def set_memory_enabled(owner_id: str, enabled: bool) -> dict:
    if type(enabled) is not bool:
        raise ValueError("enabled must be boolean")
    rows = _json(_request("PATCH", "/v3_owner", params={
        "id": f"eq.{_check_owner_id(owner_id)}", "select": OWNER_FIELDS,
    }, headers={"Prefer": "return=representation"},
        json={"memory_enabled": enabled}))
    if not rows:
        raise V3NotFound("Owner not found")
    return rows[0]


def rotate_owner_key(owner_id: str, new_key_hash: str,
                     new_recovery_hash: str | None = None) -> dict:
    """Replace the owner key (recovery-code redemption) and optionally rotate the
    recovery code; the previous key hash stops matching immediately."""
    payload = {"owner_key_hash": new_key_hash}
    if new_recovery_hash is not None:
        payload["recovery_hash"] = new_recovery_hash
    rows = _json(_request("PATCH", "/v3_owner", params={
        "id": f"eq.{_check_owner_id(owner_id)}", "select": OWNER_FIELDS,
    }, headers={"Prefer": "return=representation"}, json=payload))
    if not rows:
        raise V3NotFound("Owner not found")
    return rows[0]


def delete_owner(owner_id: str) -> dict:
    """Hard delete: facts/documents/state first (for the count), owner last."""
    owner_id = _check_owner_id(owner_id)
    counts = {}
    for table, key in (("v3_facts", "facts"),
                       ("v3_resume_documents", "documents"),
                       ("v3_extraction_state", "extraction_state")):
        rows = _json(_request("DELETE", f"/{table}", params={
            "owner_id": f"eq.{owner_id}", "select": "owner_id",
        }, headers={"Prefer": "return=representation"}))
        counts[key] = len(rows)
    rows = _json(_request("DELETE", "/v3_owner", params={
        "id": f"eq.{owner_id}", "select": "id",
    }, headers={"Prefer": "return=representation"}))
    counts["owner"] = len(rows)
    if not counts["owner"]:
        raise V3NotFound("Owner not found")
    return counts


# --- facts -------------------------------------------------------------------

def fact_value_hash(kind: str, slot_path: str, value) -> str:
    """sha256 of the normalized fact identity; normalized = compact sorted JSON."""
    normalized = json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"))
    return _sha256(f"{kind}|{slot_path}|{normalized}")


def _check_fact_input(kind, slot_path, value, status, confidence):
    if kind not in FACT_KINDS:
        raise ValueError("kind must be a whitelisted fact kind")
    if not isinstance(slot_path, str) or not 1 <= len(slot_path) <= 120:
        raise ValueError("slot_path must be 1-120 chars")
    if len(json.dumps(value, ensure_ascii=False)) > 512:
        raise ValueError("value must be at most 512 chars as JSON")
    if status not in FACT_STATUSES:
        raise ValueError("invalid fact status")
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1):
        raise ValueError("confidence must be between zero and one")


def _check_evidence(evidence):
    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list")
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("evidence entries must be objects")


def insert_fact(owner_id: str, kind: str, slot_path: str, value, *,
                status: str = "proposed", confidence: float = 0,
                source_session_id: str | None = None,
                source_message_id: int | None = None,
                evidence: list | None = None) -> tuple[dict, bool]:
    """Insert one atomic fact. Returns (row, created).

    On a live duplicate value_hash the new source is appended to the existing
    row's evidence (cap 8) and (row, False) is returned—never a second row.
    """
    owner_id = _check_owner_id(owner_id)
    _check_fact_input(kind, slot_path, value, status, confidence)
    evidence = evidence or []
    _check_evidence(evidence)
    if source_session_id is not None:
        source_session_id = _uuid(source_session_id)
    if source_message_id is not None and type(source_message_id) is not int:
        raise ValueError("source_message_id must be an integer")
    payload = {
        "owner_id": owner_id, "kind": kind, "slot_path": slot_path,
        "value": value, "value_hash": fact_value_hash(kind, slot_path, value),
        "status": status, "confidence": confidence,
        "source_session_id": source_session_id,
        "source_message_id": source_message_id,
        "evidence": evidence[:EVIDENCE_CAP],
    }
    response = _request("POST", "/v3_facts",
                        headers={"Prefer": "return=representation"}, json=payload)
    if response.status_code == 409:
        try:
            duplicate = response.json().get("code") == "23505"
        except (ValueError, AttributeError):
            duplicate = False
        if duplicate:
            existing = _one(_json(_request("GET", "/v3_facts", params={
                "owner_id": f"eq.{owner_id}", "value_hash": f"eq.{payload['value_hash']}",
                "deleted_at": "is.null", "select": "*", "limit": 1,
            })))
            if existing:
                merged = merge_evidence(existing.get("evidence") or [], evidence)
                if merged != existing["evidence"]:
                    existing = _one(_json(_request("PATCH", "/v3_facts", params={
                        "id": f"eq.{existing['id']}", "select": "*",
                    }, headers={"Prefer": "return=representation"},
                        json={"evidence": merged}))) or existing
                return existing, False
    rows = _json(response)
    if not rows:
        raise MemoryStoreError("Fact insert returned no row")
    return rows[0], True


def merge_evidence(existing: list, new_items: list, cap: int = EVIDENCE_CAP) -> list:
    """Append unseen evidence entries (dedup on session_id+msg_id+quote), capped."""
    out = list(existing or [])
    seen = {(e.get("session_id"), e.get("msg_id"), e.get("quote"))
            for e in out if isinstance(e, dict)}
    for item in new_items or []:
        if not isinstance(item, dict):
            continue
        key = (item.get("session_id"), item.get("msg_id"), item.get("quote"))
        if key not in seen:
            seen.add(key)
            out.append(item)
        if len(out) >= cap:
            break
    return out[:cap]


def get_fact_by_hash(owner_id: str, value_hash: str) -> dict | None:
    """Any live fact with this value_hash (any status—rejected and superseded
    rows must also block re-insertion of the identical value)."""
    if not isinstance(value_hash, str) or len(value_hash) != 64:
        return None
    return _one(_json(_request("GET", "/v3_facts", params={
        "owner_id": f"eq.{_check_owner_id(owner_id)}",
        "value_hash": f"eq.{value_hash}", "deleted_at": "is.null",
        "select": "*", "limit": 1,
    })))


def get_fact(owner_id: str, fact_id: str) -> dict | None:
    return _one(_json(_request("GET", "/v3_facts", params={
        "id": f"eq.{_uuid(fact_id)}", "owner_id": f"eq.{_check_owner_id(owner_id)}",
        "deleted_at": "is.null", "select": "*", "limit": 1,
    })))


def list_facts(owner_id: str, *, kind: str | None = None, status: str | None = None,
               cursor: str | None = None, limit: int = 50) -> dict:
    """Keyset pagination on (created_at, id); cursor is '<created_at>~<id>'."""
    owner_id = _check_owner_id(owner_id)
    if type(limit) is not int or not 1 <= limit <= FACT_LIST_MAX:
        raise ValueError("limit must be between 1 and 200")
    params = {
        "owner_id": f"eq.{owner_id}", "deleted_at": "is.null",
        "select": "*", "order": "created_at.asc,id.asc", "limit": limit + 1,
    }
    if kind:
        if kind not in FACT_KINDS:
            raise ValueError("kind must be a whitelisted fact kind")
        params["kind"] = f"eq.{kind}"
    if status:
        if status not in FACT_STATUSES:
            raise ValueError("invalid fact status")
        params["status"] = f"eq.{status}"
    if cursor:
        try:
            c_at, c_id = cursor.split("~", 1)
            _uuid(c_id)
        except (ValueError, AttributeError):
            raise ValueError("invalid cursor")
        params["or"] = (f"(created_at.gt.{c_at},"
                        f"and(created_at.eq.{c_at},id.gt.{c_id}))")
    rows = _json(_request("GET", "/v3_facts", params=params))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = f"{last['created_at']}~{last['id']}"
    return {"facts": rows, "next_cursor": next_cursor}


def facts_for_generation(owner_id: str) -> list:
    """All live confirmed+proposed facts (proposed participates but is flagged)."""
    rows = _json(_request("GET", "/v3_facts", params={
        "owner_id": f"eq.{_check_owner_id(owner_id)}", "deleted_at": "is.null",
        "status": f"in.({','.join(GENERATION_STATUSES)})",
        "select": "*", "order": "created_at.asc,id.asc", "limit": 2000,
    }))
    return rows


def set_fact_status(owner_id: str, fact_id: str, status: str) -> dict:
    if status not in ("confirmed", "rejected"):
        raise ValueError("status must be confirmed or rejected")
    rows = _json(_request("PATCH", "/v3_facts", params={
        "id": f"eq.{_uuid(fact_id)}", "owner_id": f"eq.{_check_owner_id(owner_id)}",
        "deleted_at": "is.null", "select": "*",
    }, headers={"Prefer": "return=representation"}, json={"status": status}))
    if not rows:
        raise V3NotFound("Fact not found")
    return rows[0]


def supersede_fact(owner_id: str, fact_id: str, new_value, *,
                   status: str | None = None, confidence: float | None = None,
                   evidence: list | None = None) -> dict:
    """Edit-by-replace: insert a new fact, then mark the old one superseded."""
    old = get_fact(owner_id, fact_id)
    if old is None:
        raise V3NotFound("Fact not found")
    row, _created = insert_fact(
        owner_id, old["kind"], old["slot_path"], new_value,
        status=status or old["status"],
        confidence=old["confidence"] if confidence is None else confidence,
        source_session_id=old.get("source_session_id"),
        source_message_id=old.get("source_message_id"),
        evidence=evidence if evidence is not None else [])
    marked = _json(_request("PATCH", "/v3_facts", params={
        "id": f"eq.{old['id']}", "select": "*",
    }, headers={"Prefer": "return=representation"},
        json={"status": "superseded", "superseded_by": row["id"]}))
    if not marked:
        raise MemoryStoreError("Supersede mark failed")
    return row


def soft_delete_fact(owner_id: str, fact_id: str) -> bool:
    rows = _json(_request("PATCH", "/v3_facts", params={
        "id": f"eq.{_uuid(fact_id)}", "owner_id": f"eq.{_check_owner_id(owner_id)}",
        "deleted_at": "is.null", "select": "id",
    }, headers={"Prefer": "return=representation"}, json={"deleted_at": _now()}))
    return bool(rows)


def detach_session_facts(owner_id: str, session_id: str) -> int:
    """Forget-this-session: null the source pointer AND strip that session's
    evidence entries (the session's原话 must not survive its deletion)."""
    owner_id = _check_owner_id(owner_id)
    session_id = _uuid(session_id)
    rows = _json(_request("GET", "/v3_facts", params={
        "owner_id": f"eq.{owner_id}", "source_session_id": f"eq.{session_id}",
        "deleted_at": "is.null", "select": "id,evidence", "limit": 2000,
    }))
    count = 0
    for row in rows:
        kept = [e for e in (row.get("evidence") or [])
                if not (isinstance(e, dict) and e.get("session_id") == session_id)]
        patched = _json(_request("PATCH", "/v3_facts", params={
            "id": f"eq.{row['id']}", "select": "id",
        }, headers={"Prefer": "return=representation"}, json={
            "source_session_id": None, "source_message_id": None,
            "evidence": kept,
        }))
        count += len(patched)
    return count


def fact_counts(owner_id: str) -> dict:
    """by_kind / by_status / total over live facts (owner-scale: one projection)."""
    rows = _json(_request("GET", "/v3_facts", params={
        "owner_id": f"eq.{_check_owner_id(owner_id)}", "deleted_at": "is.null",
        "select": "kind,status", "limit": 2000,
    }))
    by_kind, by_status = {}, {}
    for row in rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    return {"total": len(rows), "by_kind": by_kind, "by_status": by_status}


# --- extraction state ----------------------------------------------------------

def get_extraction_state(owner_id: str, session_id: str) -> dict | None:
    return _one(_json(_request("GET", "/v3_extraction_state", params={
        "owner_id": f"eq.{_check_owner_id(owner_id)}",
        "session_id": f"eq.{_uuid(session_id)}", "select": "*", "limit": 1,
    })))


def upsert_watermark(owner_id: str, session_id: str, last_message_id: int) -> dict:
    """Advance the watermark; repeated extracts never re-read earlier messages."""
    if type(last_message_id) is not int or last_message_id < 0:
        raise ValueError("last_message_id must be a nonnegative integer")
    payload = {"owner_id": _check_owner_id(owner_id),
               "session_id": _uuid(session_id),
               "last_message_id": last_message_id,
               "attempts": 0, "last_error_code": None,
               "extracted_at": _now()}
    rows = _json(_request("POST", "/v3_extraction_state", json=payload, params={
        "select": "*",
    }, headers={"Prefer": "return=representation,resolution=merge-duplicates"}))
    if not rows:
        raise MemoryStoreError("Watermark upsert returned no row")
    return rows[0]


def mark_extraction_error(owner_id: str, session_id: str, error_code: str) -> dict:
    """Failure bookkeeping only—chat is never affected; 3 strikes -> blocked."""
    owner_id = _check_owner_id(owner_id)
    session_id = _uuid(session_id)
    state = get_extraction_state(owner_id, session_id) or {
        "owner_id": owner_id, "session_id": session_id, "last_message_id": 0,
        "attempts": 0}
    payload = {"owner_id": owner_id, "session_id": session_id,
               "last_message_id": state["last_message_id"],
               "attempts": int(state.get("attempts") or 0) + 1,
               "last_error_code": (error_code or "UNKNOWN")[:80]}
    rows = _json(_request("POST", "/v3_extraction_state", json=payload, params={
        "select": "*",
    }, headers={"Prefer": "return=representation,resolution=merge-duplicates"}))
    if not rows:
        raise MemoryStoreError("Extraction error upsert returned no row")
    return rows[0]


def last_extracted_at(owner_id: str) -> str | None:
    rows = _json(_request("GET", "/v3_extraction_state", params={
        "owner_id": f"eq.{_check_owner_id(owner_id)}", "select": "extracted_at",
        "extracted_at": "not.is.null", "order": "extracted_at.desc", "limit": 1,
    }))
    return rows[0]["extracted_at"] if rows else None


# --- resume documents ----------------------------------------------------------

def create_document(owner_id: str, *, title: str, template_version: str,
                    target_role: str | None = None, jd_digest: str | None = None,
                    fields: dict | None = None, evidence_map: dict | None = None,
                    gaps: list | None = None, fact_snapshot: list | None = None,
                    status: str = "draft") -> dict:
    owner_id = _check_owner_id(owner_id)
    if not isinstance(title, str) or not 1 <= len(title) <= 80:
        raise ValueError("title must be 1-80 chars")
    if not isinstance(template_version, str) or not template_version:
        raise ValueError("template_version is required")
    if status not in DOC_STATUSES:
        raise ValueError("invalid document status")
    for name, blob, kind in (("fields", fields or {}, dict),
                             ("evidence_map", evidence_map or {}, dict),
                             ("gaps", gaps or [], list),
                             ("fact_snapshot", fact_snapshot or [], list)):
        if not isinstance(blob, kind):
            raise ValueError(f"{name} must be a {kind.__name__}")
    rows = _json(_request("POST", "/v3_resume_documents",
                          headers={"Prefer": "return=representation"}, json={
        "owner_id": owner_id, "title": title,
        "template_version": template_version,
        "target_role": target_role, "jd_digest": jd_digest,
        "fields": copy.deepcopy(fields or {}),
        "evidence_map": copy.deepcopy(evidence_map or {}),
        "gaps": copy.deepcopy(gaps or []),
        "fact_snapshot": copy.deepcopy(fact_snapshot or []),
        "status": status,
    }))
    if not rows:
        raise MemoryStoreError("Document insert returned no row")
    return rows[0]


def get_document(owner_id: str, doc_id: str) -> dict | None:
    return _one(_json(_request("GET", "/v3_resume_documents", params={
        "id": f"eq.{_uuid(doc_id)}", "owner_id": f"eq.{_check_owner_id(owner_id)}",
        "deleted_at": "is.null", "select": "*", "limit": 1,
    })))


def list_documents(owner_id: str) -> list:
    return _json(_request("GET", "/v3_resume_documents", params={
        "owner_id": f"eq.{_check_owner_id(owner_id)}", "deleted_at": "is.null",
        "select": "id,title,target_role,status,version,updated_at,created_at,"
                  "fields,gaps,fact_snapshot",
        "order": "updated_at.desc,id.desc", "limit": 500,
    }))


def patch_document(owner_id: str, doc_id: str, updates: dict,
                   expected_version: int) -> dict:
    """CAS update; version filter makes concurrent writers conflict instead of
    silently overwriting each other."""
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError("expected_version must be a nonnegative integer")
    allowed = {"title", "target_role", "jd_digest", "fields", "evidence_map",
               "gaps", "fact_snapshot", "status"}
    payload = {k: v for k, v in updates.items() if k in allowed}
    if "title" in payload and (not isinstance(payload["title"], str)
                               or not 1 <= len(payload["title"]) <= 80):
        raise ValueError("title must be 1-80 chars")
    if "status" in payload and payload["status"] not in DOC_STATUSES:
        raise ValueError("invalid document status")
    payload["version"] = expected_version + 1
    rows = _json(_request("PATCH", "/v3_resume_documents", params={
        "id": f"eq.{_uuid(doc_id)}", "owner_id": f"eq.{_check_owner_id(owner_id)}",
        "version": f"eq.{expected_version}", "select": "*",
    }, headers={"Prefer": "return=representation"}, json=payload))
    if not rows:
        raise V3Conflict("Document version conflict")
    return rows[0]


def soft_delete_document(owner_id: str, doc_id: str) -> bool:
    rows = _json(_request("PATCH", "/v3_resume_documents", params={
        "id": f"eq.{_uuid(doc_id)}", "owner_id": f"eq.{_check_owner_id(owner_id)}",
        "deleted_at": "is.null", "select": "id",
    }, headers={"Prefer": "return=representation"}, json={"deleted_at": _now()}))
    return bool(rows)


# --- snapshot diff / export -----------------------------------------------------

def stale_sources(doc: dict, current_facts: list) -> list:
    """Diff doc.fact_snapshot against live facts + evidence_map to find which
    leaf fields lost or outgrew their sources. [{field_path, fact_id, change}]"""
    live = {f["id"]: f for f in current_facts or []}
    snapshot = {s.get("fact_id"): s.get("value_hash")
                for s in (doc.get("fact_snapshot") or []) if isinstance(s, dict)}
    stale_facts = {}
    for fact_id, snap_hash in snapshot.items():
        current = live.get(fact_id)
        if current is None:
            stale_facts[fact_id] = "deleted"
        elif current.get("value_hash") != snap_hash:
            stale_facts[fact_id] = "updated"
    if not stale_facts:
        return []
    out = []
    for path, evs in (doc.get("evidence_map") or {}).items():
        for e in evs if isinstance(evs, list) else []:
            fact_id = isinstance(e, dict) and e.get("fact_id")
            if fact_id in stale_facts:
                out.append({"field_path": path, "fact_id": fact_id,
                            "change": stale_facts[fact_id]})
                break
    return out


def prune_inactive_owners(days: int = 365, dry_run: bool = True) -> int:
    """Owner retention sweep (§9/D4): dry-run report first, then explicit run."""
    if type(days) is not int or days < 365:
        raise ValueError("days must be an integer >= 365")
    return int(_json(_request("POST", "/rpc/v3_prune_inactive_owners",
                              json={"days": days, "dry_run": dry_run})))


def export_owner(owner_id: str) -> dict:
    """Full portable dump: live facts (with evidence) + live documents."""
    owner_id = _check_owner_id(owner_id)
    owner = get_owner(owner_id)
    if owner is None:
        raise V3NotFound("Owner not found")
    facts = _json(_request("GET", "/v3_facts", params={
        "owner_id": f"eq.{owner_id}", "select": "*",
        "order": "created_at.asc,id.asc", "limit": 5000,
    }))
    documents = _json(_request("GET", "/v3_resume_documents", params={
        "owner_id": f"eq.{owner_id}", "deleted_at": "is.null", "select": "*",
        "order": "created_at.asc,id.asc", "limit": 500,
    }))
    return {"schema_version": 1, "owner": _public_owner(owner),
            "facts": facts, "documents": documents}
