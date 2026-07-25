#!/usr/bin/env python3
"""Run the evaluator's offline functional, robustness, and performance harness."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from pola_skill_eval.inspection import inspect_skill
from pola_skill_eval.runner import (
    execute_matrix,
    load_runner_config,
    normalize_arms,
)
from pola_skill_eval.suite import load_and_validate_suite
from pola_skill_eval.utils import percentile


def run_unit_tests() -> tuple[bool, int]:
    suite = unittest.defaultTestLoader.discover(str(SKILL_ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return result.wasSuccessful(), result.testsRun


def run_performance_probe() -> dict:
    fixture = SKILL_ROOT / "tests/fixtures/good-skill"
    durations: list[float] = []
    for _ in range(30):
        started = time.perf_counter()
        report = inspect_skill(fixture)
        if report["status"] != "pass":
            raise AssertionError("good fixture failed static inspection")
        durations.append((time.perf_counter() - started) * 1000)
    p50 = percentile(durations, 50) or 0
    p95 = percentile(durations, 95) or 0
    if p50 >= 150 or p95 >= 500:
        raise AssertionError(
            f"static inspection performance regression: p50={p50:.3f}, p95={p95:.3f}"
        )
    return {
        "samples": len(durations),
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "budget": {"p50_ms": 150, "p95_ms": 500},
    }


def run_integration_probe() -> dict:
    suite = load_and_validate_suite(SKILL_ROOT / "tests/fixtures/suite.json")
    runner = load_runner_config(SKILL_ROOT / "tests/fixtures/runner.json")
    arms = normalize_arms(
        candidate=SKILL_ROOT / "tests/fixtures/good-skill", baseline=None
    )
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as temporary:
        report = execute_matrix(
            suite=suite,
            runner=runner,
            arms=arms,
            output_dir=Path(temporary) / "matrix",
            concurrency=2,
        )
    elapsed = time.perf_counter() - started
    if report["decision"]["status"] != "pass":
        raise AssertionError(
            f"paired harness did not pass: {report['decision']['reasons']}"
        )
    uplift = report["aggregate"]["comparisons"]["candidate_vs_baseline"][
        "success_rate_delta"
    ]
    if uplift < 0.2:
        raise AssertionError(f"candidate uplift is too small: {uplift}")
    if elapsed >= 15:
        raise AssertionError(f"end-to-end harness exceeded 15 seconds: {elapsed:.3f}")
    return {
        "decision": report["decision"]["status"],
        "runs": len(report["runs"]),
        "candidate_success": report["aggregate"]["arms"]["candidate"]["success_rate"],
        "baseline_success": report["aggregate"]["arms"]["baseline"]["success_rate"],
        "uplift": uplift,
        "candidate_trigger_f1": report["aggregate"]["arms"]["candidate"]["trigger"][
            "f1"
        ],
        "candidate_p95_ms": report["aggregate"]["arms"]["candidate"]["duration_ms"][
            "p95"
        ],
        "elapsed_seconds": round(elapsed, 3),
        "budget_seconds": 15,
    }


def main() -> int:
    started = time.perf_counter()
    unit_ok, tests_run = run_unit_tests()
    summary = {
        "unit_tests": {"passed": unit_ok, "count": tests_run},
        "static_performance": None,
        "paired_integration": None,
        "self_inspection": None,
    }
    if unit_ok:
        try:
            summary["static_performance"] = run_performance_probe()
            summary["paired_integration"] = run_integration_probe()
            self_report = inspect_skill(SKILL_ROOT)
            summary["self_inspection"] = {
                "status": self_report["status"],
                "findings": len(self_report["findings"]),
                "estimated_tokens": self_report["metrics"][
                    "skill_body_estimated_tokens"
                ],
                "package_bytes": self_report["metrics"]["package_bytes"],
            }
            if self_report["status"] != "pass":
                raise AssertionError(
                    f"evaluator self-inspection returned {self_report['status']}"
                )
        except AssertionError as exc:
            summary["error"] = str(exc)
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    summary["passed"] = (
        unit_ok
        and "error" not in summary
        and summary["static_performance"] is not None
        and summary["paired_integration"] is not None
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
