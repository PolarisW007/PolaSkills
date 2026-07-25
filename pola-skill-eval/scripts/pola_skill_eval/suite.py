"""Validation and normalization for Skill evaluation suites."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .utils import SAFE_ID_RE, load_json, validate_relative_path


MODES = {"quick", "standard", "release"}
CATEGORIES = {
    "trigger_positive",
    "trigger_negative",
    "task_success",
    "robustness",
    "security",
    "performance",
    "coexistence",
}
RELEASE_CATEGORIES = {
    "trigger_positive",
    "trigger_negative",
    "task_success",
    "robustness",
    "security",
    "performance",
}
GRADER_TYPES = {
    "exit_code",
    "stdout_contains",
    "stdout_not_contains",
    "response_contains",
    "response_not_contains",
    "response_regex",
    "file_exists",
    "json_path_equals",
    "max_duration_ms",
    "max_output_bytes",
    "skill_invoked",
}
SEVERITIES = {"critical", "high", "medium", "info"}
SPLITS = {"authoring", "holdout", "regression"}


class SuiteValidationError(ValueError):
    """Raised when an evaluation suite violates its contract."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _number(
    value: Any,
    location: str,
    errors: list[str],
    *,
    minimum: float,
    maximum: float,
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{location} must be a number")
        return None
    if value < minimum or value > maximum:
        errors.append(f"{location} must be between {minimum} and {maximum}")
        return None
    return float(value)


