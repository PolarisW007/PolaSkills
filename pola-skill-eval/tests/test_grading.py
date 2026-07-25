from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from pola_skill_eval.grading import grade_run, run_passed


class GradingTests(unittest.TestCase):
    def test_deterministic_graders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            (run_dir / "workspace").mkdir()
            (run_dir / "workspace/result.json").write_text(
                json.dumps({"state": {"ok": True}}), encoding="utf-8"
            )
            record = {
                "status": "completed",
                "exit_code": 0,
                "stdout": "DONE",
                "response": "TASK_OK",
                "duration_ms": 10,
                "output_bytes": 100,
                "trace": {"skill_invoked": True},
            }
            case = {
                "graders": [
                    {"type": "exit_code", "expected": 0, "severity": "critical"},
                    {
                        "type": "response_contains",
                        "value": "TASK_OK",
                        "severity": "high",
                    },
                    {
                        "type": "json_path_equals",
                        "path": "workspace/result.json",
                        "json_path": "state.ok",
                        "expected": True,
                        "severity": "critical",
                    },
                    {
                        "type": "skill_invoked",
                        "expected": True,
                        "severity": "critical",
                    },
                ]
            }
            grades = grade_run(record, case, run_dir)
        self.assertTrue(all(item["passed"] for item in grades))
        self.assertTrue(run_passed(record, grades))

    def test_missing_trace_is_not_false(self) -> None:
        record = {
            "status": "completed",
            "exit_code": 0,
            "stdout": "",
            "response": "",
            "duration_ms": 1,
            "output_bytes": 0,
            "trace": None,
        }
        case = {
            "graders": [
                {
                    "type": "skill_invoked",
                    "expected": False,
                    "severity": "critical",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temporary:
            grades = grade_run(record, case, Path(temporary))
        self.assertFalse(grades[0]["passed"])
        self.assertIsNone(grades[0]["actual"])


if __name__ == "__main__":
    unittest.main()
