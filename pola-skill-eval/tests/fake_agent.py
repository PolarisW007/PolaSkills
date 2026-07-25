#!/usr/bin/env python3
"""Deterministic Agent double used by the offline harness."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-path", default="")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--response-file", required=True)
    parser.add_argument("--trace-file", required=True)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--trial", required=True)
    args = parser.parse_args()
    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    candidate = args.arm == "candidate"

    if "[SLEEP]" in prompt:
        time.sleep(2)
    if "[FLOOD]" in prompt:
        sys.stdout.write("x" * 100_000)
        sys.stdout.flush()

    invoked = False
    exit_code = 0
    if "[SHOULD_NOT_TRIGGER]" in prompt or "[SECURITY]" in prompt:
        response = "REFUSED" if "[SECURITY]" in prompt else "GENERIC"
    elif "[FAULT]" in prompt:
        invoked = candidate
        response = "RECOVERED" if candidate else "FAILED"
        exit_code = 0 if candidate else 3
    else:
        invoked = candidate
        response = "TASK_OK" if candidate else "PARTIAL"

    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "artifact.json").write_text(
        json.dumps({"status": "ok" if candidate else "partial"}),
        encoding="utf-8",
    )
    Path(args.response_file).write_text(response, encoding="utf-8")
    Path(args.trace_file).write_text(
        json.dumps(
            {
                "skill_invoked": invoked,
                "tool_calls": 2 if candidate else 1,
                "usage": {
                    "input_tokens": 120 if candidate else 80,
                    "output_tokens": 30 if candidate else 20,
                    "cost_usd": 0.002 if candidate else 0.001,
                },
            }
        ),
        encoding="utf-8",
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
