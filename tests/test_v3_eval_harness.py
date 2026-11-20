"""#71 unittest 包装：让评测 harness 随回归套件跑，阈值不过即红。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.eval.run_extraction_eval import run  # noqa: E402


class ExtractionEvalTest(unittest.TestCase):
    def test_eval_precision_and_coverage_at_target(self):
        self.assertTrue(run(), "评测未达标：precision/coverage < 0.95")


if __name__ == "__main__":
    unittest.main()
