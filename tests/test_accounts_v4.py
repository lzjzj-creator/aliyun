"""v4 账号体系离线契约测试：guest/注册升级/登录/init/草稿/删除账号。

全部 I/O 走 httpx.MockTransport 的 REST 模型；不触网、不读真实凭据。
路由层用 FastAPI TestClient，存储层 patch memory._client（accounts_v4 复用）。
"""
import copy
import importlib
import json
import os
import re
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

with patch.dict(os.environ, {
    "SUPABASE_URL": "https://supabase.invalid",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "KB_SOURCE_BASE": "https://mirror.invalid",
}):
    memory = importlib.import_module("backend.memory")
    accounts_v4 = importlib.import_module("backend.accounts_v4")
    session_security = importlib.import_module("backend.session_security")
    security_v4 = importlib.import_module("backend.session_security_v4")
    accounts_router = importlib.import_module("backend.routers.accounts_v4")

TABLES = ("app_users", "app_user_tokens", "resume_drafts",
          "chat_sessions", "v3_owner")


class Store:
    """app_users 域 REST 模型（只实现路由用到的过滤/投影）。"""
    def __init__(self):
        self.rows = {t: [] for t in TABLES}
        self.requests = []
        self.lock = threading.Lock()

    @staticmethod
    def filtered(rows, params):
        for field, value in params.items():
            if field in ("select", "order", "limit", "offset", "or"):
                continue
            if value.startswith("eq."):
                rows = [r for r in rows if str(r.get(field)) == value[3:]]
            elif value == "is.null":
                rows = [r for r in rows if r.get(field) is None]
            elif value == "not.is.null":
                rows = [r for r in rows if r.get(field) is not None]
            elif value.startswith("gt."):
                rows = [r for r in rows
                        if str(r.get(field) or "") > value[3:]]
            elif value.startswith("in.("):
                allowed = set(value[4:-1].split(","))
                rows = [r for r in rows if str(r.get(field)) in allowed]
        return rows

    @staticmethod
    def sorted(rows, order):
        for key in reversed((order or "").split(",")):
            field, _, direction = key.partition(".")
            rows = sorted(
                rows,
                key=lambda r: (r.get(field) is None, str(r.get(field, ""))),
                reverse=(direction == "desc"))
        return rows

    @staticmethod
    def projected(rows, select):
        if not select or select == "*":
            return rows
        fields = set(select.split(","))
        return [{k: v for k, v in r.items() if k in fields} for r in rows]

    def __call__(self, request):
        with self.lock:
            return self.handle(request)

    def handle(self, request):
        self.requests.append(request)
        table = request.url.path.rsplit("/", 1)[-1]
        params = dict(request.url.params)
        data = json.loads(request.content) if request.content else {}
        rows = self.filtered(list(self.rows.get(table, [])), params)
        if request.method == "GET":
            rows = self.sorted(rows, params.get("order"))
            limit = int(params.get("limit", 1000))
            return httpx.Response(200, json=copy.deepcopy(
                self.projected(rows[:limit], params.get("select"))))
        if request.method == "POST":
            row = {"id": str(uuid4()), **copy.deepcopy(data)}
            if table == "app_users" and "kind" not in row:
                row["kind"] = "guest"
            if table == "app_user_tokens":
                row.setdefault("expires_at", "9999-01-01T00:00:00+00:00")
                row.setdefault("revoked_at", None)
            self.rows.setdefault(table, []).append(row)
            return httpx.Response(201, json=copy.deepcopy(
                self.projected([row], params.get("select"))))
        if request.method == "PATCH":
            for r in rows:
                r.update(copy.deepcopy(data))
            return httpx.Response(200, json=copy.deepcopy(
                self.projected(rows, params.get("select"))))
        if request.method == "DELETE":
            kept = [r for r in self.rows.get(table, []) if r not in rows]
            self.rows[table] = kept
            return httpx.Response(200, json=copy.deepcopy(
                self.projected(rows, params.get("select"))))
        raise AssertionError("Unexpected request")


