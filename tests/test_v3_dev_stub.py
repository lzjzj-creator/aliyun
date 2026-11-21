"""Smoke for tests/v3_dev_stub.py so the walkthrough stub can't rot."""
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from tests import v3_dev_stub


class V3DevStubTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), v3_dev_stub.H)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def call(self, path, method="GET", body=None, token=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                   method=method)
        if token:
            r.add_header("X-Owner-Token", token)
        if body is not None:
            r.add_header("Content-Type", "application/json")
            r.data = json.dumps(body).encode()
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_privacy_loop(self):
        # unauthenticated → 404 (never reveals the resource exists)
        status, _ = self.call("/api/v3/memory")
        self.assertEqual(status, 404)

        status, data = self.call("/api/v3/memory/enable", "POST",
                                 {"recovery": True})
        self.assertEqual(status, 200)
        token = data["owner_token"]

        _, mem = self.call("/api/v3/memory", token=token)
        self.assertTrue(mem["enabled"])
        self.assertEqual(mem["protocol"], 3)

        _, facts = self.call("/api/v3/facts", token=token)
        self.assertGreater(len(facts["facts"]), 0)

        _, out = self.call("/api/v3/memory/sessions/"
                           "87654321-4321-4321-8321-cba987654321",
                           "DELETE", token=token)
        self.assertIn("facts_detached", out)

        _, doc = self.call("/api/v3/resumes", token=token)
        did = doc["documents"][0]["id"]
        _, detail = self.call(f"/api/v3/resumes/{did}", token=token)
        _, vs = self.call(f"/api/v3/resumes/{did}/versions", token=token)
        self.assertIn("versions", vs)
        _, dup = self.call(f"/api/v3/resumes/{did}/duplicate", "POST",
                           token=token)
        self.assertEqual(dup["document"]["version"], 0)

        _, gone = self.call("/api/v3/memory", "DELETE",
                            {"confirm": "DELETE_ALL"}, token=token)
        self.assertIn("deleted", gone)


if __name__ == "__main__":
    unittest.main()
