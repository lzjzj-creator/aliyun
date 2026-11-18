"""Extraction trust-boundary regressions; all models and persistence are offline mocks."""
import copy
import os
import unittest
from unittest.mock import patch

with patch.dict(os.environ, {
    "SUPABASE_URL": "https://supabase.invalid",
    "SUPABASE_SERVICE_KEY": "test-service-key",
}):
    from backend import coach


class ExtractionReviewTest(unittest.TestCase):
    SLOTS = set(coach.SCALAR_SLOT_PATH) | {"skills", "awards", "projects", "internships"}

    def apply(self, data, text="I use Python", profile=None, detail=None, history=None):
        messages = (history or []) + [{"role": "user", "content": text}]
        return coach.apply_extraction(profile or {}, detail or {}, data, messages, self.SLOTS)

    def test_bad_types_unknown_keys_and_bounds_are_atomic(self):
        good = {"slot": "identity.school", "value": "Python", "quote": "Python"}
        bad = [[], None, 1, "text", {"extra": []},
               {"slot_updates": [dict(good, value=123)]},
               {"slot_updates": [dict(good, quote={})]},
               {"skills": [{"group": {"evil": []}, "items": ["Python"]}]},
               {"skills": [{"group": ["evil"], "items": ["Python"]}]},
               {"skills": [{"group": "bad\nname", "items": ["Python"]}]},
               {"skills": [{"items": [42]}]},
               {"skills": [{"items": "Python"}]},
               {"experiences": [{"title": "Python", "quote": "Python", "type": "evil"}]},
               {"experiences": [{"title": "Python", "quote": "Python", "actions": [False]}]},
               {"awards": [{"text": "Python", "period": 2024}]},
               {"slot_updates": [dict(good, extra="bad")]},
               {"slot_updates": [good] * 33},
               {"slot_updates": [dict(good, value="x" * 2001)]}]
        for data in bad:
            with self.subTest(data=data):
                if isinstance(data, dict):
                    data = {"slot_updates": [good], **data}
                p, d, updates = self.apply(data)
                self.assertEqual(p, {})
                self.assertEqual(d, {"filled": {}})
                self.assertEqual(updates, [])

    def test_unrelated_quote_cannot_back_scalar_or_experience_facts(self):
        p, _, _ = self.apply({
            "slot_updates": [
                {"slot": "identity.school", "value": "Harvard", "quote": "I"},
                {"slot": "summary", "value": "100000USD", "quote": "I"}],
            "experiences": [{"title": "Python", "type": "project", "quote": "I",
                             "role": "CEO", "period": "2024", "situation": "Harvard",
                             "actions": ["use Python", "raise capital"],
                             "results": ["100000USD"], "metrics": ["30%"]}]})
        self.assertNotIn("identity", p)
        self.assertNotIn("summary_material", p)
        exp = p["experiences"][0]
        self.assertEqual(exp["actions"], ["use Python"])
        self.assertEqual(exp["metrics"], [])
        self.assertEqual(exp["results"], [])
        for key in ("role", "period", "situation"):
            self.assertNotIn(key, exp)
        self.assertNotIn("experiences", self.apply({"experiences": [
            {"title": "Harvard", "quote": "I", "type": "project"}]})[0])

    def test_history_and_assistant_are_not_current_evidence(self):
        history = [{"role": "user", "content": "Harvard Python 2024 Gold"},
                   {"role": "assistant", "content": "Harvard Python 2024 Gold"}]
        data = {"slot_updates": [{"slot": "identity.school", "value": "Harvard", "quote": "Harvard"}],
                "skills": [{"group": "语言", "items": ["Python"]}],
                "awards": [{"text": "Gold", "period": "2024"}],
                "experiences": [{"title": "Harvard", "type": "project", "quote": "Harvard"}]}
        self.assertEqual(self.apply(data, text="现在聊别的", history=history)[0], {})
        with patch.object(coach.llm, "chat_json", return_value=data):
            p, _, updates, _ = coach._extract("现在聊别的", {}, {}, history)
        self.assertEqual((p, updates), ({}, []))

    def test_award_period_requires_current_support(self):
        self.assertNotIn("awards", self.apply({"awards": [
            {"text": "Gold", "period": "2024"}]}, text="Gold")[0])
        p, _, _ = self.apply({"awards": [{"text": "Gold", "period": "2024"}]},
                             text="2024 Gold")
        self.assertEqual(p["awards"], [{"text": "Gold", "period": "2024"}])

    def test_filled_all_branches_and_confirmed_experiences_are_protected(self):
        profile = {"identity": {"school": "Old"}, "skills": [{"group": "语言", "items": ["Old"]}],
                   "awards": [{"text": "Old", "period": "2000"}],
                   "experiences": [{"title": "Python", "type": "project", "role": "Old"}]}
        detail = {"filled": {s: {"value": "Old", "quote": "Old"} for s in
                             ("identity.school", "skills", "awards", "projects")}}
        data = {"slot_updates": [{"slot": "identity.school", "value": "Python", "quote": "Python"}],
                "skills": [{"group": "语言", "items": ["Python"]}],
                "awards": [{"text": "Python"}],
                "experiences": [{"title": "Python", "quote": "Python", "role": "Python",
                                 "actions": ["Python"]}]}
        p, d, updates = self.apply(data, profile=profile, detail=detail)
        self.assertEqual((p, d, updates), (profile, detail, []))
        self.assertEqual(profile["skills"][0]["items"], ["Old"])
        confirmed = copy.deepcopy(profile)
        confirmed["experiences"][0]["confirmed"] = True
        p, _, _ = self.apply({"experiences": data["experiences"]}, profile=confirmed)
        self.assertEqual(p["experiences"], confirmed["experiences"])

    def test_legacy_bad_profile_brief_is_tolerant_not_migrated(self):
        for profile in [None, [], {"identity": 42, "goal": [], "summary_material": 1,
                                 "experiences": [{"title": 123}, None],
                                 "skills": [{"group": {}}, 42]}]:
            before = copy.deepcopy(profile)
            self.assertIsInstance(coach._profile_brief(profile), str)
            self.assertEqual(profile, before)
        self.assertEqual(self.apply({}, profile={"summary_material": 123})[0],
                         {"summary_material": 123})

    def test_positive_extractive_fields_and_existing_values(self):
        text = "2024 Python 项目，担任开发，在校开发，写后端，用户翻倍，30%"
        data = {"slot_updates": [{"slot": "summary", "value": "写后端", "quote": "写后端"}],
                "experiences": [{"type": "project", "title": "Python 项目",
                                 "quote": text, "role": "开发", "period": "2024",
                                 "situation": "在校开发", "actions": ["写后端"],
                                 "results": ["用户翻倍"], "metrics": ["30%"]}]}
        p, _, _ = self.apply(data, text=text)
        self.assertEqual(p["summary_material"], "写后端")
        exp = p["experiences"][0]
        self.assertEqual(exp["star_score"], 1.0)
        self.assertEqual(exp["role"], "开发")
        self.assertEqual(exp["period"], "2024")
        old = {"experiences": [{"title": "Python 项目", "type": "project",
                                "role": "Old role", "period": "Old period",
                                "situation": "Old situation"}]}
        before = copy.deepcopy(old)
        merged, _, _ = self.apply({"experiences": data["experiences"]}, text=text, profile=old)
        self.assertEqual(old, before)  # Pure function must not mutate callers.
        for key in ("role", "period", "situation"):
            self.assertEqual(merged["experiences"][0][key], old["experiences"][0][key])
        self.assertEqual(merged["experiences"][0]["metrics"], ["30%"])

    def test_model_failure_degrades_without_changes(self):
        profile = {"identity": {"school": "Old"}}
        detail = {"filled": {"identity.school": {"quote": "Old"}}}
        with patch.object(coach.llm, "chat_json", side_effect=ValueError("bad json")):
            self.assertEqual(coach._extract("Python", profile, detail, []),
                             (profile, detail, [], {}))

    def test_mock_model_persist_reload_next_turn(self):
        bad_outputs = [[], {"skills": [{"group": {}, "items": ["Python"]}]},
                       {"slot_updates": [{"slot": "summary", "value": 123, "quote": "I"}]}]
        good = {"skills": [{"group": "语言", "items": ["Python"]}]}
        for output in bad_outputs + [good]:
            with self.subTest(output=output):
                stored = {"profile": {}, "coverage_detail": {}, "version": 0}

                def persist(sid, profile, version, **kwargs):
                    stored.update(profile=copy.deepcopy(profile),
                                  coverage_detail=copy.deepcopy(kwargs["coverage_detail"]),
                                  version=version + 1)
                    return copy.deepcopy(stored)

                plan = {"act": "ask", "focus_slot": "skills", "need_kb": False, "deep": False}
                with patch.object(coach, "_l1", return_value=plan), \
                     patch.object(coach, "should_wrap", return_value=False), \
                     patch.object(coach.llm, "chat_stream", side_effect=lambda *a, **k: iter(["ok"])), \
                     patch.object(coach.llm, "chat_json", side_effect=[output, {}]), \
                     patch.object(coach.memory, "merge_profile", side_effect=persist):
                    session = {"id": "mock-session", "stage": "probing"}
                    first = list(coach.turn_events(session, copy.deepcopy(stored),
                                 [{"role": "user", "content": "I use Python"}], "I use Python"))
                    self.assertIn("profile", [e for e, _ in first])
                    second = list(coach.turn_events(session, copy.deepcopy(stored),
                                  [{"role": "user", "content": "继续聊"}], "继续聊"))
                    self.assertIn("reasoning_summary", [e for e, _ in second])
                self.assertEqual(stored["version"], 2)
                self.assertEqual(stored["profile"], {} if output != good else {
                    "skills": [{"group": "语言", "items": ["Python"]}]})


if __name__ == "__main__":
    unittest.main()
