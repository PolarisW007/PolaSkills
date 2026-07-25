#!/usr/bin/env python3
"""Validate a Skill evaluation suite without executing a runner."""

from __future__ import annotations

import argparse
import json

from pola_skill_eval.suite import SuiteValidationError, load_and_validate_suite


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite")
    parser.add_argument("--mode", choices=("quick", "standard", "release"))
    args = parser.parse_args()
    try:
        suite = load_and_validate_suite(args.suite, requested_mode=args.mode)
    except (OSError, ValueError, SuiteValidationError) as exc:
        errors = getattr(exc, "errors", [str(exc)])
        print(json.dumps({"valid": False, "errors": errors}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "valid": True,
                "name": suite["name"],
                "mode": suite["mode"],
                "cases": len(suite["cases"]),
                "warnings": suite["warnings"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
