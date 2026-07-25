from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from pola_skill_eval.runner import (
    RunnerConfigError,
    build_plan,
    execute_matrix,
    load_runner_config,
    normalize_arms,
)
from pola_skill_eval.suite import load_and_validate_suite, validate_suite


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = load_and_validate_suite(SKILL_ROOT / "tests/fixtures/suite.json")
        self.runner = load_runner_config(SKILL_ROOT / "tests/fixtures/runner.json")
        self.arms = normalize_arms(
            candidate=SKILL_ROOT / "tests/fixtures/good-skill", baseline=None
        )

    def test_dry_plan_is_complete_and_side_effect_free(self) -> None:
        plan = build_plan(self.suite, self.arms, concurrency=2)
        self.assertFalse(plan["execute"])
        self.assertEqual(26, len(plan["jobs"]))

    def test_paired_matrix_passes_and_has_uplift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "matrix"
            report = execute_matrix(
                suite=self.suite,
                runner=self.runner,
                arms=self.arms,
                output_dir=output,
                concurrency=2,
            )
            workspaces = {
                run["evidence_dir"] + "/workspace" for run in report["runs"]
            }
            self.assertEqual(len(report["runs"]), len(workspaces))
            self.assertTrue((output / "report.json").is_file())
            self.assertTrue((output / "report.md").is_file())
        self.assertEqual("pass", report["decision"]["status"])
        self.assertGreater(
            report["aggregate"]["comparisons"]["candidate_vs_baseline"][
                "success_rate_delta"
            ],
            0.2,
        )
        self.assertEqual(1.0, report["aggregate"]["arms"]["candidate"]["trigger"]["f1"])
        self.assertGreater(
            report["aggregate"]["arms"]["candidate"]["total_tokens"]["count"], 0
        )

    def test_timeout_is_bounded(self) -> None:
        suite = validate_suite(
            {
                "schema_version": "1.0",
                "name": "timeout",
                "mode": "standard",
                "defaults": {
                    "trials": 1,
                    "timeout_seconds": 1,
                    "max_output_bytes": 4096,
                },
                "cases": [
                    {
                        "id": "timeout",
                        "category": "robustness",
                        "prompt": "[SLEEP] wait",
                        "graders": [
                            {
                                "type": "exit_code",
                                "expected": 0,
                                "severity": "critical",
                            }
                        ],
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            report = execute_matrix(
                suite=suite,
                runner=self.runner,
                arms=self.arms,
                output_dir=Path(temporary) / "timeout",
                concurrency=2,
            )
        self.assertTrue(all(run["status"] == "timeout" for run in report["runs"]))
        self.assertEqual("reject", report["decision"]["status"])

    def test_stream_output_limit_is_enforced(self) -> None:
        suite = validate_suite(
            {
                "schema_version": "1.0",
                "name": "output-limit",
                "mode": "standard",
                "defaults": {
                    "trials": 1,
                    "timeout_seconds": 5,
                    "max_output_bytes": 1024,
                },
                "cases": [
                    {
                        "id": "flood",
                        "category": "robustness",
                        "prompt": "[FLOOD] emit too much",
                        "graders": [
                            {
                                "type": "max_output_bytes",
                                "value": 1024,
                                "severity": "critical",
                            }
                        ],
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            report = execute_matrix(
                suite=suite,
                runner=self.runner,
                arms=self.arms,
                output_dir=Path(temporary) / "flood",
                concurrency=2,
            )
        self.assertTrue(
            all(run["status"] == "output_limit" for run in report["runs"])
        )

    def test_sensitive_runner_environment_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "runner.json"
            config.write_text(
                json.dumps({"argv": ["true"], "env": {"API_TOKEN": "value"}}),
                encoding="utf-8",
            )
            with self.assertRaises(RunnerConfigError):
                load_runner_config(config)

    def test_output_directory_inside_candidate_is_refused(self) -> None:
        output = SKILL_ROOT / "tests/fixtures/good-skill/eval-output"
        self.assertFalse(output.exists())
        with self.assertRaises(ValueError):
            execute_matrix(
                suite=self.suite,
                runner=self.runner,
                arms=self.arms,
                output_dir=output,
                concurrency=1,
            )

    def test_runner_cwd_must_stay_inside_workspace(self) -> None:
        suite = validate_suite(
            {
                "schema_version": "1.0",
                "name": "cwd-boundary",
                "mode": "standard",
                "cases": [
                    {
                        "id": "cwd",
                        "category": "security",
                        "prompt": "Do not run outside the workspace.",
                        "graders": [
                            {
                                "type": "exit_code",
                                "expected": 0,
                                "severity": "critical",
                            }
                        ],
                    }
                ],
            }
        )
        runner = dict(self.runner)
        runner["cwd"] = "{config_dir}"
        with tempfile.TemporaryDirectory() as temporary:
            report = execute_matrix(
                suite=suite,
                runner=runner,
                arms=self.arms,
                output_dir=Path(temporary) / "cwd",
                concurrency=1,
            )
        self.assertTrue(
            all(run["status"] == "start_error" for run in report["runs"])
        )


if __name__ == "__main__":
    unittest.main()
