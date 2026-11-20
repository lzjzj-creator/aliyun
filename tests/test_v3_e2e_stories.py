"""#72 三 fixture 故事端到端：真组件链跑通——

  A 跨会话召回：两会话抽取的事实进同一份简历的证据链
  B 一库多稿：同一 owner 两份不同岗位简历并存且证据独立
  C 删除无残留：忘掉会话后全库无该会话原话；清空后全表为空

LLM 用 chat_json stub、存储用 fake transport——全链路真实代码，零外呼。
"""
import copy
import importlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

memory = importlib.import_module("backend.memory")
memory_v3 = importlib.import_module("backend.memory_v3")
security_v3 = importlib.import_module("backend.session_security_v3")
fact_extractor = importlib.import_module("backend.fact_extractor")
resume_builder_v3 = importlib.import_module("backend.resume_builder_v3")

from tests.test_memory_v3 import Store  # noqa: E402

SID_A = "87654321-4321-4321-8321-cba987654321"
SID_B = "87654321-4321-4321-8321-cba987654322"
ANON = "anon-e2e"


def _v2_session(store, sid, messages):
    store.rows.setdefault("chat_sessions", []).append({
        "id": sid, "anon_id": ANON, "title": "会话" + sid[-2:],
        "stage": "done", "created_at": "t", "updated_at": "t",
        "last_message_at": "t"})
    for i, content in enumerate(messages, start=1):
        store.rows.setdefault("chat_messages", []).append({
            "id": i, "session_id": sid, "role": "user",
            "content": content, "created_at": "t"})


def _model_reply(kind, slot, value, msg_id, quote):
    return {"facts": [{"kind": kind, "slot_path": slot, "value": value,
                       "msg_id": msg_id, "quote": quote,
                       "confidence": 0.9}],
            "conflicts": [], "denied": []}


