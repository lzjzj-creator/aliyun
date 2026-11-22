"""Offline dev stub for the v3 walkthrough (issue #90): serves /api/v3/*
from in-memory state seeded with frontend/public/demo/v3 fixtures, plus a
tiny v2 session list so backfill/session titles resolve. No Supabase, no LLM.

    python tests/v3_dev_stub.py          # listens on :8790
    VITE_API_BASE=http://localhost:8790 npm run dev   # in frontend/

Covers the G4 privacy loop (enable/recover/export/forget/delete-all) and G5
usability flows (facts confirm/reject, stale banner, versions, duplicate,
batch). CORS allows X-Owner-Token.
"""
import json
import re
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "frontend" / "public" / "demo" / "v3"
sys.path.insert(0, str(ROOT))
PORT = 8790
TOKEN = "dev-owner-token-0000000000000000"
RECOVERY = "dev-recovery-0000"

SESSIONS = [
    {"id": "87654321-4321-4321-8321-cba987654321", "title": "秋招意向访谈",
     "stage": "done", "updated_at": "2026-11-20T09:00:00Z"},
    {"id": "76543210-4321-4321-8321-cba987654321", "title": "项目复盘",
     "stage": "done", "updated_at": "2026-11-21T14:30:00Z"},
]


def _fixture(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class State:
    def reset(self):
        self.enabled = False
        self.disabled = False
        self.facts = [dict(f) for f in _fixture("facts.json")["facts"]]
        doc = _fixture("resume-detail.json")
        doc.pop("protocol", None)
        self.docs = {doc["id"]: doc}
        self.versions = {doc["id"]: [
            {"id": str(uuid.uuid4()), "version": 1,
             "created_at": "2026-11-21T18:12:00Z",
             "snapshot": json.loads(json.dumps(doc))}]}


S = State()
S.reset()


def _public_doc(doc):
    d = json.loads(json.dumps(doc))
    d.setdefault("stale_sources", [])
    return d


def _memory_summary():
    by_kind, by_status = {}, {}
    for f in S.facts:
        if f.get("deleted_at"):
            continue
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
        by_status[f["status"]] = by_status.get(f["status"], 0) + 1
    return {"protocol": 3, "enabled": S.enabled and not S.disabled,
            "consent_at": "2026-11-20T08:00:00Z",
            "fact_count": sum(by_kind.values()),
            "by_kind": by_kind, "by_status": by_status,
            "last_extracted_at": "2026-11-22T10:05:00Z"}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    def _send(self, status, obj=None, raw=None, ctype="application/json"):
        body = raw if raw is not None else \
            json.dumps(obj or {}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-Owner-Token, X-Anon-Id, "
                         "X-Session-Token, Authorization, If-Match")
        self.send_header("Access-Control-Allow-Methods",
                         "GET, POST, PATCH, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        return self.headers.get("X-Owner-Token") == TOKEN

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def log_message(self, fmt, *args):  # keep console quiet
        pass

    def do_OPTIONS(self):
        self._send(204, raw=b"")

    def _route(self, method):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)
        body = self._body() if method in ("POST", "PATCH", "DELETE") else {}

        # -- unauthenticated lifecycle entries ---------------------------
        if path == "/api/v3/memory/enable" and method == "POST":
            S.enabled, S.disabled = True, False
            out = {"protocol": 3, "owner_token": TOKEN,
                   "owner_id": "dev-owner"}
            if body.get("recovery", True):
                out["recovery_code"] = RECOVERY
            return self._send(200, out)
        if path == "/api/v3/memory/recover" and method == "POST":
            if body.get("recovery_code") != RECOVERY:
                return self._send(404, {"detail": "not found"})
            return self._send(200, {"protocol": 3, "owner_token": TOKEN})

        if path == "/api/v2/resume/template" and method == "GET":
            return self._send(200, json.loads((ROOT / "backend/resume_template.json").read_text(encoding="utf-8")))
        if path == "/api/v2/resume/pdf" and method == "POST":
            return self._pdf(body.get("fields", {}), body.get("layout", "classic"))

        # -- v2 session list (titles/backfill picker) ---------------------
        if path == "/api/v2/sessions" and method == "GET":
            return self._send(200, SESSIONS)

        if not self._auth():
            return self._send(404, {"detail": "not found"})

        # -- memory -------------------------------------------------------
        if path == "/api/v3/memory":
            if method == "GET":
                return self._send(200, _memory_summary())
            if method == "DELETE":
                if body.get("confirm") != "DELETE_ALL":
                    return self._send(422, {"detail": "confirm required"})
                n = len(S.facts)
                S.reset()
                return self._send(200, {"protocol": 3,
                                        "deleted": {"facts": n}})
        if path == "/api/v3/memory/disable" and method == "POST":
            S.disabled = True
            return self._send(200, {"protocol": 3, "enabled": False})
        if path == "/api/v3/memory/export" and method == "GET":
            payload = {"schema_version": 3, "exported_at": time.time(),
                       "facts": S.facts, "documents": list(S.docs.values())}
            raw = json.dumps(payload, ensure_ascii=False,
                             indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition",
                             "attachment; filename=zhimai-v3-export.json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return self.wfile.write(raw)

        m = re.fullmatch(r"/api/v3/memory/sessions/([0-9a-f-]{36})", path)
        if m and method == "DELETE":
            sid, detached = m.group(1), 0
            for f in S.facts:
                ev = [e for e in f.get("evidence", [])
                      if e.get("session_id") != sid]
                if len(ev) != len(f.get("evidence", [])):
                    detached += 1
                f["evidence"] = ev
                if f.get("source_session_id") == sid:
                    f["source_session_id"] = None
            return self._send(200, {"protocol": 3,
                                    "facts_detached": detached,
                                    "documents_scrubbed": 0})

        if path == "/api/v3/memory/backfill" and method == "POST":
            ids = body.get("session_ids") or [s["id"] for s in SESSIONS]
            return self._send(200, {"protocol": 3, "results": [
                {"session_id": sid, "added": 2, "merged": 0,
                 "conflicts": 0, "skipped": 1} for sid in ids]})

        # -- facts --------------------------------------------------------
        if path == "/api/v3/facts" and method == "GET":
            facts = [f for f in S.facts if not f.get("deleted_at")]
            kind, status = qs.get("kind", [None])[0], qs.get("status", [None])[0]
            if kind:
                facts = [f for f in facts if f["kind"] == kind]
            if status:
                facts = [f for f in facts if f["status"] == status]
            return self._send(200, {"protocol": 3, "facts": facts,
                                    "next_cursor": None})

        if path == "/api/v3/facts/extract" and method == "POST":
            return self._send(200, {"protocol": 3, "added": 2, "merged": 1,
                                    "conflicts": 0, "skipped": 1})

        m = re.fullmatch(r"/api/v3/facts/([0-9a-f-]{36})", path)
        if m:
            fid = m.group(1)
            fact = next((f for f in S.facts if f["id"] == fid), None)
            if fact is None or fact.get("deleted_at"):
                return self._send(404, {"detail": "not found"})
            if method == "PATCH":
                if "status" in body:
                    fact["status"] = body["status"]
                if "value" in body:
                    fact["value"] = body["value"]
                    fact["status"] = "proposed"
                    for d in S.docs.values():
                        for fids in (d.get("evidence_map") or {}).values():
                            if any((e.get("fact_id") if isinstance(e, dict) else e) == fid for e in fids):
                                d["status"] = "stale"
                return self._send(200, {"protocol": 3, "fact": fact})
            if method == "DELETE":
                fact["deleted_at"] = time.time()
                for d in S.docs.values():
                    for fids in (d.get("evidence_map") or {}).values():
                        if any((e.get("fact_id") if isinstance(e, dict) else e) == fid for e in fids):
                            d["status"] = "stale"
                return self._send(200, {"protocol": 3, "deleted": fid})

        # -- resumes ------------------------------------------------------
        if path == "/api/v3/resumes":
            if method == "GET":
                items = [{"id": d["id"], "title": d["title"],
                          "target_role": d.get("target_role"),
                          "status": d["status"],
                          "completeness": d.get("completeness", 0.72),
                          "updated_at": d["updated_at"]}
                         for d in S.docs.values()]
                return self._send(200, {"protocol": 3, "documents": items})
            if method == "POST":
                doc = json.loads(json.dumps(
                    next(iter(S.docs.values()))))
                doc.update({"id": str(uuid.uuid4()),
                            "title": body.get("title", "新简历"),
                            "target_role": body.get("target_role"),
                            "version": 0, "status": "draft",
                            "created_at": doc["updated_at"]})
                S.docs[doc["id"]] = doc
                return self._send(200, {"protocol": 3,
                                        "document": _public_doc(doc)})

        if path == "/api/v3/resumes/batch" and method == "POST":
            out = []
            for item in body.get("items", []):
                doc = json.loads(json.dumps(next(iter(S.docs.values()))))
                doc.update({"id": str(uuid.uuid4()),
                            "title": item.get("title", "新简历"),
                            "version": 0, "status": "draft"})
                S.docs[doc["id"]] = doc
                out.append({"title": item.get("title"),
                            "document": _public_doc(doc)})
            return self._send(200, {"protocol": 3, "results": out})

        m = re.fullmatch(r"/api/v3/resumes/([0-9a-f-]{36})(.*)", path)
        if m:
            did, sub = m.group(1), m.group(2)
            doc = S.docs.get(did)
            if doc is None:
                return self._send(404, {"detail": "not found"})
            if sub == "/pdf" and method == "POST":
                if body.get("version") != doc["version"]:
                    return self._send(409, {"detail": "version conflict"})
                return self._pdf(doc.get("fields", {}), body.get("layout", "classic"))
            if sub == "" and method == "GET":
                return self._send(200, {"protocol": 3,
                                        **_public_doc(doc)})
            if sub == "" and method == "PATCH":
                want = self.headers.get("If-Match")
                if want is not None and int(want) != doc["version"]:
                    return self._send(409, {"detail": "version conflict"})
                self._snapshot(did, doc)
                doc.update({k: v for k, v in body.items()
                            if k in ("title", "fields", "status")})
                doc["version"] += 1
                return self._send(200, {"protocol": 3,
                                        "document": _public_doc(doc)})
            if sub == "" and method == "DELETE":
                del S.docs[did]
                return self._send(200, {"protocol": 3, "deleted": did})
            if sub == "/duplicate" and method == "POST":
                dup = json.loads(json.dumps(doc))
                dup.update({"id": str(uuid.uuid4()),
                            "title": (doc["title"] + "（副本）")[:80],
                            "version": 0, "status": "draft"})
                S.docs[dup["id"]] = dup
                return self._send(200, {"protocol": 3,
                                        "document": _public_doc(dup)})
            if sub == "/regenerate" and method == "POST":
                self._snapshot(did, doc)
                doc["version"] += 1
                doc["status"] = "draft"
                doc["updated_at"] = "2026-11-23T09:00:00Z"
                return self._send(200, {"protocol": 3,
                                        "document": _public_doc(doc)})
            if sub == "/versions" and method == "GET":
                vs = [{"id": v["id"], "version": v["version"],
                       "created_at": v["created_at"]}
                      for v in S.versions.get(did, [])][-10:][::-1]
                return self._send(200, {"protocol": 3, "versions": vs})
            m2 = re.fullmatch(r"/versions/(\d+)/restore", sub)
            if m2 and method == "POST":
                v = next((x for x in S.versions.get(did, [])
                          if x["version"] == int(m2.group(1))), None)
                if v is None:
                    return self._send(404, {"detail": "not found"})
                self._snapshot(did, doc)
                doc.update({k: v["snapshot"][k] for k in
                            ("title", "target_role", "status", "fields",
                             "evidence_map", "gaps", "fact_snapshot")
                            if k in v["snapshot"]})
                doc["version"] += 1
                return self._send(200, {"protocol": 3,
                                        "document": _public_doc(doc)})

        return self._send(404, {"detail": "not found"})

    def _pdf(self, fields, layout):
        from fastapi import HTTPException
        from backend.resume_pdf import pdf_response
        from backend.resume_template import load
        try:
            response = pdf_response(load(), fields, layout)
        except HTTPException as exc:
            return self._send(exc.status_code, {"detail": exc.detail})
        return self._send(200, raw=response.body, ctype="application/pdf")

    def _snapshot(self, did, doc):
        S.versions.setdefault(did, []).append({
            "id": str(uuid.uuid4()), "version": doc["version"],
            "created_at": "2026-11-23T09:00:00Z",
            "snapshot": json.loads(json.dumps(doc))})
        S.versions[did] = S.versions[did][-10:]

    do_GET = lambda s: s._route("GET")
    do_POST = lambda s: s._route("POST")
    do_PATCH = lambda s: s._route("PATCH")
    do_DELETE = lambda s: s._route("DELETE")


if __name__ == "__main__":
    print(f"v3 dev stub on http://localhost:{PORT}  "
          f"(token: {TOKEN}, recovery: {RECOVERY})")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
