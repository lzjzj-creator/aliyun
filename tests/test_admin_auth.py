import os
import unittest
from unittest.mock import patch

from starlette.requests import Request

from backend.admin import _client_key
from backend.admin_auth import (admin_ready, hash_password, issue_session,
                                verify_password, verify_session)

SECRET = "test-secret-" + "x" * 32


class PasswordTest(unittest.TestCase):
    def test_roundtrip(self):
        encoded = hash_password("correct horse", iterations=1000)
        self.assertTrue(verify_password("correct horse", encoded))

    def test_wrong_password(self):
        encoded = hash_password("correct horse", iterations=1000)
        self.assertFalse(verify_password("wrong", encoded))

    def test_malformed_hash(self):
        self.assertFalse(verify_password("x", "plain-text"))
        self.assertFalse(verify_password("x", "pbkdf2_sha256$bad"))

    def test_admin_ready_requires_hash_and_secret(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD_HASH": "", "ADMIN_SESSION_SECRET": ""}):
            self.assertFalse(admin_ready())
        with patch.dict(os.environ, {
            "ADMIN_PASSWORD_HASH": hash_password("x", iterations=1000),
            "ADMIN_SESSION_SECRET": "short",
        }):
            self.assertFalse(admin_ready())
        with patch.dict(os.environ, {
            "ADMIN_PASSWORD_HASH": hash_password("x", iterations=1000),
            "ADMIN_SESSION_SECRET": SECRET,
        }):
            self.assertTrue(admin_ready())


class SessionTest(unittest.TestCase):
    def test_valid_token(self):
        token = issue_session(SECRET, ttl=600, now=1000)
        self.assertTrue(verify_session(token, SECRET, now=1200))

    def test_expired_token(self):
        token = issue_session(SECRET, ttl=60, now=1000)
        self.assertFalse(verify_session(token, SECRET, now=1061))

    def test_tampered_payload(self):
        token = issue_session(SECRET, ttl=600, now=1000)
        body, signature = token.split(".", 1)
        forged = ("A" if body[0] != "A" else "B") + body[1:]
        self.assertFalse(verify_session(f"{forged}.{signature}", SECRET, now=1100))

    def test_tampered_signature(self):
        token = issue_session(SECRET, ttl=600, now=1000)
        body, signature = token.split(".", 1)
        forged = ("A" if signature[0] != "A" else "B") + signature[1:]
        self.assertFalse(verify_session(f"{body}.{forged}", SECRET, now=1100))

    def test_wrong_secret(self):
        token = issue_session(SECRET, ttl=600, now=1000)
        self.assertFalse(verify_session(token, SECRET + "y", now=1100))

    def test_malformed_token(self):
        for token in ("", "abc", "a.b.c", "!!!.???"):
            self.assertFalse(verify_session(token, SECRET, now=1100))


class ClientKeyTest(unittest.TestCase):
    def request(self, xff=None, client=("10.0.0.9", 1234)):
        headers = [] if xff is None else [(b"x-forwarded-for", xff.encode())]
        return Request({"type": "http", "headers": headers, "client": client})

    def test_uses_last_xff_hop(self):
        self.assertEqual(_client_key(self.request("198.51.100.1, 203.0.113.7")), "203.0.113.7")

    def test_strips_last_hop_whitespace(self):
        self.assertEqual(_client_key(self.request("198.51.100.1 , 203.0.113.7 ")), "203.0.113.7")

    def test_single_xff_value(self):
        self.assertEqual(_client_key(self.request("203.0.113.7")), "203.0.113.7")

    def test_no_xff_falls_back_to_client_host(self):
        self.assertEqual(_client_key(self.request(client=("192.0.2.5", 443))), "192.0.2.5")

    def test_blank_xff_falls_back_to_client_host(self):
        self.assertEqual(_client_key(self.request("   ", client=("192.0.2.6", 80))), "192.0.2.6")

    def test_missing_client_uses_unknown(self):
        self.assertEqual(_client_key(self.request(client=None)), "unknown")


if __name__ == "__main__":
    unittest.main()