class E2E(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        import httpx
        self.client = httpx.Client(
            base_url="https://supabase.invalid/rest/v1",
            headers={"apikey": "k", "Authorization": "Bearer k"},
            transport=httpx.MockTransport(self.store))
        for p in (patch.object(memory, "_client", self.client),):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.client.close)
        self.bundle = security_v3.issue_owner(ANON)
        self.oid = self.bundle["owner"]["id"]

    def _facts(self):
        return self.store.rows["v3_facts"]

    # -- Story A: 跨会话召回 --------------------------------------------------
    def test_a_cross_session_recall(self):
        _v2_session(self.store, SID_A, [
            "我去年在阿里做后端实习，主要用 Java 写优惠券系统"])
        _v2_session(self.store, SID_B, [
            "英语六级 580 分，还拿过校程序设计竞赛一等奖"])

        replies = iter([
            _model_reply("skill", "skills.groups.0.items.0", "Java", 1,
                         "主要用 Java 写优惠券系统"),
            _model_reply("award", "awards.items.0.text",
                         "校程序设计竞赛一等奖", 1,
                         "还拿过校程序设计竞赛一等奖"),
        ])
        with patch.object(fact_extractor.llm, "chat_json",
                          lambda *a, **k: next(replies)):
            ra = fact_extractor.extract_incremental(self.oid, SID_A)
            rb = fact_extractor.extract_incremental(self.oid, SID_B)
        self.assertEqual((ra["added"], rb["added"]), (1, 1))
        # 同一 owner 的事实库汇聚了来自两个会话的证据
        sessions = {e["session_id"] for f in self._facts()
                    for e in f["evidence"]}
        self.assertEqual(sessions, {SID_A, SID_B})

        # 生成简历：LLM stub 逐字引用两条事实的值，证据链跨两个会话
        def build_reply(*a, **k):
            facts = memory_v3.facts_for_generation(self.oid)
            by_value = {f["value"]: f["id"] for f in facts}
            return {"fields": {
                "basics": {"name": "张明", "target_role": "后端开发"},
                "skills": {"groups": [{"name": "后端", "items": ["Java"]}]},
                "awards": {"items": [
                    {"text": "校程序设计竞赛一等奖", "period": "2025"}]},
            }, "evidence_map": {
                "skills.groups.0.items.0": [by_value["Java"]],
                "awards.items.0.text": [by_value["校程序设计竞赛一等奖"]],
            }, "gaps": []}
        with patch.object(resume_builder_v3.llm, "chat_json", build_reply):
            doc = resume_builder_v3.create_document(
                self.oid, title="后端简历", target_role="后端开发")
        ev_sessions = {e["session_id"]
                       for evs in doc["evidence_map"].values() for e in evs}
        self.assertEqual(ev_sessions, {SID_A, SID_B})
        self.assertEqual(len(doc["fact_snapshot"]), 2)

        # -- Story C 一半：忘掉会话 B，简历与事实里都不留 B 的原话 -------------
        quote_b = "还拿过校程序设计竞赛一等奖"
        res = memory_v3.forget_session(self.oid, SID_B)
        self.assertEqual(res["facts_detached"], 1)
        # 残留口径：v3 事实/文档层无 B 的原话与来源指针；
        # v2 聊天本体不归「忘掉」管，extraction_state 水位保留防再抽取。
        v3_blob = json.dumps(
            {k: v for k, v in self.store.rows.items()
             if k in ("v3_facts", "v3_resume_documents",
                      "v3_document_versions")},
            ensure_ascii=False)
        self.assertNotIn(quote_b, v3_blob)
        self.assertNotIn(SID_B, v3_blob)
        self.assertIn("Java", v3_blob)       # 会话 A 的事实保留

    # -- Story B: 一库多稿 ----------------------------------------------------
    def test_b_one_owner_many_role_resumes(self):
        _v2_session(self.store, SID_A, [
            "我去年在阿里做后端实习，主要用 Java 写优惠券系统"])
        with patch.object(fact_extractor.llm, "chat_json",
                          lambda *a, **k: _model_reply(
                              "skill", "skills.groups.0.items.0", "Java",
                              1, "主要用 Java 写优惠券系统")):
            fact_extractor.extract_incremental(self.oid, SID_A)

        def reply_for(role):
            def fn(*a, **k):
                fid = self._facts()[0]["id"]
                return {"fields": {
                    "basics": {"name": "张明", "target_role": role},
                    "skills": {"groups": [
                        {"name": "后端", "items": ["Java"]}]},
                }, "evidence_map": {"skills.groups.0.items.0": [fid]},
                    "gaps": []}
            return fn
        with patch.object(resume_builder_v3.llm, "chat_json",
                          reply_for("后端开发")):
            d1 = resume_builder_v3.create_document(
                self.oid, title="后端稿", target_role="后端开发")
        with patch.object(resume_builder_v3.llm, "chat_json",
                          reply_for("算法实习")):
            d2 = resume_builder_v3.create_document(
                self.oid, title="算法稿", target_role="算法实习")
        docs = memory_v3.list_documents(self.oid)
        self.assertEqual({d["title"] for d in docs}, {"后端稿", "算法稿"})
        self.assertNotEqual(d1["id"], d2["id"])
        # 两稿各自持有同一事实的快照（共享事实库、独立文档）
        self.assertEqual(d1["fact_snapshot"], d2["fact_snapshot"])

    # -- Story C: 删除无残留 ----------------------------------------------------
    def test_c_delete_leaves_no_residue(self):
        _v2_session(self.store, SID_A, [
            "我去年在阿里做后端实习，主要用 Java 写优惠券系统"])
        with patch.object(fact_extractor.llm, "chat_json",
                          lambda *a, **k: _model_reply(
                              "skill", "skills.groups.0.items.0", "Java",
                              1, "主要用 Java 写优惠券系统")):
            fact_extractor.extract_incremental(self.oid, SID_A)
        fid = self._facts()[0]["id"]

        # 单条删除 → 引用它的文档标 stale
        def build_reply(*a, **k):
            return {"fields": {"skills": {"groups": [
                        {"name": "后端", "items": ["Java"]}]}},
                    "evidence_map": {"skills.groups.0.items.0": [fid]},
                    "gaps": []}
        with patch.object(resume_builder_v3.llm, "chat_json", build_reply):
            doc = resume_builder_v3.create_document(self.oid, title="x")
        memory_v3.soft_delete_fact(self.oid, fid)
        stale = memory_v3.stale_sources(
            doc, memory_v3.facts_for_generation(self.oid))
        self.assertEqual(stale[0]["change"], "deleted")

        # 清空全部 → facts/state/documents/owner/versions 全空
        memory_v3.delete_owner(self.oid)
        for t in ("v3_facts", "v3_extraction_state", "v3_resume_documents",
                  "v3_owner", "v3_document_versions"):
            self.assertEqual(self.store.rows.get(t, []), [], t)
        v3_blob = json.dumps(
            {k: v for k, v in self.store.rows.items()
             if k.startswith("v3_")}, ensure_ascii=False)
        self.assertNotIn("Java", v3_blob)   # v2 聊天本体不在清空范围


if __name__ == "__main__":
    unittest.main()
