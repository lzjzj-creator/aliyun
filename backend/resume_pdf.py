import asyncio
import ctypes
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

if __package__:
    from .resume_photo import PhotoInputError, photo_from_fields
else:  # The isolated worker also runs directly by absolute script path.
    from resume_photo import PhotoInputError, photo_from_fields

ASSETS = Path(__file__).with_name("resume_assets")
MAX_CONTENT_BYTES = 120000
RENDER_TIMEOUT_SECONDS = 30
WORKER_CLEANUP_SECONDS = 2
WORKER_INPUT_ERROR = 2
_slots = threading.BoundedSemaphore(2)
_metrics_lock = threading.Lock()
_metrics = {"ok": 0, "failed": 0, "timeout": 0, "rejected": 0,
            "busy": 0, "active": 0, "duration_ms_total": 0, "duration_ms_max": 0}


def render_metrics():
    with _metrics_lock:
        return {"scope": "process", "concurrency_limit": 2, **_metrics}


class PdfInputError(Exception):
    def __init__(self, issues=None):
        self.issues = issues or []


class PdfBusy(Exception):
    pass


class PdfUnavailable(Exception):
    pass


class DocumentPdfReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0, strict=True)
    layout: Literal["classic", "modern"] = "classic"


class SnapshotPdfReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_version: str = Field(min_length=1, max_length=64)
    fields: dict
    layout: Literal["classic", "modern"] = "classic"
    session_id: str | None = Field(default=None, min_length=1, max_length=64)


def validate_snapshot(template, fields):
    if not isinstance(fields, dict):
        raise PdfInputError()
    try:
        photo = photo_from_fields(fields)
    except PhotoInputError as exc:
        raise PdfInputError([exc.issue]) from None
    if len(json.dumps(fields, ensure_ascii=False).encode("utf-8")) > MAX_CONTENT_BYTES:
        raise PdfInputError()
    known = {section.key for section in template.sections}
    if set(fields) - known - {"_meta"}:
        raise PdfInputError()

    def text(value):
        if not isinstance(value, str) or len(value) > 16000:
            raise PdfInputError()
        return value

    def array(value):
        if not isinstance(value, list) or len(value) > 100:
            raise PdfInputError()
        return value

    def obj(value, keys):
        if not isinstance(value, dict) or set(value) - set(keys):
            raise PdfInputError()
        return value

    def record(value, specs):
        obj(value, [spec.key for spec in specs])
        out = {}
        for spec in specs:
            if spec.key not in value:
                continue
            item = value[spec.key]
            if spec.input in ("tags", "bullets"):
                out[spec.key] = [text(v) for v in array(item)]
            elif spec.input == "skillgroups":
                out[spec.key] = []
                for group in array(item):
                    obj(group, ("name", "items"))
                    out[spec.key].append({"name": text(group.get("name", "")),
                                          "items": [text(v) for v in array(group.get("items", []))]})
            elif spec.input == "lines":
                out[spec.key] = []
                for line in array(item):
                    obj(line, ("text", "period"))
                    out[spec.key].append({key: text(v) for key, v in line.items()})
            else:
                out[spec.key] = text(item)
        return out

    clean = {"_meta": {"photo": photo}} if photo else {}
    for section in template.sections:
        if section.key not in fields:
            continue
        value = fields[section.key]
        specs = section.spec_fields()
        clean[section.key] = ([record(entry, specs) for entry in array(value)]
                              if section.type == "entries" else record(value, specs))
    return clean


def render_pdf(template, fields, layout="classic"):
    fields = validate_snapshot(template, fields)
    if layout not in ("classic", "modern"):
        raise PdfInputError()
    if not _slots.acquire(blocking=False):
        with _metrics_lock:
            _metrics["busy"] += 1
        raise PdfBusy()
    started = time.monotonic()
    result = "failed"
    with _metrics_lock:
        _metrics["active"] += 1
    try:
        data = _run_worker(template, fields, layout)
        result = "ok"
        return data
    except PdfInputError:
        result = "rejected"
        raise
    except subprocess.TimeoutExpired:
        result = "timeout"
        raise PdfUnavailable() from None
    except Exception:
        raise PdfUnavailable() from None
    finally:
        _slots.release()
        duration = round((time.monotonic() - started) * 1000)
        with _metrics_lock:
            _metrics["active"] -= 1
            _metrics[result] += 1
            _metrics["duration_ms_total"] += duration
            _metrics["duration_ms_max"] = max(_metrics["duration_ms_max"], duration)


