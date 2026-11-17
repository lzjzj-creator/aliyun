"""Offline v2 coach engine + SSE route tests. LLM calls mocked; storage mocked."""
import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

with patch.dict(os.environ, {
    "SUPABASE_URL": "https://supabase.invalid",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "KB_SOURCE_BASE": "https://mirror.invalid",
    "COACH_ENABLED": "1",
}):
    from backend import coach, memory, session_security as security
    from backend.routers.coach import router
from tests.test_memory import Store, SID

ANON = "11111111-1111-4111-8111-111111111111"
OTHER_ANON = "22222222-2222-4222-8222-222222222222"


def _session(stage="probing"):
    return {"id": SID, "stage": stage, "title": "t"}


def _row(profile=None, detail=None, version=0):
    return {"profile": profile or {}, "coverage_detail": detail or {},
            "version": version, "coverage": 0}


def _events(gen):
    return [(e, d) for e, d in gen]


class L1Test(unittest.TestCase):
    def test_chips_skip_ack_done_stage(self):
        s = _session("probing")
        self.assertEqual(coach._l1(s, coach.CHIP_START, {}, {})["act"], "ask")
        self.assertEqual(coach._l1(s, coach.CHIP_ASK, {}, {})["act"], "answer")
        self.assertEqual(coach._l1(s, "跳过", {}, {})["act"], "ask")
        self.assertEqual(coach._l1(s, "你好", {}, {})["act"], "ack")
        self.assertEqual(coach._l1(s, "嗯嗯", {}, {})["l1"], "ack")
        self.assertEqual(coach._l1(_session("done"), "随便聊聊", {}, {})["act"], "answer")
        self.assertIsNone(coach._l1(s, "我大二的时候做过一个二手书小程序", {}, {}))

    def test_rule_decide(self):
        self.assertEqual(coach._rule_decide("实习怎么找？", {}, {})["act"], "answer")
        self.assertEqual(coach._rule_decide("说说我", {}, {})["act"], "ask")
        full = {"identity": {"grade": "大二", "school": "X", "major": "CS"},
                "goal": {"target_role": "后端"}, "experiences": [], "skills": [{"items": ["x"]}]}
        detail = {"filled": {"internships": {"value": "无", "denied": True},
                             "projects": {"value": "x"}}}
        plan = coach._rule_decide("嗯", full, detail)
        self.assertIn(plan["act"], ("ask", "wrap_up"))


class OrchestrateTest(unittest.TestCase):
    def test_invalid_json_act_falls_back_to_rules(self):
        coach._orch_failures.clear()
        with patch.object(coach.llm, "chat_json", return_value={"act": "nonsense"}):
            plan = coach._orchestrate("实习怎么找", {}, {}, [])
        self.assertEqual(plan["act"], "answer")
        self.assertTrue(plan.get("degraded"))
        self.assertEqual(len(coach._orch_failures), 1)

    def test_circuit_breaker_skips_model_after_repeated_failures(self):
        coach._orch_failures.clear()
        import time as _t
        coach._orch_failures.extend([_t.monotonic()] * 3)
        with patch.object(coach.llm, "chat_json") as spy:
            plan = coach._orchestrate("你好世界", {}, {}, [])
        spy.assert_not_called()
        self.assertTrue(plan.get("circuit"))
        coach._orch_failures.clear()

    def test_valid_plan_passthrough_and_focus_whitelist(self):
        coach._orch_failures.clear()
        good = {"act": "ask", "need_kb": False, "focus_slot": "projects",
                "reason": "缺项目", "deep": True}
        with patch.object(coach.llm, "chat_json", return_value=dict(good)):
            plan = coach._orchestrate("说说我做的项目", {}, {}, [])
        self.assertEqual(plan["focus_slot"], "projects")
        bad = {**good, "focus_slot": "invented_slot"}
        with patch.object(coach.llm, "chat_json", return_value=dict(bad)):
            plan = coach._orchestrate("说说我做的项目", {}, {}, [])
        self.assertIsNone(plan["focus_slot"])
        coach._orch_failures.clear()