def _integer(
    value: Any,
    location: str,
    errors: list[str],
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{location} must be an integer")
        return None
    if value < minimum or value > maximum:
        errors.append(f"{location} must be between {minimum} and {maximum}")
        return None
    return value


def _validate_grader(
    grader: Any, location: str, errors: list[str]
) -> dict[str, Any] | None:
    if not isinstance(grader, dict):
        errors.append(f"{location} must be an object")
        return None
    grader_type = grader.get("type")
    if grader_type not in GRADER_TYPES:
        errors.append(f"{location}.type is unsupported: {grader_type!r}")
        return None
    severity = grader.get("severity", "high")
    if severity not in SEVERITIES:
        errors.append(f"{location}.severity is unsupported: {severity!r}")
    normalized = dict(grader)
    normalized["severity"] = severity

    if grader_type == "exit_code":
        if not isinstance(grader.get("expected"), int):
            errors.append(f"{location}.expected must be an integer")
    elif grader_type in {
        "stdout_contains",
        "stdout_not_contains",
        "response_contains",
        "response_not_contains",
        "response_regex",
    }:
        if not isinstance(grader.get("value"), str) or not grader.get("value"):
            errors.append(f"{location}.value must be a non-empty string")
        elif grader_type == "response_regex":
            try:
                re.compile(grader["value"])
            except re.error as exc:
                errors.append(f"{location}.value is not a valid regex: {exc}")
    elif grader_type == "file_exists":
        try:
            normalized["path"] = validate_relative_path(grader.get("path"))
        except (TypeError, ValueError) as exc:
            errors.append(f"{location}.path: {exc}")
    elif grader_type == "json_path_equals":
        try:
            normalized["path"] = validate_relative_path(grader.get("path"))
        except (TypeError, ValueError) as exc:
            errors.append(f"{location}.path: {exc}")
        if not isinstance(grader.get("json_path"), str) or not grader.get("json_path"):
            errors.append(f"{location}.json_path must be a non-empty dotted path")
        if "expected" not in grader:
            errors.append(f"{location}.expected is required")
    elif grader_type in {"max_duration_ms", "max_output_bytes"}:
        upper = 600_000 if grader_type == "max_duration_ms" else 10 * 1024 * 1024
        _number(
            grader.get("value"),
            f"{location}.value",
            errors,
            minimum=1,
            maximum=upper,
        )
    elif grader_type == "skill_invoked":
        if not isinstance(grader.get("expected"), bool):
            errors.append(f"{location}.expected must be a boolean")
    return normalized


def validate_suite(data: Any, *, requested_mode: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        raise SuiteValidationError(["suite must be a JSON object"])
    if data.get("schema_version") != "1.0":
        errors.append("schema_version must be '1.0'")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name must be a non-empty string")
    mode = requested_mode or data.get("mode", "standard")
    if mode not in MODES:
        errors.append(f"mode must be one of {sorted(MODES)}")
        mode = "standard"

    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        errors.append("defaults must be an object")
        defaults = {}
    default_trials = defaults.get("trials", 1)
    default_timeout = defaults.get("timeout_seconds", 60)
    default_output = defaults.get("max_output_bytes", 1024 * 1024)
    _integer(default_trials, "defaults.trials", errors, minimum=1, maximum=20)
    _number(
        default_timeout,
        "defaults.timeout_seconds",
        errors,
        minimum=1,
        maximum=600,
    )
    _integer(
        default_output,
        "defaults.max_output_bytes",
        errors,
        minimum=1024,
        maximum=10 * 1024 * 1024,
    )

    thresholds = data.get("thresholds", {})
    if not isinstance(thresholds, dict):
        errors.append("thresholds must be an object")
        thresholds = {}
    normalized_thresholds = {
        "candidate_success_min": thresholds.get("candidate_success_min", 0.8),
        "uplift_min": thresholds.get("uplift_min", 0.1),
        "trigger_f1_min": thresholds.get("trigger_f1_min", 0.8),
        "max_p95_duration_ms": thresholds.get("max_p95_duration_ms"),
    }
    for key in ("candidate_success_min", "uplift_min", "trigger_f1_min"):
        _number(
            normalized_thresholds[key],
            f"thresholds.{key}",
            errors,
            minimum=-1 if key == "uplift_min" else 0,
            maximum=1,
        )
    if normalized_thresholds["max_p95_duration_ms"] is not None:
        _number(
            normalized_thresholds["max_p95_duration_ms"],
            "thresholds.max_p95_duration_ms",
            errors,
            minimum=1,
            maximum=600_000,
        )

    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty array")
        cases = []
    elif len(cases) > 200:
        errors.append("cases must contain at most 200 entries")
    ids: set[str] = set()
    categories: set[str] = set()
    normalized_cases: list[dict[str, Any]] = []
    holdout_count = 0
    for index, case in enumerate(cases):
        location = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{location} must be an object")
            continue
        case_id = case.get("id")
        if not isinstance(case_id, str) or not SAFE_ID_RE.fullmatch(case_id):
            errors.append(f"{location}.id must be a safe lowercase identifier")
        elif case_id in ids:
            errors.append(f"{location}.id is duplicated: {case_id}")
        else:
            ids.add(case_id)
        category = case.get("category")
        if category not in CATEGORIES:
            errors.append(f"{location}.category is unsupported: {category!r}")
        else:
            categories.add(category)
        split = case.get("split", "authoring")
        if split not in SPLITS:
            errors.append(f"{location}.split is unsupported: {split!r}")
        elif split == "holdout":
            holdout_count += 1
        prompt = case.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            errors.append(f"{location}.prompt must be a non-empty string")
        elif len(prompt.encode("utf-8")) > 1024 * 1024:
            errors.append(f"{location}.prompt must not exceed 1 MiB")
        should_trigger = case.get("should_trigger")
        if category in {"trigger_positive", "trigger_negative"} and not isinstance(
            should_trigger, bool
        ):
            errors.append(f"{location}.should_trigger must be a boolean")
        trials = case.get("trials", default_trials)
        timeout = case.get("timeout_seconds", default_timeout)
        output_limit = case.get("max_output_bytes", default_output)
        _integer(trials, f"{location}.trials", errors, minimum=1, maximum=20)
        _number(
            timeout,
            f"{location}.timeout_seconds",
            errors,
            minimum=1,
            maximum=600,
        )
        _integer(
            output_limit,
            f"{location}.max_output_bytes",
            errors,
            minimum=1024,
            maximum=10 * 1024 * 1024,
        )
        graders = case.get("graders")
        if not isinstance(graders, list) or not graders:
            errors.append(f"{location}.graders must be a non-empty array")
            graders = []
        elif len(graders) > 50:
            errors.append(f"{location}.graders must contain at most 50 entries")
        normalized_graders: list[dict[str, Any]] = []
        for grader_index, grader in enumerate(graders):
            normalized = _validate_grader(
                grader, f"{location}.graders[{grader_index}]", errors
            )
            if normalized is not None:
                normalized_graders.append(normalized)
        normalized_case = dict(case)
        normalized_case.update(
            {
                "split": split,
                "trials": trials,
                "timeout_seconds": timeout,
                "max_output_bytes": output_limit,
                "graders": normalized_graders,
            }
        )
        normalized_cases.append(normalized_case)

    if mode == "release":
        missing = sorted(RELEASE_CATEGORIES - categories)
        if missing:
            errors.append(f"release mode is missing categories: {', '.join(missing)}")
        if holdout_count == 0:
            warnings.append("release suite has no holdout cases")
        if default_trials < 3 and not any(
            isinstance(case.get("trials"), int) and case.get("trials", 0) >= 3
            for case in cases
            if isinstance(case, dict)
        ):
            warnings.append("release suite has no case with at least three trials")

    if errors:
        raise SuiteValidationError(errors)
    return {
        "schema_version": "1.0",
        "name": name.strip(),
        "mode": mode,
        "defaults": {
            "trials": default_trials,
            "timeout_seconds": default_timeout,
            "max_output_bytes": default_output,
        },
        "thresholds": normalized_thresholds,
        "cases": normalized_cases,
        "warnings": warnings,
    }


def load_and_validate_suite(
    path: str | Path, *, requested_mode: str | None = None
) -> dict[str, Any]:
    suite_path = Path(path).expanduser().resolve()
    return validate_suite(load_json(suite_path), requested_mode=requested_mode)