def _kill_posix_worker_tree(pid, deadline):
    # Chromium uses a detached group; freeze verified descendants before killing them.
    groups = [pid]
    pending = [pid]
    seen = {pid}
    try:
        os.killpg(pid, signal.SIGSTOP)
        while pending and time.monotonic() < deadline:
            parent = pending.pop()
            for task in Path(f"/proc/{parent}/task").glob("*/children"):
                try:
                    children = task.read_text().split()
                except OSError:
                    continue
                for value in children:
                    if time.monotonic() >= deadline:
                        break
                    try:
                        child = int(value)
                        if child in seen:
                            continue
                        stat = Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()
                        if int(stat[1]) != parent:
                            continue  # Reparented/reused: no longer a confirmed descendant.
                        if int(stat[2]) == child:
                            os.killpg(child, signal.SIGSTOP)
                            groups.append(child)  # Only a verified descendant group leader.
                        seen.add(child)
                        pending.append(child)
                    except (OSError, ValueError, IndexError):
                        continue
    finally:
        for group in reversed(groups):
            try:
                os.killpg(group, signal.SIGKILL)
            except OSError:
                pass


class _WindowsJob:
    """Private, non-inheritable job: ownership survives the worker's exit."""

    def __init__(self):
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits),
                        ("IoInfo", ctypes.c_ulonglong * 6),  # IO_COUNTERS
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                  wintypes.LPVOID, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        self._handle = kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self._handle, 9, ctypes.byref(limits),
                                                  ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def assign(self, worker):
        if not self._kernel.AssignProcessToJobObject(self._handle, int(worker._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self):
        if not self._kernel.TerminateJobObject(self._handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def _terminate_worker(worker, job=None):
    """Only the process/group created for this request; never a browser-name kill."""
    deadline = time.monotonic() + WORKER_CLEANUP_SECONDS
    try:
        if sys.platform == "win32":
            if job is not None:
                job.terminate()  # Even when the leader has exited and its PID is gone.
        else:
            # Popen(start_new_session=True) makes this worker the group leader.
            _kill_posix_worker_tree(worker.pid, deadline)
    except (OSError, subprocess.SubprocessError):
        pass
    # Fallback is restricted to the Popen handle we own, even if tree cleanup fails.
    try:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=max(0, deadline - time.monotonic()))
    except (OSError, subprocess.SubprocessError):
        pass


def _close_worker_pipes(worker):
    for pipe in (worker.stdin, worker.stdout):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass


def _communicate_worker(worker, payload):
    if sys.platform != "win32":
        return worker.communicate(input=payload, timeout=RENDER_TIMEOUT_SECONDS)

    # Guard Windows' synchronous stdin write within the parent deadline.
    done = threading.Event()
    outcome = []

    def communicate():
        try:
            # Keep reader joins and pipe locks off the deadline-owning thread.
            outcome.append((True, worker.communicate(input=payload)))
        except BaseException as exc:
            outcome.append((False, exc))
        finally:
            try:
                _close_worker_pipes(worker)
            finally:
                done.set()

    try:
        threading.Thread(target=communicate, daemon=True).start()
    except BaseException:
        _close_worker_pipes(worker)  # No communication/reader thread was started.
        raise
    if not done.wait(RENDER_TIMEOUT_SECONDS):
        raise subprocess.TimeoutExpired("pdf-worker", RENDER_TIMEOUT_SECONDS)
    success, value = outcome[0]
    if not success:
        raise value
    return value


def _run_worker(template, fields, layout):
    payload = json.dumps({"template": template.model_dump(), "fields": fields,
                          "layout": layout}, ensure_ascii=False).encode("utf-8")
    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
               if sys.platform == "win32" else {"start_new_session": True})
    job = _WindowsJob() if sys.platform == "win32" else None
    worker = None
    communicating = False
    try:
        worker = subprocess.Popen([sys.executable, str(Path(__file__).resolve())],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, bufsize=0, **options)
        if job is not None:
            # Assign before stdin unblocks Playwright, so descendants belong to this job.
            job.assign(worker)
        communicating = True
        output, _ = _communicate_worker(worker, payload)
    except BaseException:
        if worker is not None:
            _terminate_worker(worker, job)
        raise
    finally:
        # No Popen context manager: its implicit wait() has no deadline.
        if job is not None:
            job.close()  # Also reclaim descendants left after successful rendering.
        if worker is not None and (sys.platform != "win32" or not communicating):
            _close_worker_pipes(worker)
    if worker.returncode == WORKER_INPUT_ERROR:
        issues = json.loads(output)
        if not isinstance(issues, list) or any(
            not isinstance(issue, dict) or set(issue) != {"field_path", "label", "reason"}
            or any(not isinstance(value, str) for value in issue.values())
            for issue in issues
        ):
            raise PdfUnavailable()
        raise PdfInputError(issues)
    if worker.returncode != 0 or not output.startswith(b"%PDF-"):
        raise PdfUnavailable()
    return output


