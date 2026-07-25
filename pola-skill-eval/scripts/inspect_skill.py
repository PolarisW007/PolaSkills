#!/usr/bin/env python3
"""Inspect an Agent Skill and emit evidence-rich reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pola_skill_eval.inspection import inspect_skill
from pola_skill_eval.reporting import inspection_markdown
from pola_skill_eval.utils import write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_path", help="Path to the Skill directory")
    parser.add_argument(
        "--profile",
        choices=("auto", "codex", "agent-skills", "generic"),
        default="auto",
    )
    parser.add_argument("--json", dest="json_path", help="Write JSON report")
    parser.add_argument("--markdown", help="Write Markdown report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = inspect_skill(args.skill_path, profile=args.profile)
    if args.json_path:
        write_json(Path(args.json_path), report)
    if args.markdown:
        markdown_path = Path(args.markdown)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(inspection_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return {"pass": 0, "conditional": 1, "reject": 2}.get(report["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
