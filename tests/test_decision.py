import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class _FakeLLM:
    def decide(self, *args, **kwargs):
        raise AssertionError("decision.llm.decide must be mocked")


llm_module = types.ModuleType("llm")
llm_module.llm = _FakeLLM()
previous_llm = sys.modules.get("llm")
sys.modules["llm"] = llm_module
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import decision

if previous_llm is None:
    sys.modules.pop("llm", None)
else:
    sys.modules["llm"] = previous_llm



def payload(intent="kb_qa", confidence=0.9, scope="all", clarify=0.0,
            probabilities=None):
    return {
        "intent": {
            "choice": intent,
            "confidence": confidence,
            "probabilities": probabilities or {intent: confidence},
        },
        "kb_scope": {"choice": scope},
        "need_clarify": {"noul": clarify},
    }


class RouteActionTest(unittest.TestCase):
    def route(self, query="大创项目对保研有加分吗？", **kwargs):
        with patch.object(decision.llm, "decide", return_value=payload(**kwargs)):
            return decision.route(query)

    def test_confident_intent_is_answered_even_if_clarify_probability_is_high(self):
        r = self.route(confidence=0.97, clarify=0.93)
        self.assertEqual(r["intent"], "kb_qa")
        self.assertEqual(r["action"], "answer")

    def test_uncertain_intent_still_clarifies(self):
        r = self.route(
            query="这个怎么弄？", confidence=0.43, clarify=0.9,
            probabilities={"kb_qa": 0.43, "resume_qa": 0.35},
        )
        self.assertEqual(r["action"], "clarify")
        self.assertIn("查资料问答", r["clarify_question"])
        self.assertIn("简历写法咨询", r["clarify_question"])

    def test_confident_out_of_scope_is_refused(self):
        r = self.route(query="推荐一部电影", intent="out_of_scope",
                       confidence=0.95, clarify=0.8)
        self.assertEqual(r["action"], "refuse")

    def test_confident_resume_edit_guides_to_resume(self):
        r = self.route(query="帮我润色简历", intent="resume_edit",
                       confidence=0.94, clarify=0.75)
        self.assertEqual(r["action"], "guide_resume")

    def test_attachment_fast_path_skips_model(self):
        with patch.object(decision.llm, "decide") as decide:
            r = decision.route("这是我的简历", has_attachment=True)
        self.assertEqual(r["action"], "guide_resume")
        self.assertEqual(r["route_layer"], "rule")
        decide.assert_not_called()

    def test_decision_service_failure_falls_back_to_answer(self):
        with patch.object(decision.llm, "decide", side_effect=RuntimeError("down")):
            r = decision.route("保研需要准备哪些材料？")
        self.assertEqual(r["intent"], "kb_qa")
        self.assertEqual(r["action"], "answer")


if __name__ == "__main__":
    unittest.main()