def _worker_main():
    """Private stdin/stdout protocol: 0 = PDF, 2 = issues JSON, 1 = unavailable."""
    output = sys.stdout.buffer
    try:
        # The script is launched by absolute path, so imports also work outside repo cwd.
        if __package__:
            from .resume_template import load
        else:
            from resume_template import load
        with open(os.devnull, "w", encoding="utf-8") as discarded, redirect_stdout(discarded):
            request = json.loads(sys.stdin.buffer.read())
            template = load()
            if set(request) != {"template", "fields", "layout"} or request["template"] != template.model_dump():
                return 1
            fields = validate_snapshot(template, request["fields"])
            layout = request["layout"]
            if layout not in ("classic", "modern"):
                return 1
            # Uvicorn reload can install a Selector policy; use a private Proactor loop.
            loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
            try:
                data = loop.run_until_complete(_render_pdf(template, fields, layout))
            finally:
                loop.close()
        code = 0
    except PdfInputError as exc:
        data = json.dumps(exc.issues, ensure_ascii=False).encode("utf-8")
        code = WORKER_INPUT_ERROR
    except Exception:
        return 1
    output.write(data)
    output.flush()
    return code


async def _render_pdf(template, fields, layout):
    from playwright.async_api import async_playwright

    assets = {
        "render.mjs": ("text/javascript", (ASSETS / "render.mjs").read_bytes()),
        "resume.css": ("text/css", (ASSETS / "resume.css").read_bytes()),
        "fonts.css": ("text/css", (ASSETS / "fonts.css").read_bytes()),
        "ZhimaiResume-Regular.ttf": ("font/ttf", (ASSETS / "ZhimaiResume-Regular.ttf").read_bytes()),
        "ZhimaiResume-Bold.ttf": ("font/ttf", (ASSETS / "ZhimaiResume-Bold.ttf").read_bytes()),
        "boot.mjs": ("text/javascript", b'import { renderResume, exportIssues } from "./render.mjs"; window.resumeRender = { renderResume, exportIssues };'),
    }
    origin = "https://resume.invalid/"
    html = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src data:; base-uri 'none'; form-action 'none'">
<link rel="stylesheet" href="/resume.css"><link rel="stylesheet" href="/fonts.css"><style>
html,body { margin: 0; padding: 0; background: white; }
</style><script type="module" src="/boot.mjs"></script></head><body></body></html>'''

    async def resource(route):
        url = route.request.url
        if url == origin:
            await route.fulfill(status=200, content_type="text/html", body=html)
        elif url.startswith(origin) and url[len(origin):] in assets:
            mime, body = assets[url[len(origin):]]
            await route.fulfill(status=200, content_type=mime, body=body)
        else:
            await route.abort()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, timeout=10000,
                                                   chromium_sandbox=False)
        try:
            page = await browser.new_page(viewport={"width": 794, "height": 1123},
                                          device_scale_factor=1, service_workers="block")
            page.set_default_timeout(8000)
            await page.route("**/*", resource)
            await page.goto(origin, wait_until="load", timeout=10000)
            await page.wait_for_function("Boolean(window.resumeRender)")
            issues = await page.evaluate("""([template, fields, layout]) => {
                const issues = window.resumeRender.exportIssues(template, fields);
                if (!issues.length) document.body.innerHTML = window.resumeRender.renderResume(template, fields, layout);
                return issues;
            }""", [template.model_dump(), fields, layout])
            if issues:
                raise PdfInputError(issues)
            images_ready = await page.evaluate("""async () => {
                const decoded = await Promise.all(Array.from(document.images, async image => {
                    try {
                        await image.decode();
                        return image.naturalWidth > 0 && image.naturalHeight > 0;
                    } catch { return false; }
                }));
                return decoded.every(Boolean);
            }""")
            if not images_ready:
                raise PdfInputError([{"field_path": "_meta.photo", "label": "照片",
                                      "reason": "照片无法解码，请重新上传后再导出"}])
            await page.evaluate("() => document.fonts.ready")
            if not await page.evaluate("() => document.fonts.check('11pt ZhimaiResume')"):
                raise PdfUnavailable()
            await page.emulate_media(media="print")
            return await page.pdf(format="A4", prefer_css_page_size=True,
                                  print_background=True, display_header_footer=False)
        finally:
            await browser.close()


def pdf_response(template, fields, layout):
    try:
        data = render_pdf(template, fields, layout)
    except PdfInputError as exc:
        raise HTTPException(422, {"code": "RESUME_INCOMPLETE",
                                  "message": "请补全必填信息并处理待补充内容后再导出",
                                  "issues": exc.issues}) from None
    except PdfBusy:
        raise HTTPException(429, "导出任务较多，请稍后重试", headers={"Retry-After": "5"}) from None
    except PdfUnavailable:
        raise HTTPException(503, "PDF 渲染服务暂时不可用，请稍后重试") from None
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="resume.pdf"',
        "Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff",
    })


if __name__ == "__main__":
    sys.exit(_worker_main())
