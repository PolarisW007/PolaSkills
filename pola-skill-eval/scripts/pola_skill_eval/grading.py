"""Deterministic graders for completed runner records."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .utils import is_within, validate_relative_path


def _evidence(
    grader: dict[str, Any],
    passed: bool,
    expected: Any,
    actual: Any,
    message: str,
) -> dict[str, Any]:
    return {
        "type": grader["type"],
        "severity": grader.get("severity", "high"),
        "passed": bool(passed),
        "expected": expected,
        "actual": actual,
        "message": message,
    }


def _safe_run_file(run_dir: Path, relative: str) -> Path:
    normalized = validate_relative_path(relative)
    path = (run_dir / normalized).resolve(strict=False)
    if not is_within(path, run_dir):
        raise ValueError(f"grader path escapes run directory: {relative}")
    return path


def _json_path(value: Any, dotted_path: str) -> Any:
    current = value
    for part in dotted_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise KeyError(dotted_path)
    return current


def grade_run(
    record: dict[str, Any], case: dict[str, Any], run_dir: Path
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    response = record.get("response", "")
    stdout = record.get("stdout", "")
    trace = record.get("trace")
    for grader in case["graders"]:
        grader_type = grader["type"]
        try:
            if grader_type == "exit_code":
                expected = grader["expected"]
                actual = record.get("exit_code")
                results.append(
                    _evidence(
                        grader,
                        actual == expected,
                        expected,
                        actual,
                        "runner exit code",
                    )
                )
            elif grader_type in {"stdout_contains", "stdout_not_contains"}:
                value = grader["value"]
                contains = value in stdout
                expected = grader_type == "stdout_contains"
                results.append(
                    _evidence(
                        grader,
                        contains == expected,
                        expected,
                        contains,
                        f"stdout {'contains' if contains else 'does not contain'} marker",
                    )
                )
            elif grader_type in {"response_contains", "response_not_contains"}:
                value = grader["value"]
                contains = value in response
                expected = grader_type == "response_contains"
                results.append(
                    _evidence(
                        grader,
                        contains == expected,
                        expected,
                        contains,
                        f"response {'contains' if contains else 'does not contain'} marker",
                    )
                )
            elif grader_type == "response_regex":
                matched = re.search(grader["value"], response) is not None
                results.append(
                    _evidence(
                        grader,
                        matched,
                        grader["value"],
                        matched,
                        "response regex match",
                    )
                )
            elif grader_type == "file_exists":
                path = _safe_run_file(run_dir, grader["path"])
                exists = path.exists()
                results.append(
                    _evidence(
                        grader, exists, True, exists, f"file existence: {grader['path']}"
                    )
                )
            elif grader_type == "json_path_equals":
                path = _safe_run_file(run_dir, grader["path"])
                if not path.is_file():
                    raise FileNotFoundError(grader["path"])
                value = json.loads(path.read_text(encoding="utf-8"))
                actual = _json_path(value, grader["json_path"])
                expected = grader["expected"]
                results.append(
                    _evidence(
                        grader,
                        actual == expected,
                        expected,
                        actual,
                        f"JSON path {grader['json_path']}",
                    )
                )
            elif grader_type == "max_duration_ms":
                actual = record.get("duration_ms")
                expected = grader["value"]
                results.append(
                    _evidence(
                        grader,
                        actual is not None and actual <= expected,
                        f"<= {expected}",
                        actual,
                        "run duration in milliseconds",
                    )
                )
            elif grader_type == "max_output_bytes":
                actual = record.get("output_bytes")
                expected = grader["value"]
                results.append(
                    _evidence(
                        grader,
                        actual is not None and actual <= expected,
                        f"<= {expected}",
                        actual,
                        "combined output evidence bytes",
                    )
                )
            elif grader_type == "skill_invoked":
                actual = trace.get("skill_invoked") if isinstance(trace, dict) else None
                expected = grader["expected"]
                results.append(
                    _evidence(
                        grader,
                        actual is expected,
                        expected,
                        actual,
                        "runner trace skill_invoked signal",
                    )
                )
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            results.append(
                _evidence(grader, False, "valid evidence", None, f"grader error: {exc}")
            )
    return results


def run_passed(record: dict[str, Any], grades: list[dict[str, Any]]) -> bool:
    if record.get("status") != "completed":
        return False
    return all(item["passed"] for item in grades if item["severity"] != "info")