class ExtractionTest(unittest.TestCase):
    MSGS = [{"role": "user", "content": "我大二在浙大读计算机，做过二手书小程序"}]
    SLOTS = {"identity.grade", "identity.school", "identity.major", "projects", "skills"}

    def test_quote_red_line_drops_fabricated(self):
        data = {"slot_updates": [
            {"slot": "identity.grade", "value": "大二", "quote": "我大二"},
            {"slot": "identity.major", "value": "医学", "quote": "我学的是医学"},  # 编造
        ]}
        p, d, u = coach.apply_extraction({}, {}, data, self.MSGS, self.SLOTS)
        self.assertEqual(p["identity"]["grade"], "大二")
        self.assertNotIn("major", p.get("identity", {}))
        self.assertIn("identity.grade", d["filled"])
        self.assertEqual(len(u), 1)

    def test_denials_and_experience_merge(self):
        msgs = [{"role": "user", "content": "我没做过任何项目，只做过二手书小程序"}]
        # 无 quote 的否认被丢弃（防虚构缺口抬覆盖率）
        d0 = coach.apply_extraction({}, {}, {"denials": ["projects"]},
                                  msgs, self.SLOTS)[1]
        self.assertNotIn("projects", d0["filled"])
        p, d, _ = coach.apply_extraction({}, {}, {
            "denials": [{"slot": "projects", "quote": "没做过任何项目"}]},
            msgs, self.SLOTS)
        self.assertTrue(d["filled"]["projects"]["denied"])
        # 后来补真实经历 → 推翻旧的"已确认无"
        p2, d2, _ = coach.apply_extraction(p, d, {
            "experiences": [{"type": "project", "title": "二手书小程序",
                             "actions": ["写后端"], "metrics": ["30%"],
                             "quote": "二手书小程序"}]}, msgs, self.SLOTS)
        self.assertFalse(d2["filled"]["projects"].get("denied"))
        self.assertEqual(p2["experiences"][0]["star_score"], 0.5)
        # 同题二次合并补字段，不重复追加
        p3, _, _ = coach.apply_extraction(p2, d2, {
            "experiences": [{"title": "二手书小程序", "results": ["用户翻倍"],
                             "quote": "二手书小程序"}]}, msgs, self.SLOTS)
        self.assertEqual(len(p3["experiences"]), 1)
        self.assertEqual(p3["experiences"][0]["star_score"], 0.75)


class CoverageTest(unittest.TestCase):
    def test_coverage_and_wrap(self):
        profile = {"identity": {"grade": "大二", "school": "浙大", "major": "CS"},
                   "goal": {"target_role": "后端"},
                   "experiences": [{"type": "project", "title": "t",
                                    "situation": "s", "actions": ["a"],
                                    "results": ["r"], "metrics": ["1"]}]}
        detail = {"filled": {"identity.grade": {}, "identity.school": {},
                             "identity.major": {}, "basics.target_role": {},
                             "internships": {"denied": True}, "projects": {},
                             "skills": {}}}
        cov, gaps, n, total = coach.coverage_of(profile, detail)
        self.assertEqual(gaps, [])
        self.assertEqual(cov, 1.0)
        self.assertTrue(coach.should_wrap(profile, detail, 5))
        self.assertTrue(coach.should_wrap(profile, {}, 12))
        self.assertFalse(coach.should_wrap({}, {}, 5))


class TurnEventsTest(unittest.TestCase):
    """引擎事件序列（LLM/检索全 mock）。"""

    def _llm(self):
        return patch.multiple(
            coach.llm,
            chat_json=lambda msgs, **kw: {"act": "ask", "focus_slot": "projects",
                                          "reason": "r"} if "编排" in str(msgs[0]) else {},
            chat_stream=lambda msgs, **kw: iter(["好的，", "说说你的项目？"]),
        )

    def test_event_order_and_terminal(self):
        with self._llm(), patch.object(coach.memory, "merge_profile",
                                       side_effect=lambda *a, **k: {
                                           "profile": {}, "coverage_detail": {},
                                           "version": 1}):
            evs = _events(coach.turn_events(_session(), _row(), [
                {"role": "user", "content": "我做过二手书小程序"}], "我做过二手书小程序"))
        kinds = [e for e, _ in evs]
        self.assertEqual(kinds[0], "step_start")
        self.assertIn("reasoning_summary", kinds)
        self.assertIn("token", kinds)
        self.assertIn("profile", kinds)
        # step_start 必有配对 step_end（同一 id）
        starts = [d["id"] for e, d in evs if e == "step_start"]
        ends = [d["id"] for e, d in evs if e == "step_end"]
        self.assertEqual(starts, ends)

    def test_chip_ask_is_canned_no_llm(self):
        with patch.object(coach.llm, "chat_stream") as spy, \
             patch.object(coach.memory, "merge_profile",
                          side_effect=lambda *a, **k: {"profile": {},
                                                     "coverage_detail": {},
                                                     "version": 1}), \
             patch.object(coach.llm, "chat_json", return_value={}):
            evs = _events(coach.turn_events(_session(), _row(), [
                {"role": "user", "content": "直接提问"}], "直接提问"))
        self.assertIn(("token", "好，直接问吧——规划、实习、简历写法都可以，我答完不打岔。"), evs)
        # chat_stream 不被调用（canned 回复）
        spy.assert_not_called()

    def test_retrieve_then_sources_when_question(self):
        hits = [{"title": "t", "source_url": "u", "seq": 1, "score": 0.9, "text": "x"}]
        with patch.object(coach.llm, "chat_json",
                          return_value={"act": "answer", "need_kb": True}), \
             patch.object(coach.llm, "chat_stream", return_value=iter(["答案。"])), \
             patch.object(coach.llm, "embed", return_value=[0.1]), \
             patch.object(coach.kb, "search", return_value=hits), \
             patch.object(coach.memory, "merge_profile",
                          side_effect=lambda *a, **k: {"profile": {},
                                                     "coverage_detail": {},
                                                     "version": 1}):
            evs = _events(coach.turn_events(_session(), _row(), [
                {"role": "user", "content": "实习怎么找"}], "实习怎么找"))
        kinds = [e for e, _ in evs]
        self.assertIn("retrieve", [d.get("kind") for e, d in evs if e == "step_start"])
        self.assertIn("sources", kinds)