class V4AppTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.supa = httpx.Client(
            base_url="https://supabase.invalid/rest/v1",
            headers={"apikey": "test-service-key",
                     "Authorization": "Bearer test-service-key"},
            transport=httpx.MockTransport(self.store))
        self.client_patch = patch.object(memory, "_client", self.supa)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.addCleanup(self.supa.close)
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(accounts_router.router)
        self.http = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.http.close)
        accounts_router._login_hits.clear()
        # 同 IP 高频调用会撞 IP 限流；实例级放行（不改进程级配额常量）
        limit_patch = patch.object(session_security.limiter, "check_ip",
                                   return_value=(True, 0))
        limit_patch.start()
        self.addCleanup(limit_patch.stop)

    # -- helpers ---------------------------------------------------------------
    def guest(self):
        r = self.http.post("/api/v4/auth/guest")
        assert r.status_code == 200, r.text
        return r.json()

    def bearer(self, token):
        return {"Authorization": f"Bearer {token}"}

    # -- G1：guest → 注册升级 → 登录 → 跨设备数据可达 ---------------------------
    def test_guest_then_register_then_login(self):
        g = self.guest()
        assert g["user"]["kind"] == "guest"
        guest_id = g["user"]["id"]
        token = g["auth_token"]

        me = self.http.get("/api/v4/me", headers=self.bearer(token))
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["id"], guest_id)
        self.assertEqual(me.json()["kind"], "guest")

        r = self.http.post("/api/v4/auth/register",
                           headers=self.bearer(token),
                           json={"username": "小林", "password": "secret-123"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["user"]["id"], guest_id)     # 同一行升级
        self.assertEqual(r.json()["user"]["kind"], "user")
        self.assertTrue(r.json()["user"]["needs_init"])

        # 旧 token 仍有效（升级不吊销）
        me2 = self.http.get("/api/v4/me", headers=self.bearer(token))
        self.assertEqual(me2.status_code, 200)
        self.assertEqual(me2.json()["username"], "小林")

        # 新设备：登录拿到新 token，同一账号数据
        lr = self.http.post("/api/v4/auth/login",
                            json={"username": "小林", "password": "secret-123"})
        self.assertEqual(lr.status_code, 200)
        t2 = lr.json()["auth_token"]
        self.assertNotEqual(t2, token)
        me3 = self.http.get("/api/v4/me", headers=self.bearer(t2))
        self.assertEqual(me3.json()["id"], guest_id)

    def test_register_conflict_and_login_failures(self):
        self.guest()
        self.http.post("/api/v4/auth/register",
                       json={"username": "dupname", "password": "secret-123"})
        r = self.http.post("/api/v4/auth/register",
                           json={"username": "DupName", "password": "secret-456"})
        self.assertEqual(r.status_code, 409)                  # 大小写归一冲突
        bad = self.http.post("/api/v4/auth/login",
                             json={"username": "dupname", "password": "wrong"})
        self.assertEqual(bad.status_code, 401)
        nouser = self.http.post("/api/v4/auth/login",
                                json={"username": "ghost", "password": "x" * 9})
        self.assertEqual(nouser.status_code, 401)
        self.assertEqual(bad.json(), nouser.json())           # 无用户名枚举

    def test_logout_revokes_and_missing_token_404(self):
        g = self.guest()
        token = g["auth_token"]
        self.assertEqual(self.http.post(
            "/api/v4/auth/logout", headers=self.bearer(token)).status_code, 200)
        r = self.http.get("/api/v4/me", headers=self.bearer(token))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.http.get("/api/v4/me").status_code, 404)
        self.assertEqual(
            self.http.get("/api/v4/me",
                          headers=self.bearer("not-a-token")).status_code, 404)

    # -- G2：init 校验 + 画像字段 -------------------------------------------------
    def _registered(self):
        g = self.guest()
        token = g["auth_token"]
        self.http.post("/api/v4/auth/register", headers=self.bearer(token),
                       json={"username": "u" + uuid4().hex[:8],
                             "password": "secret-123"})
        return token

    VALID_INIT = {
        "identity": {"grade": "大三"},
        "basics": {"name": "林晓", "phone": "13800000000",
                   "email": "lin@example.com", "target_role": "后端实习生"},
        "education": [{"school": "浙江大学", "major": "计算机科学",
                       "degree": "本科", "period": "2023.09-2027.06"}],
    }

    def test_init_validation_and_save(self):
        token = self._registered()
        bad = self.http.put("/api/v4/me/init", headers=self.bearer(token),
                            json={"init_profile": {"identity": {"grade": "大三"}}})
        self.assertEqual(bad.status_code, 422)
        issues = bad.json()["detail"]["issues"]
        paths = {i["path"] for i in issues}
        for p in ("basics.name", "basics.email", "education.0.school",
                  "education.0.major"):
            self.assertIn(p, paths)
        self.assertNotIn("identity.grade", paths)             # 已填不误报

        ok = self.http.put("/api/v4/me/init", headers=self.bearer(token),
                           json={"init_profile": self.VALID_INIT})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertTrue(ok.json()["init_done"])
        me = self.http.get("/api/v4/me", headers=self.bearer(token))
        self.assertTrue(me.json()["init_done"])
        self.assertEqual(
            me.json()["init_profile"]["education"][0]["school"], "浙江大学")

        bad_email = dict(self.VALID_INIT)
        bad_email["basics"] = dict(self.VALID_INIT["basics"], email="not-mail")
        r = self.http.put("/api/v4/me/init", headers=self.bearer(token),
                          json={"init_profile": bad_email})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"]["issues"][0]["code"], "format")

    def test_settings_and_guide_seen(self):
        token = self._registered()
        r = self.http.put("/api/v4/me/settings", headers=self.bearer(token),
                          json={"settings": {"sidebar": True}})
        self.assertEqual(r.status_code, 200)
        me = self.http.get("/api/v4/me", headers=self.bearer(token))
        self.assertEqual(me.json()["settings"]["sidebar"], True)
        self.assertFalse(me.json()["guide_seen"])
        self.http.post("/api/v4/me/guide-seen", headers=self.bearer(token))
        me2 = self.http.get("/api/v4/me", headers=self.bearer(token))
        self.assertTrue(me2.json()["guide_seen"])

    # -- 草稿 CRUD（含 slug 键） ---------------------------------------------------
    def test_draft_crud_with_slug(self):
        token = self._registered()
        body = {"draft_id": "current", "template_version": "v1",
                "title": "简历草稿", "fields": {"basics": {"name": "林"}},
                "photo": {"data_url": "data:image/png;base64,x"}}
        put = self.http.put("/api/v4/drafts", headers=self.bearer(token),
                            json=body)
        self.assertEqual(put.status_code, 200, put.text)
        self.assertEqual(put.json()["draft"]["slug"], "current")

        get = self.http.get("/api/v4/drafts/current",
                            headers=self.bearer(token))
        self.assertEqual(get.status_code, 200)
        self.assertEqual(get.json()["draft"]["photo"]["data_url"],
                         "data:image/png;base64,x")

        # slug upsert 幂等：同键再写不产生第二行
        body["fields"] = {"basics": {"name": "改"}}
        self.http.put("/api/v4/drafts", headers=self.bearer(token), json=body)
        get2 = self.http.get("/api/v4/drafts/current",
                             headers=self.bearer(token))
        self.assertEqual(get2.json()["draft"]["fields"]["basics"]["name"], "改")
        listing = self.http.get("/api/v4/drafts", headers=self.bearer(token))
        self.assertEqual(len([d for d in listing.json()["drafts"]
                              if d["slug"] == "current"]), 1)

        # 越权：另一个账号看不见
        other = self._registered()
        r = self.http.get("/api/v4/drafts/current",
                          headers=self.bearer(other))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.http.delete(
            "/api/v4/drafts/current",
            headers=self.bearer(other)).status_code, 404)

        self.assertEqual(self.http.delete(
            "/api/v4/drafts/current",
            headers=self.bearer(token)).status_code, 200)
        self.assertEqual(self.http.get(
            "/api/v4/drafts/current",
            headers=self.bearer(token)).status_code, 404)

    # -- G4：删除账号硬删 + 数据无残留（REST 层能观察到的部分） --------------------
    def test_delete_account_removes_row_and_tokens(self):
        token = self._registered()
        self.http.put("/api/v4/drafts", headers=self.bearer(token),
                      json={"draft_id": "current", "fields": {"a": 1}})
        me = self.http.get("/api/v4/me", headers=self.bearer(token))
        uid = me.json()["id"]

        # 确认串不对 → 422
        self.assertEqual(self.http.request(
            "DELETE", "/api/v4/me", headers=self.bearer(token),
            json={"confirm": "yes"}).status_code, 422)
        r = self.http.request("DELETE", "/api/v4/me",
                              headers=self.bearer(token),
                              json={"confirm": "DELETE_ACCOUNT"})
        self.assertEqual(r.status_code, 200, r.text)

        # 用户行没了；token 解析不到 → 404
        users = [u for u in self.store.rows["app_users"] if u["id"] == uid]
        self.assertEqual(users, [])
        self.assertEqual(self.http.get(
            "/api/v4/me", headers=self.bearer(token)).status_code, 404)
        # SQL 层 cascade 约束静态核对（FK on delete cascade）
        from pathlib import Path
        sql = Path("supabase/migrations/20261124_v5_accounts.sql") \
            .read_text(encoding="utf-8")
        self.assertIn("on delete cascade", sql.lower())

    # -- 安全：响应不泄漏密码哈希/token 明文 -------------------------------------
    def test_no_secret_leak_in_responses(self):
        g = self.guest()
        token = g["auth_token"]
        r = self.http.post("/api/v4/auth/register",
                           headers=self.bearer(token),
                           json={"username": "leakcheck", "password": "secret-123"})
        blob = json.dumps(r.json(), ensure_ascii=False)
        self.assertNotIn("password_hash", blob)
        self.assertNotIn("scrypt$", blob)
        me = self.http.get("/api/v4/me", headers=self.bearer(token))
        blob = json.dumps(me.json(), ensure_ascii=False)
        self.assertNotIn("password_hash", blob)
        self.assertNotIn(token, blob)                        # token 不回显
        login = self.http.post("/api/v4/auth/login",
                               json={"username": "leakcheck",
                                     "password": "secret-123"})
        blob = json.dumps(login.json(), ensure_ascii=False)
        self.assertNotIn("password_hash", blob)
        self.assertNotIn("scrypt$", blob)

    # -- scrypt 单元层 -------------------------------------------------------------
    def test_scrypt_hash_and_verify(self):
        h = security_v4.hash_password("pässw0rd-九")
        self.assertTrue(h.startswith("scrypt$16384$8$1$"))
        self.assertTrue(security_v4.verify_password("pässw0rd-九", h))
        self.assertFalse(security_v4.verify_password("wrong", h))
        self.assertFalse(security_v4.verify_password("x", None))
        self.assertFalse(security_v4.verify_password("x", "garbage"))


if __name__ == "__main__":
    unittest.main()
