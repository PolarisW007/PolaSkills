from __future__ import annotations

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from pola_skill_eval.statistics import aggregate_runs, decide


def record(arm: str, case: str, passed: bool, invoked: bool) -> dict:
    return {
        "arm": arm,
        "case_id": case,
        "trial": 1,
        "status": "completed",
        "passed": passed,
        "duration_ms": 10 if arm == "candidate" else 20,
        "output_bytes": 100,
        "should_trigger": case == "positive",
        "trace": {
            "skill_invoked": invoked,
            "tool_calls": 1,
            "usage": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001},
        },
        "grades": [],
    }


class StatisticsTests(unittest.TestCase):
    def test_aggregate_and_decision_keep_uplift_visible(self) -> None:
        records = [
            record("candidate", "positive", True, True),
            record("candidate", "negative", True, False),
            record("candidate", "third", True, False),
            record("baseline", "positive", False, False),
            record("baseline", "negative", True, False),
            record("baseline", "third", False, False),
        ]
        aggregate = aggregate_runs(records)
        decision = decide(
            records,
            aggregate,
            {
                "candidate_success_min": 0.8,
                "uplift_min": 0.2,
                "trigger_f1_min": 0.8,
                "max_p95_duration_ms": 100,
            },
            mode="release",
        )
        self.assertEqual("pass", decision["status"])
        self.assertGreater(
            aggregate["comparisons"]["candidate_vs_baseline"]["success_rate_delta"],
            0.2,
        )
        self.assertEqual(1.0, aggregate["arms"]["candidate"]["trigger"]["f1"])


if __name__ == "__main__":
    unittest.main()
