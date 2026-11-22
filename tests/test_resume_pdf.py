import importlib
import json
import os
import subprocess
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("SUPABASE_URL", "https://supabase.invalid")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("DASHSCOPE_API_KEY", "test-key")

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

v2 = importlib.import_module("backend.routers.resume")
v3 = importlib.import_module("backend.routers.resumes_v3")
ANON = "12345678-1234-4234-8234-123456789abc"
DOC_ID = "d0c0ffee-2222-4000-8000-000000000001"
FIELDS = {"basics": {"name": "测试求职者", "target_role": "后端工程师", "email": "resume@example.com"}}
DOC = {"id": DOC_ID, "owner_id": ANON, "title": "简历", "version": 3,
       "template_version": "1.0.0", "fields": FIELDS}


class PdfRoutesTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(v2.router)
        app.include_router(v3.router)
        self.client = TestClient(app, raise_server_exceptions=False)
        p = patch.object(v2.security, "enforce_ip")
        p.start()
        self.addCleanup(p.stop)

    def test_snapshot_requires_local_identity(self):
        response = self.client.post("/api/v2/resume/pdf", json={
            "template_version": "1.0.0", "fields": FIELDS})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "资源不存在")

    def test_snapshot_rejects_unknown_template(self):
        response = self.client.post("/api/v2/resume/pdf", headers={"X-Anon-Id": ANON},
                                    json={"template_version": "old", "fields": FIELDS})
        self.assertEqual(response.status_code, 409)

    def test_snapshot_verifies_any_referenced_session(self):
        with patch.object(v2.security, "require_owner", side_effect=HTTPException(404, "资源不存在")) as auth:
            response = self.client.post("/api/v2/resume/pdf", headers={"X-Anon-Id": ANON},
                                        json={"template_version": "1.0.0", "fields": FIELDS,
                                              "session_id": DOC_ID})
        self.assertEqual(response.status_code, 404)
        auth.assert_called_once_with(DOC_ID, ANON, None)

    def test_document_export_requires_matching_version(self):
        with patch.object(v3, "_doc", return_value=({"id": ANON}, DOC)):
            response = self.client.post(f"/api/v3/resumes/{DOC_ID}/pdf", json={"version": 2})
        self.assertEqual(response.status_code, 409)

    def test_document_export_does_not_accept_client_fields_or_url(self):
        for extra in ({"fields": FIELDS}, {"url": "http://127.0.0.1/"}, {"html": "<script>1</script>"}):
            with self.subTest(extra=extra):
                response = self.client.post(f"/api/v3/resumes/{DOC_ID}/pdf", json={"version": 3, **extra})
                self.assertEqual(response.status_code, 422)

    def test_invalid_layout_rejected(self):
        response = self.client.post("/api/v2/resume/pdf", headers={"X-Anon-Id": ANON},
                                    json={"template_version": "1.0.0", "fields": FIELDS,
                                          "layout": "<script>"})
        self.assertEqual(response.status_code, 422)


