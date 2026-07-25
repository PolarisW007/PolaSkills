#!/usr/bin/env python3
"""Preview or execute a baseline/candidate/old Skill evaluation matrix."""

from __future__ import annotations

import argparse
import json

from pola_skill_eval.runner import (
    build_plan,
    execute_matrix,
    load_runner_config,
    normalize_arms,
)
from pola_skill_eval.suite import load_and_validate_suite


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--runner", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--old")
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", choices=("quick", "standard", "release"))
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually execute runner commands; omitted means dry-run",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        suite = load_and_validate_suite(args.suite, requested_mode=args.mode)
        runner = load_runner_config(args.runner)
        arms = normalize_arms(
            candidate=args.candidate, baseline=args.baseline, old=args.old
        )
        if not args.execute:
            plan = build_plan(suite, arms, concurrency=args.concurrency)
            print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        report = execute_matrix(
            suite=suite,
            runner=runner,
            arms=arms,
            output_dir=args.output,
            concurrency=args.concurrency,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "status": report["decision"]["status"],
                "confidence": report["decision"]["confidence"],
                "output": args.output,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return {
        "pass": 0,
        "conditional": 1,
        "inconclusive": 1,
        "blocked": 2,
        "reject": 2,
    }[report["decision"]["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
