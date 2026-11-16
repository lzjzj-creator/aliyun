import os
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()