class PdfEngineTest(unittest.TestCase):
    def setUp(self):
        # Protocol unit tests use fake Popen handles; real jobs are covered in worker tests.
        p = patch("backend.resume_pdf._WindowsJob")
        factory = p.start()
        self.addCleanup(p.stop)
        self.job = factory.return_value if os.name == "nt" else None

    def test_size_and_shape_are_checked_before_browser(self):
        pdf = importlib.import_module("backend.resume_pdf")
        for fields in ({"basics": []}, {"projects": ["bad"]},
                       {"basics": {"name": {"html": "bad"}}},
                       {"skills": {"groups": [{"items": [False]}]}},
                       {"summary": {"text": "a" * 120001}}):
            with self.subTest(fields=str(fields)[:70]):
                with self.assertRaises(pdf.PdfInputError):
                    pdf.validate_snapshot(v2._tpl(), fields)

    def test_metadata_is_not_sent_to_renderer(self):
        pdf = importlib.import_module("backend.resume_pdf")
        result = pdf.validate_snapshot(v2._tpl(), {**FIELDS, "_meta": {"secret": "internal"}})
        self.assertEqual(result, FIELDS)

    def test_concurrency_limit_fails_without_starting_worker(self):
        pdf = importlib.import_module("backend.resume_pdf")
        with patch.object(pdf, "_slots") as slots, patch("subprocess.Popen") as spawn:
            slots.acquire.return_value = False
            with self.assertRaises(pdf.PdfBusy):
                pdf.render_pdf(v2._tpl(), FIELDS)
            slots.release.assert_not_called()
            spawn.assert_not_called()

    def test_renderer_deadline_releases_capacity(self):
        pdf = importlib.import_module("backend.resume_pdf")
        worker = Mock(returncode=None)
        worker.communicate.side_effect = subprocess.TimeoutExpired("worker", 0.01)
        before = pdf.render_metrics()
        with patch.object(pdf, "RENDER_TIMEOUT_SECONDS", 0.01), \
                patch("subprocess.Popen", return_value=worker), \
                patch.object(pdf, "_terminate_worker", create=True) as terminate, \
                patch.object(pdf, "_slots") as slots:
            slots.acquire.return_value = True
            with self.assertRaises(pdf.PdfUnavailable):
                pdf.render_pdf(v2._tpl(), FIELDS)
            slots.release.assert_called_once()
            terminate.assert_called_once_with(worker, self.job)
        self.assertEqual(pdf.render_metrics()["timeout"], before["timeout"] + 1)

    def test_metrics_count_only_outcomes_and_duration_without_content(self):
        pdf = importlib.import_module("backend.resume_pdf")
        worker = Mock(returncode=0)
        worker.communicate.return_value = (b"%PDF-test", None)
        before = pdf.render_metrics()
        with patch("subprocess.Popen", return_value=worker):
            pdf.render_pdf(v2._tpl(), FIELDS)
        after = pdf.render_metrics()
        self.assertEqual(after["ok"], before["ok"] + 1)
        self.assertEqual(after["active"], 0)
        self.assertEqual(after["scope"], "process")
        self.assertNotIn("测试求职者", str(after))
        self.assertNotIn("resume@example.com", str(after))

    def test_worker_receives_only_fixed_template_clean_fields_and_layout(self):
        import sys
        from pathlib import Path
        pdf = importlib.import_module("backend.resume_pdf")
        worker = Mock(returncode=0)
        worker.communicate.return_value = (b"%PDF-test", None)
        with patch("subprocess.Popen", return_value=worker) as spawn:
            result = pdf.render_pdf(v2._tpl(), {**FIELDS, "_meta": {"secret": "private"}}, "modern")
        self.assertEqual(result, b"%PDF-test")
        self.assertEqual(spawn.call_args.args[0], [sys.executable, str(Path(pdf.__file__).resolve())])
        options = spawn.call_args.kwargs
        self.assertEqual(options["stdin"], subprocess.PIPE)
        self.assertEqual(options["stdout"], subprocess.PIPE)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)
        self.assertFalse(options.get("shell", False))
        if os.name == "nt":
            self.assertTrue(options["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            self.assertTrue(options["start_new_session"])
        request = json.loads(worker.communicate.call_args.kwargs["input"])
        self.assertEqual(request, {"template": v2._tpl().model_dump(), "fields": FIELDS, "layout": "modern"})
        if os.name == "nt":
            self.assertNotIn("timeout", worker.communicate.call_args.kwargs)
        else:
            self.assertEqual(worker.communicate.call_args.kwargs["timeout"], pdf.RENDER_TIMEOUT_SECONDS)

    def test_worker_input_issues_keep_422_protocol_and_release_slot(self):
        pdf = importlib.import_module("backend.resume_pdf")
        issues = [{"field_path": "basics.name", "label": "姓名", "reason": "请填写姓名"}]
        worker = Mock(returncode=2)
        worker.communicate.return_value = (json.dumps(issues).encode(), None)
        before = pdf.render_metrics()
        with patch("subprocess.Popen", return_value=worker), patch.object(pdf, "_slots") as slots:
            with self.assertRaises(HTTPException) as raised:
                pdf.pdf_response(v2._tpl(), FIELDS, "classic")
            slots.release.assert_called_once()
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.detail["issues"], issues)
        self.assertEqual(raised.exception.detail["code"], "RESUME_INCOMPLETE")
        self.assertEqual(pdf.render_metrics()["rejected"], before["rejected"] + 1)

    def test_worker_errors_are_sanitized_and_release_slot(self):
        pdf = importlib.import_module("backend.resume_pdf")
        for code, output in ((1, b"resume@example.com"), (0, b"not a PDF"),
                             (2, b"invalid json"), (2, b'{"secret": "private"}'),
                             (2, b'[{"arbitrary": "private"}]')):
            with self.subTest(code=code, output=output):
                worker = Mock(returncode=code)
                worker.communicate.return_value = (output, None)
                with patch("subprocess.Popen", return_value=worker), patch.object(pdf, "_slots") as slots:
                    with self.assertRaises(HTTPException) as raised:
                        pdf.pdf_response(v2._tpl(), FIELDS, "classic")
                    slots.release.assert_called_once()
                self.assertEqual(raised.exception.status_code, 503)
                self.assertNotIn("private", str(raised.exception.detail))
                self.assertNotIn("resume@example.com", str(raised.exception.detail))
        with patch("subprocess.Popen", side_effect=OSError("private")), patch.object(pdf, "_slots") as slots:
            with self.assertRaises(HTTPException) as raised:
                pdf.pdf_response(v2._tpl(), FIELDS, "classic")
            slots.release.assert_called_once()
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(pdf.render_metrics()["active"], 0)

    def test_invalid_input_never_starts_worker(self):
        pdf = importlib.import_module("backend.resume_pdf")
        with patch("subprocess.Popen") as spawn:
            for fields, layout in (({"url": "http://127.0.0.1"}, "classic"), (FIELDS, "invalid")):
                with self.assertRaises(pdf.PdfInputError):
                    pdf.render_pdf(v2._tpl(), fields, layout)
            spawn.assert_not_called()

    def test_metrics_endpoint_requires_admin(self):
        admin = importlib.import_module("backend.admin")
        app = FastAPI()
        app.include_router(admin.router)
        with patch.object(admin, "admin_ready", return_value=True):
            response = TestClient(app).get("/api/admin/pdf-metrics")
        self.assertEqual(response.status_code, 401)
        app.dependency_overrides[admin.require_admin] = lambda: None
        response = TestClient(app).get("/api/admin/pdf-metrics")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["scope"], "process")

    def test_versioned_document_uses_only_saved_snapshot(self):
        pdf = importlib.import_module("backend.resume_pdf")
        app = FastAPI()
        app.include_router(v3.router)
        with patch.object(v3, "_doc", return_value=({"id": ANON}, DOC)), \
                patch.object(pdf, "render_pdf", return_value=b"%PDF-1.7 test") as render:
            response = TestClient(app).post(f"/api/v3/resumes/{DOC_ID}/pdf",
                                            json={"version": 3, "layout": "modern"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(render.call_args.args[1], FIELDS)
        self.assertEqual(render.call_args.args[2], "modern")


if __name__ == "__main__":
    unittest.main()