def _parse_sse(body: str):
    out = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        ev = block.split("event: ", 1)[1].split("\n", 1)[0]
        data = block.split("\ndata: ", 1)[1]
        try:
            data = json.loads(data)
        except ValueError:
            pass
        out.append((ev, data))
    return out


class CoachRouteTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.transport = httpx.Client(
            base_url="https://supabase.invalid/rest/v1",
            transport=httpx.MockTransport(self.store))
        self._patchers = [patch.object(memory, "_client", self.transport),
                          patch.object(security, "limiter", security.RateLimiter())]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.transport.close)
        self.env = patch.dict(os.environ, {"COACH_ENABLED": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self._llm_patch = patch.multiple(
            coach.llm,
            chat_json=lambda msgs, **kw: {"act": "ask", "focus_slot": "projects"}
            if "编排" in str(msgs[0]) else {},
            chat_stream=lambda msgs, **kw: iter(["聊聊你的项目？"]),
        )
        self._llm_patch.start()
        self.addCleanup(self._llm_patch.stop)

    def _post(self, body, headers=None):
        h = {"X-Anon-Id": ANON}
        if headers:
            h.update(headers)
        return self.client.post("/api/v2/chat/stream", json=body, headers=h)

    def test_new_session_greeting_returns_token_once(self):
        res = self._post({"message": ""})
        self.assertEqual(res.status_code, 200)
        evs = _parse_sse(res.text)
        sess = dict(evs)["session"]
        self.assertTrue(sess["is_new"])
        self.assertIn("session_token", sess)
        kinds = [e for e, _ in evs]
        self.assertEqual(kinds[0], "session")
        self.assertIn("token", kinds)
        self.assertIn("card", kinds)
        self.assertEqual(kinds[-1], "done")

    def test_turn_flow_end_to_end_and_persisted(self):
        first = _parse_sse(self._post({"message": ""}).text)
        sid = dict(first)["session"]["session_id"]
        token = dict(first)["session"]["session_token"]
        res = self._post({"session_id": sid, "client_msg_id": "c1",
                          "message": "我做过二手书小程序"},
                         {"X-Session-Token": token})
        self.assertEqual(res.status_code, 200)
        evs = _parse_sse(res.text)
        kinds = [e for e, _ in evs]
        for required in ("session", "step_start", "step_end",
                         "reasoning_summary", "token", "profile", "done"):
            self.assertIn(required, kinds)
        self.assertEqual(kinds[-1], "done")
        done = dict(evs)["done"]
        self.assertEqual(done["session_id"], sid)
        self.assertIn(done["stage"], ("probing", "wrapping", "done"))
        # 持久化：user+assistant 落库，assistant 带 steps
        users = [m for m in self.store.messages
                 if m["role"] == "user" and m["session_id"] == sid]
        asst = [m for m in self.store.messages
                if m["role"] == "assistant" and m["session_id"] == sid]
        self.assertEqual(len(users), 1)
        self.assertTrue(any(m["status"] == "done" and m["steps"] for m in asst))

    def test_duplicate_client_msg_id_returns_error_not_regen(self):
        first = _parse_sse(self._post({"message": ""}).text)
        sess = dict(first)["session"]
        headers = {"X-Session-Token": sess["session_token"]}
        body = {"session_id": sess["session_id"], "client_msg_id": "dup",
                "message": "hi there"}
        self._post(body, headers)
        res = self._post(body, headers)
        evs = _parse_sse(res.text)
        self.assertIn("error", [e for e, _ in evs])
        users = [m for m in self.store.messages if m["role"] == "user"]
        self.assertEqual(len(users), 1)

    def test_concurrent_stream_lease_returns_429(self):
        # 租约在响应前获取：竞争 → 标准 429（而非断头 SSE 流）
        first = _parse_sse(self._post({"message": ""}).text)
        sess = dict(first)["session"]
        with security.limiter.stream(sess["session_id"]):
            res = self._post({"session_id": sess["session_id"], "client_msg_id": "c2",
                              "message": "hi"}, {"X-Session-Token": sess["session_token"]})
        self.assertEqual(res.status_code, 429)

    def test_auth_failures_are_uniform_404(self):
        for headers in [{}, {"X-Anon-Id": ANON},
                        {"X-Anon-Id": OTHER_ANON, "X-Session-Token": "x"},
                        {"X-Anon-Id": "not-uuid"}]:
            res = self.client.post("/api/v2/chat/stream", headers=headers,
                                   json={"session_id": SID, "client_msg_id": "c",
                                         "message": "hi"})
            self.assertEqual(res.status_code, 404)

    def test_disabled_flag_is_503(self):
        with patch.dict(os.environ, {"COACH_ENABLED": "0"}):
            res = self._post({"message": ""})
        self.assertEqual(res.status_code, 503)

    def test_message_size_limit(self):
        res = self._post({"message": "x" * 2001})
        self.assertEqual(res.status_code, 422)


if __name__ == "__main__":
    unittest.main()
