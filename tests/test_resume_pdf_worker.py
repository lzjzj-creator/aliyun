"""Process-boundary regressions; no PDF bodies or worker scripts on disk."""
import asyncio
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, call, patch

from backend import resume_pdf as pdf
from backend.resume_template import load

FIELDS = {"basics": {"name": "林知行", "target_role": "后端开发工程师", "email": "lin@example.com"},
          "summary": {"text": "精确原文：缓存命中率提升 17%。<script>原样文本</script> & Python"}}
WORKER = [sys.executable, str(Path(pdf.__file__).resolve())]


class WorkerDeadlineTest(unittest.TestCase):
    def test_two_hung_workers_including_cleanup_reclaimed_then_success(self):
        real_popen = subprocess.Popen
        started = queue.Queue()
        workers = []
        scripts = iter([
            "import sys, time; sys.stdin.buffer.read(); time.sleep(60)",
            """import asyncio, sys
sys.stdin.buffer.read()
async def render():
    try:
        await asyncio.Event().wait()
    finally:
        await asyncio.Event().wait()  # browser.close() ignores cancellation
async def main():
    await asyncio.wait_for(render(), timeout=0.01)
asyncio.run(main())
""",
        ])

        def spawn(command, **options):
            if command != WORKER:
                return real_popen(command, **options)
            worker = real_popen([sys.executable, "-c", next(scripts)], **options)
            workers.append(worker)
            started.put(worker)
            return worker

        before = pdf.render_metrics()
        start = time.monotonic()
        with patch.object(pdf, "RENDER_TIMEOUT_SECONDS", 1), \
                patch("subprocess.Popen", side_effect=spawn), ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(pdf.render_pdf, load(), FIELDS) for _ in range(2)]
            try:
                for _ in range(2):
                    started.get(timeout=5)
                with self.assertRaises(pdf.PdfBusy):
                    pdf.render_pdf(load(), FIELDS)
                for future in futures:
                    with self.assertRaises(pdf.PdfUnavailable):
                        future.result(timeout=8)
                self.assertLess(time.monotonic() - start, 8)
                self.assertTrue(all(worker.poll() is not None for worker in workers))
            finally:
                # Test safety net: only the exact processes created in this test.
                for worker in workers:
                    if worker.poll() is None:
                        worker.kill()
                    worker.wait(timeout=3)
        after = pdf.render_metrics()
        self.assertEqual(after["active"], 0)
        self.assertEqual(after["timeout"], before["timeout"] + 2)
        self.assertEqual(after["busy"], before["busy"] + 1)
        worker = Mock(returncode=0)
        worker.communicate.return_value = (b"%PDF-recovered", None)
        with patch("subprocess.Popen", return_value=worker), patch.object(pdf, "_WindowsJob"):
            for _ in range(3):
                self.assertEqual(pdf.render_pdf(load(), FIELDS), b"%PDF-recovered")

    def test_worker_not_reading_stdin_cannot_escape_deadline(self):
        real_popen = subprocess.Popen
        started = queue.Queue()

        def spawn(command, **options):
            if command != WORKER:
                return real_popen(command, **options)
            worker = real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **options)
            started.put(worker)
            return worker

        before = pdf.render_metrics()
        with patch.object(pdf, "RENDER_TIMEOUT_SECONDS", 0.2), \
                patch("subprocess.Popen", side_effect=spawn), ThreadPoolExecutor(1) as pool:
            future = pool.submit(pdf.render_pdf, load(), {**FIELDS, "summary": {"text": "x" * 16000}})
            worker = started.get(timeout=3)
            try:
                with self.assertRaises(pdf.PdfUnavailable):
                    future.result(timeout=4)
                self.assertIsNotNone(worker.poll())
            finally:
                if worker.poll() is None:
                    worker.kill()
                worker.wait(timeout=3)
        self.assertEqual(pdf.render_metrics()["timeout"], before["timeout"] + 1)
        self.assertEqual(pdf.render_metrics()["active"], 0)

    @unittest.skipUnless(os.name == "nt", "Windows process-tree cleanup")
    def test_windows_cleanup_targets_owned_job_even_after_exit_and_is_bounded(self):
        for returncode in (None, 0):
            with self.subTest(returncode=returncode):
                worker = Mock(returncode=returncode)
                worker.poll.return_value = returncode
                worker.wait.side_effect = subprocess.TimeoutExpired("worker", 1)
                job = Mock()
                job.terminate.side_effect = OSError("termination failed")
                with patch("subprocess.Popen") as spawn:
                    start = time.monotonic()
                    pdf._terminate_worker(worker, job)
                self.assertLess(time.monotonic() - start, 1)
                job.terminate.assert_called_once()
                spawn.assert_not_called()  # Never discover/kill trees using a dead PID.
                self.assertEqual(worker.kill.call_count, int(returncode is None))
                self.assertLessEqual(worker.wait.call_args.kwargs["timeout"], pdf.WORKER_CLEANUP_SECONDS)

    @unittest.skipUnless(os.name == "nt", "Windows descendant handles")
    def test_windows_timeout_kills_owned_descendant_but_not_unrelated_process(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        real_popen = subprocess.Popen
        owned = []
        children = []
        script = """import subprocess, sys, time
sys.stdin.buffer.read(1)
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(child.pid, flush=True)
sys.stdin.buffer.read()
time.sleep(60)
"""

        def spawn(command, **options):
            if command != WORKER:
                return real_popen(command, **options)
            worker = real_popen([sys.executable, "-c", script], **options)
            owned.append(worker)
            communicate = worker.communicate

            def exchange(input, **kwargs):
                worker.stdin.write(input[:1])
                worker.stdin.flush()
                pid = int(worker.stdout.readline())
                handle = kernel.OpenProcess(0x00100001, False, pid)  # SYNCHRONIZE | TERMINATE
                self.assertTrue(handle)
                children.append(handle)
                return communicate(input=input[1:], **kwargs)

            worker.communicate = exchange
            return worker

        unrelated = real_popen([sys.executable, "-c", "import time; time.sleep(60)"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with patch.object(pdf, "RENDER_TIMEOUT_SECONDS", 0.2), patch("subprocess.Popen", side_effect=spawn):
                with self.assertRaises(pdf.PdfUnavailable):
                    pdf.render_pdf(load(), FIELDS)
            self.assertIsNotNone(owned[0].poll())
            self.assertEqual(kernel.WaitForSingleObject(children[0], 2000), 0)
            self.assertIsNone(unrelated.poll())
        finally:
            for worker in [*owned, unrelated]:
                if worker.poll() is None:
                    worker.kill()
                worker.wait(timeout=3)
            for handle in children:
                if kernel.WaitForSingleObject(handle, 0) != 0:
                    kernel.TerminateProcess(handle, 1)
                    kernel.WaitForSingleObject(handle, 2000)
                kernel.CloseHandle(handle)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object assignment")
    def test_windows_assignment_failure_stops_worker_before_stdin_and_closes_job(self):
        worker = Mock(returncode=0)
        worker.poll.return_value = None
        worker.communicate.return_value = (b"%PDF-unsafe", None)
        with patch.object(pdf, "_WindowsJob") as factory, \
                patch("subprocess.Popen", return_value=worker) as spawn:
            job = factory.return_value
            job.assign.side_effect = OSError("assignment refused")
            with self.assertRaises(pdf.PdfUnavailable):
                pdf.render_pdf(load(), FIELDS)
            job.assign.assert_called_once_with(worker)
            worker.communicate.assert_not_called()
            worker.kill.assert_called_once()
            self.assertLessEqual(worker.wait.call_args.kwargs["timeout"], pdf.WORKER_CLEANUP_SECONDS)
            job.close.assert_called_once()
            spawn.assert_called_once()
        self.assertEqual(pdf.render_metrics()["active"], 0)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object lifecycle")
    def test_windows_job_is_closed_on_every_protocol_outcome(self):
        for code, output, error in ((0, b"%PDF-test", None), (2, b"[]", pdf.PdfInputError),
                                    (1, b"", pdf.PdfUnavailable), (0, b"invalid", pdf.PdfUnavailable),
                                    (2, b"invalid", pdf.PdfUnavailable)):
            with self.subTest(code=code, output=output):
                worker = Mock(returncode=code)
                order = []
                worker.communicate.side_effect = lambda **kw: (order.append("stdin") or output, None)
                with patch.object(pdf, "_WindowsJob") as factory, \
                        patch("subprocess.Popen", return_value=worker):
                    job = factory.return_value
                    job.assign.side_effect = lambda owned: order.append("assigned")
                    if error:
                        with self.assertRaises(error):
                            pdf.render_pdf(load(), FIELDS)
                    else:
                        self.assertEqual(pdf.render_pdf(load(), FIELDS), output)
                    self.assertEqual(order, ["assigned", "stdin"])
                    job.assign.assert_called_once_with(worker)
                    job.close.assert_called_once()
        with patch.object(pdf, "_WindowsJob") as factory, \
                patch("subprocess.Popen", side_effect=OSError("spawn failed")):
            with self.assertRaises(pdf.PdfUnavailable):
                pdf.render_pdf(load(), FIELDS)
            factory.return_value.close.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows communication-thread pipe ownership")
    def test_windows_deadline_does_not_wait_for_pipe_close_or_io_thread(self):
        release, closed = threading.Event(), threading.Event()
        io_threads, close_threads = [], []
        worker = Mock(returncode=0)
        worker.poll.return_value = 0

        def communicate(**kwargs):
            io_threads.append(threading.get_ident())
            release.wait(5)  # Simulate a reader not yet released by job termination.
            return b"%PDF-late", None

        def close():
            close_threads.append(threading.get_ident())
            closed.set()

        worker.communicate.side_effect = communicate
        worker.stdout.close.side_effect = close
        try:
            with patch.object(pdf, "RENDER_TIMEOUT_SECONDS", 0.05), \
                    patch.object(pdf, "_WindowsJob") as factory, \
                    patch("subprocess.Popen", return_value=worker):
                start = time.monotonic()
                with self.assertRaises(pdf.PdfUnavailable):
                    pdf.render_pdf(load(), FIELDS)
                self.assertLess(time.monotonic() - start, 0.5)
                worker.stdin.close.assert_not_called()
                worker.stdout.close.assert_not_called()
                factory.return_value.terminate.assert_called_once()
                factory.return_value.close.assert_called_once()
                self.assertEqual(pdf.render_metrics()["active"], 0)
        finally:
            release.set()
            self.assertTrue(closed.wait(2))
        self.assertEqual(close_threads, io_threads)
        self.assertNotEqual(close_threads, [threading.get_ident()])

    @unittest.skipUnless(os.name == "nt", "Windows native Job Object setup failures")
    def test_windows_job_setup_failures_do_not_leak_handles(self):
        for handle in (None, 12345):
            with self.subTest(handle=handle):
                kernel = Mock()
                kernel.CreateJobObjectW.return_value = handle
                kernel.SetInformationJobObject.return_value = 0
                with patch.object(pdf.ctypes, "WinDLL", return_value=kernel):
                    with self.assertRaises(OSError):
                        pdf._WindowsJob()
                kernel.CreateJobObjectW.assert_called_once_with(None, None)
                if handle is None:
                    kernel.CloseHandle.assert_not_called()
                else:
                    kernel.CloseHandle.assert_called_once_with(handle)
        kernel.SetInformationJobObject.return_value = 1
        kernel.CloseHandle.reset_mock()
        with patch.object(pdf.ctypes, "WinDLL", return_value=kernel):
            job = pdf._WindowsJob()
            job.close()
            job.close()
        kernel.CloseHandle.assert_called_once_with(12345)

    @unittest.skipUnless(os.name == "nt", "Windows exited worker with inherited stdout")
    def test_windows_exited_worker_descendant_cannot_hold_slot_past_deadline(self):
        self._assert_windows_exited_worker_cleanup(inherited_stdout=True)

    @unittest.skipUnless(os.name == "nt", "Windows kill-on-job-close")
    def test_windows_success_also_reaps_descendants_when_job_closes(self):
        self._assert_windows_exited_worker_cleanup(inherited_stdout=False)

    def _assert_windows_exited_worker_cleanup(self, inherited_stdout):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        real_popen = subprocess.Popen
        workers, children = [], []
        script = f"""import os, subprocess, sys
sys.stdin.buffer.read(1)  # No descendants until the parent has assigned ownership.
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                         stdin=subprocess.DEVNULL,
                         stdout={"sys.stdout" if inherited_stdout else "subprocess.DEVNULL"},
                         stderr=subprocess.DEVNULL)
print(child.pid, flush=True)
sys.stdin.buffer.read()  # Keep the child handle alive until the test pins it.
sys.stdout.buffer.write(b'%PDF-test')
sys.stdout.flush()
os._exit(0)
"""

        def spawn(command, **options):
            if command != WORKER:
                return real_popen(command, **options)
            worker = real_popen([sys.executable, "-c", script], **options)
            workers.append(worker)
            communicate = worker.communicate

            def exchange(input, **kwargs):
                worker.stdin.write(input[:1])
                worker.stdin.flush()
                pid = int(worker.stdout.readline())
                handle = kernel.OpenProcess(0x00100001, False, pid)
                self.assertTrue(handle)
                children.append(handle)
                return communicate(input=input[1:], **kwargs)

            worker.communicate = exchange
            return worker

        before = pdf.render_metrics()
        with patch.object(pdf, "RENDER_TIMEOUT_SECONDS", 0.2), \
                patch("subprocess.Popen", side_effect=spawn), ThreadPoolExecutor(1) as pool:
            start = time.monotonic()
            future = pool.submit(pdf.render_pdf, load(), FIELDS)
            try:
                # 0.2s render + 2s cleanup + scheduling margin, never the child's 60s.
                if inherited_stdout:
                    with self.assertRaises(pdf.PdfUnavailable):
                        future.result(timeout=2.7)
                else:
                    self.assertEqual(future.result(timeout=2.7), b"%PDF-test")
                self.assertLess(time.monotonic() - start, 2.7)
                self.assertEqual(workers[0].poll(), 0)  # The leader exited, not killed by cleanup.
                self.assertEqual(len(children), 1)
                self.assertEqual(kernel.WaitForSingleObject(children[0], 500), 0)
                self.assertEqual(pdf.render_metrics()["active"], 0)
                outcome = "timeout" if inherited_stdout else "ok"
                self.assertEqual(pdf.render_metrics()[outcome], before[outcome] + 1)
                acquired = []
                try:
                    for _ in range(2):
                        acquired.append(pdf._slots.acquire(blocking=False))
                    self.assertEqual(acquired, [True, True])
                finally:
                    for held in acquired:
                        if held:
                            pdf._slots.release()
            finally:
                # Independent of production cleanup: only pinned, test-created handles.
                for worker in workers:
                    if worker.poll() is None:
                        worker.kill()
                    worker.wait(timeout=3)
                for handle in children:
                    if kernel.WaitForSingleObject(handle, 0) != 0:
                        kernel.TerminateProcess(handle, 1)
                        kernel.WaitForSingleObject(handle, 2000)
                    kernel.CloseHandle(handle)

    def test_posix_cleanup_includes_detached_owned_group_not_unrelated_pid(self):
        worker = Mock(pid=31, returncode=None)
        worker.wait.side_effect = subprocess.TimeoutExpired("worker", 1)
        # /proc is read only below confirmed descendants. PID 42 was reparented
        # (or reused) and must not become a kill target; 51 shares 41's group.
        files = {
            "/proc/31/task": Mock(glob=Mock(return_value=[Mock(read_text=Mock(return_value="41 42"))])),
            "/proc/41/stat": Mock(read_text=Mock(return_value="41 (chromium) S 31 41 41")),
            "/proc/42/stat": Mock(read_text=Mock(return_value="42 (unrelated) S 999 42 42")),
            "/proc/41/task": Mock(glob=Mock(return_value=[Mock(read_text=Mock(return_value="51"))])),
            "/proc/51/stat": Mock(read_text=Mock(return_value="51 (renderer) S 41 41 41")),
            "/proc/51/task": Mock(glob=Mock(return_value=[])),
        }
        with patch.object(pdf.sys, "platform", "linux"), \
                patch.object(pdf, "Path", side_effect=lambda path: files[path]), \
                patch.object(pdf.signal, "SIGSTOP", 19, create=True), \
                patch.object(pdf.signal, "SIGKILL", 9, create=True), \
                patch("os.killpg", create=True) as kill:
            pdf._terminate_worker(worker)
        self.assertEqual(kill.call_args_list, [call(31, 19), call(41, 19), call(41, 9), call(31, 9)])
        self.assertLessEqual(worker.wait.call_args.kwargs["timeout"], 3)

    @unittest.skipUnless(sys.platform == "linux", "Linux /proc and detached process groups")
    def test_posix_timeout_reaps_worker_with_detached_child(self):
        import signal
        real_popen = subprocess.Popen
        children = []
        workers = []
        script = """import subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(child.pid, flush=True)
sys.stdin.buffer.read()
time.sleep(60)
"""

        def spawn(command, **options):
            worker = real_popen([sys.executable, "-c", script], **options)
            workers.append(worker)
            children.append(int(worker.stdout.readline()))
            return worker

        try:
            with patch.object(pdf, "RENDER_TIMEOUT_SECONDS", 0.2), patch("subprocess.Popen", side_effect=spawn):
                with self.assertRaises(pdf.PdfUnavailable):
                    pdf.render_pdf(load(), FIELDS)
            self.assertIsNotNone(workers[0].poll())
            stat = Path(f"/proc/{children[0]}/stat")
            if stat.exists():
                self.assertEqual(stat.read_text().rsplit(")", 1)[1].split()[0], "Z")
        finally:
            for pid in children:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for worker in workers:
                if worker.poll() is None:
                    os.killpg(worker.pid, signal.SIGKILL)
                worker.wait(timeout=3)

    def test_communication_error_still_terminates_worker_and_releases_slot(self):
        worker = Mock(returncode=None)
        worker.communicate.side_effect = OSError("private resume text")
        with patch("subprocess.Popen", return_value=worker), \
                patch.object(pdf, "_WindowsJob") as factory, \
                patch.object(pdf, "_terminate_worker", create=True) as terminate, \
                patch.object(pdf, "_slots") as slots:
            with self.assertRaises(pdf.PdfUnavailable) as raised:
                pdf.render_pdf(load(), FIELDS)
            self.assertEqual(str(raised.exception), "")
            worker.communicate.assert_called_once()
            job = factory.return_value if os.name == "nt" else None
            terminate.assert_called_once_with(worker, job)
            if job is not None:
                job.close.assert_called_once()
            slots.release.assert_called_once()


class WorkerProtocolTest(unittest.TestCase):
    def test_bad_worker_request_has_no_output_or_traceback(self):
        for request in (b"not JSON: private resume", b'{"template": {"url": "http://127.0.0.1"}}',
                        json.dumps({"template": {"url": "http://127.0.0.1"},
                                    "fields": FIELDS, "layout": "classic"}).encode()):
            with self.subTest(request=request):
                result = subprocess.run(WORKER, input=request, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, timeout=10)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")

    def test_worker_catches_internal_exception_without_disclosing_content(self):
        from unittest.mock import AsyncMock
        request = json.dumps({"template": load().model_dump(), "fields": FIELDS, "layout": "classic"}).encode()
        stdin = Mock(buffer=io.BytesIO(request))
        stdout = Mock(buffer=io.BytesIO())
        with patch.object(pdf.sys, "stdin", stdin), patch.object(pdf.sys, "stdout", stdout), \
                patch.object(pdf, "_render_pdf", new=AsyncMock(side_effect=RuntimeError("private resume"))):
            self.assertEqual(pdf._worker_main(), 1)
        self.assertEqual(stdout.buffer.getvalue(), b"")

    @unittest.skipUnless(os.name == "nt", "Windows Selector/Proactor regression")
    def test_worker_uses_proactor_even_under_selector_without_changing_policy(self):
        from unittest.mock import AsyncMock
        request = json.dumps({"template": load().model_dump(), "fields": FIELDS, "layout": "classic"}).encode()
        original = asyncio.get_event_loop_policy()
        policy = asyncio.WindowsSelectorEventLoopPolicy()
        seen = []

        async def render(*args):
            seen.append(asyncio.get_running_loop())
            child = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
            await child.wait()
            return b"%PDF-test"

        try:
            asyncio.set_event_loop_policy(policy)
            with patch.object(pdf.sys, "stdin", Mock(buffer=io.BytesIO(request))), \
                    patch.object(pdf.sys, "stdout", Mock(buffer=io.BytesIO())), \
                    patch.object(pdf, "_render_pdf", new=AsyncMock(side_effect=render)):
                self.assertEqual(pdf._worker_main(), 0)
            self.assertIsInstance(seen[0], asyncio.ProactorEventLoop)
            self.assertIs(asyncio.get_event_loop_policy(), policy)
        finally:
            asyncio.set_event_loop_policy(original)


class WorkerRealRenderTest(unittest.TestCase):
    def test_real_pdf_exact_text_and_incomplete_issues_with_selector_parent(self):
        from pypdf import PdfReader
        original = asyncio.get_event_loop_policy()
        policy = asyncio.WindowsSelectorEventLoopPolicy() if os.name == "nt" else original
        try:
            if os.name == "nt":
                asyncio.set_event_loop_policy(policy)
            data = pdf.render_pdf(load(), FIELDS)
            self.assertTrue(data.startswith(b"%PDF-"))
            text = "".join("".join(page.extract_text().split()) for page in PdfReader(io.BytesIO(data)).pages)
            for value in [*FIELDS["basics"].values(), FIELDS["summary"]["text"]]:
                self.assertIn("".join(value.split()), text)
            with self.assertRaises(pdf.PdfInputError) as raised:
                pdf.render_pdf(load(), {**FIELDS, "summary": {"text": "提升性能【待补充：百分比】"}})
            self.assertEqual(raised.exception.issues[0]["field_path"], "summary.text")
            self.assertTrue(raised.exception.issues[0]["reason"])
            self.assertIs(asyncio.get_event_loop_policy(), policy)
            self.assertEqual(pdf.render_metrics()["active"], 0)
        finally:
            if os.name == "nt":
                asyncio.set_event_loop_policy(original)


if __name__ == "__main__":
    unittest.main()
