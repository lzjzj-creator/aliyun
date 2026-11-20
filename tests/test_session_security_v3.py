"""Offline v3 owner-auth tests: token/recovery primitives + 404 semantics."""
import base64
import hashlib
import importlib
import os
import re
import unittest
from unittest.mock import patch

from fastapi import HTTPException

with patch.dict(os.environ, {
    "SUPABASE_URL": "https://supabase.invalid",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "KB_SOURCE_BASE": "https://mirror.invalid",
}):
    memory = importlib.import_module("backend.memory")
    memory_v3 = importlib.import_module("backend.memory_v3")
    security_v3 = importlib.import_module("backend.session_security_v3")

OWNER = {"id": "12345678-1234-4234-8234-123456789abc",
         "anon_id": "anon-1", "memory_enabled": True,
         "consent_at": "2026-11-20T00:00:00+00:00",
         "created_at": "2026-11-20T00:00:00+00:00",
         "last_seen_at": "2026-11-20T00:00:00+00:00"}


class PrimitiveTest(unittest.TestCase):
    def test_owner_token_32_bytes_urlsafe_and_unique(self):
        a, b = security_v3.new_owner_token(), security_v3.new_owner_token()
        self.assertEqual(len(base64.urlsafe_b64decode(a + "=")), 32)
        self.assertNotEqual(a, b)
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_\-]+", a))

    def test_hash_token_sha256(self):
        self.assertEqual(security_v3.hash_token("tok"),
                         hashlib.sha256(b"tok").hexdigest())
        self.assertEqual(len(security_v3.hash_token("x")), 64)

    def test_recovery_code_20_chars_base32_alphabet(self):
        code = security_v3.new_recovery_code()
        self.assertEqual(len(code), 20)
        self.assertTrue(all(c in security_v3.RECOVERY_ALPHABET for c in code))
        self.assertNotEqual(code, security_v3.new_recovery_code())

    def test_recovery_normalization_and_verify(self):
        code = "ABCDE-FGHIJ-KLMNP-QRSTU"
        self.assertEqual(security_v3.normalize_recovery(code),
                         "ABCDEFGHIJKLMNPQRSTU")
        stored = security_v3.hash_recovery("abcde fghij klmnp qrstu")
        self.assertTrue(security_v3.verify_recovery(stored, "abcde-fghij-klmnp-qrstu"))
        self.assertFalse(security_v3.verify_recovery(stored, "WRONG0WRONG0WRONG00000"))
        self.assertFalse(security_v3.verify_recovery(None, code))
        self.assertFalse(security_v3.verify_recovery(stored, None))


class IssueRequireTest(unittest.TestCase):
    def test_issue_owner_returns_plaintext_once_stores_hashes(self):
        captured = {}
        def fake_enable(anon_id, key_hash, rec_hash):
            captured.update(anon_id=anon_id, key_hash=key_hash, rec_hash=rec_hash)
            return dict(OWNER)
        with patch.object(security_v3.memory_v3, "enable_owner", fake_enable):
            out = security_v3.issue_owner("anon-1")
        self.assertEqual(out["owner"], OWNER)
        self.assertEqual(captured["key_hash"],
                         security_v3.hash_token(out["owner_token"]))
        self.assertTrue(security_v3.verify_recovery(
            captured["rec_hash"], out["recovery_code"]))
        for secret in (out["owner_token"], out["recovery_code"]):
            self.assertNotIn(secret, captured.values())
        self.assertNotIn("owner_key_hash", out["owner"])
        self.assertNotIn("recovery_hash", out["owner"])

    def test_require_owner_happy_path_and_touch(self):
        token = security_v3.new_owner_token()
        touched = []
        with patch.object(security_v3.memory_v3, "get_owner_by_key_hash",
                          lambda h: dict(OWNER)), \
             patch.object(security_v3.memory_v3, "touch_owner",
                          lambda oid: touched.append(oid) or True):
            owner = security_v3.require_owner(token)
        self.assertEqual(owner["id"], OWNER["id"])
        self.assertEqual(touched, [OWNER["id"]])

    def test_require_owner_404_matrix(self):
        with patch.object(security_v3.memory_v3, "get_owner_by_key_hash",
                          lambda h: dict(OWNER)):
            for bad in (None, "", b"bytes-not-str", "x" * 300):
                with self.assertRaises(HTTPException) as ctx:
                    security_v3.require_owner(bad)
                self.assertEqual(ctx.exception.status_code, 404)
        # unknown token / disabled owner / deleted owner -> same 404
        for row in (None, dict(OWNER, memory_enabled=False)):
            with patch.object(security_v3.memory_v3, "get_owner_by_key_hash",
                              lambda h, row=row: row):
                with self.assertRaises(HTTPException) as ctx:
                    security_v3.require_owner(security_v3.new_owner_token())
                self.assertEqual(ctx.exception.status_code, 404)

    def test_require_owner_storage_error_is_503_not_404(self):
        def down(_hash):
            raise memory_v3.MemoryStoreError("down")
        with patch.object(security_v3.memory_v3, "get_owner_by_key_hash", down):
            with self.assertRaises(HTTPException) as ctx:
                security_v3.require_owner("any-token")
            self.assertEqual(ctx.exception.status_code, 503)


class RedeemTest(unittest.TestCase):
    def test_redeem_rotates_key_and_code_old_token_dies(self):
        old_hash = security_v3.hash_token("old-token")
        seen = {}
        def fake_rotate(owner_id, new_hash, new_rec):
            seen.update(owner_id=owner_id, new_hash=new_hash, new_rec=new_rec)
            return dict(OWNER)
        with patch.object(security_v3.memory_v3, "get_owner_by_recovery_hash",
                          lambda h: dict(OWNER)), \
             patch.object(security_v3.memory_v3, "rotate_owner_key", fake_rotate):
            out = security_v3.redeem_recovery("ABCDE-FGHIJ-KLMNP-QRSTU")
        self.assertNotEqual(security_v3.hash_token(out["owner_token"]), old_hash)
        self.assertEqual(seen["new_hash"],
                         security_v3.hash_token(out["owner_token"]))
        self.assertTrue(security_v3.verify_recovery(
            seen["new_rec"], out["recovery_code"]))
        self.assertEqual(len(out["recovery_code"]), 20)

    def test_redeem_unknown_code_404_no_oracle(self):
        with patch.object(security_v3.memory_v3, "get_owner_by_recovery_hash",
                          lambda h: None):
            with self.assertRaises(HTTPException) as ctx:
                security_v3.redeem_recovery("NOPE0NOPE0NOPE0NOPE0NO")
            self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
