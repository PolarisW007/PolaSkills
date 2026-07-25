"""Aggregate task, trigger, and performance evidence."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .utils import percentile


def _metric(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "p50": None, "p95": None, "mean": None}
    return {
        "count": len(values),
        "p50": round(percentile(values, 50) or 0.0, 3),
        "p95": round(percentile(values, 95) or 0.0, 3),
        "mean": round(sum(values) / len(values), 3),
    }


def aggregate_runs(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_arm[record["arm"]].append(record)

    arms: dict[str, Any] = {}
    for arm, arm_records in sorted(by_arm.items()):
        completed = [record for record in arm_records if record["status"] == "completed"]
        passed = [record for record in arm_records if record.get("passed")]
        durations = [
            float(record["duration_ms"])
            for record in completed
            if record.get("duration_ms") is not None
        ]
        output_bytes = [
            float(record["output_bytes"])
            for record in completed
            if record.get("output_bytes") is not None
        ]
        tokens: list[float] = []
        costs: list[float] = []
        tool_calls: list[float] = []
        for record in completed:
            trace = record.get("trace")
            if not isinstance(trace, dict):
                continue
            usage = trace.get("usage")
            if isinstance(usage, dict):
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
                if isinstance(input_tokens, (int, float)) and isinstance(
                    output_tokens, (int, float)
                ):
                    tokens.append(float(input_tokens + output_tokens))
                if isinstance(usage.get("cost_usd"), (int, float)):
                    costs.append(float(usage["cost_usd"]))
            if isinstance(trace.get("tool_calls"), (int, float)):
                tool_calls.append(float(trace["tool_calls"]))

        trigger_records = [
            record
            for record in completed
            if isinstance(record.get("should_trigger"), bool)
            and isinstance(
                (record.get("trace") or {}).get("skill_invoked")
                if isinstance(record.get("trace"), dict)
                else None,
                bool,
            )
        ]
        tp = fp = tn = fn = 0
        for record in trigger_records:
            expected = record["should_trigger"]
            actual = record["trace"]["skill_invoked"]
            if expected and actual:
                tp += 1
            elif expected and not actual:
                fn += 1
            elif not expected and actual:
                fp += 1
            else:
                tn += 1
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None
            and recall is not None
            and precision + recall
            else None
        )

        by_case: dict[str, list[bool]] = defaultdict(list)
        for record in arm_records:
            by_case[record["case_id"]].append(bool(record.get("passed")))
        pass_at_k_values: list[float] = []
        pass_power_k_values: list[float] = []
        for outcomes in by_case.values():
            pass_at_k_values.append(1.0 if any(outcomes) else 0.0)
            pass_power_k_values.append(1.0 if all(outcomes) else 0.0)

        arms[arm] = {
            "runs": len(arm_records),
            "completed": len(completed),
            "passed": len(passed),
            "success_rate": round(len(passed) / len(arm_records), 6)
            if arm_records
            else None,
            "pass_at_k": round(sum(pass_at_k_values) / len(pass_at_k_values), 6)
            if pass_at_k_values
            else None,
            "pass_power_k": round(
                sum(pass_power_k_values) / len(pass_power_k_values), 6
            )
            if pass_power_k_values
            else None,
            "duration_ms": _metric(durations),
            "output_bytes": _metric(output_bytes),
            "total_tokens": _metric(tokens),
            "cost_usd": _metric(costs),
            "tool_calls": _metric(tool_calls),
            "trigger": {
                "samples": len(trigger_records),
                "tp": tp,
                "fp": fp,
                "tn": tn,
                "fn": fn,
                "precision": round(precision, 6) if precision is not None else None,
                "recall": round(recall, 6) if recall is not None else None,
                "f1": round(f1, 6) if f1 is not None else None,
            },
        }

    candidate = arms.get("candidate")
    comparisons: dict[str, Any] = {}
    if candidate:
        for reference in ("baseline", "old"):
            other = arms.get(reference)
            if other and candidate["success_rate"] is not None and other[
                "success_rate"
            ] is not None:
                comparisons[f"candidate_vs_{reference}"] = {
                    "success_rate_delta": round(
                        candidate["success_rate"] - other["success_rate"], 6
                    ),
                    "p95_duration_delta_ms": _optional_delta(
                        candidate["duration_ms"]["p95"], other["duration_ms"]["p95"]
                    ),
                    "p95_output_bytes_delta": _optional_delta(
                        candidate["output_bytes"]["p95"],
                        other["output_bytes"]["p95"],
                    ),
                }
    return {"arms": arms, "comparisons": comparisons}


def _optional_delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 3)


def decide(
    records: list[dict[str, Any]],
    aggregate: dict[str, Any],
    thresholds: dict[str, Any],
    *,
    mode: str,
) -> dict[str, Any]:
    gates: list[dict[str, Any]] = []
    candidate_records = [record for record in records if record["arm"] == "candidate"]
    candidate = aggregate["arms"].get("candidate")
    baseline = aggregate["arms"].get("baseline")
    if not candidate_records or not candidate:
        return {
            "status": "blocked",
            "confidence": "none",
            "reasons": ["no candidate runs were produced"],
            "gates": [{"name": "candidate_runs", "passed": False}],
        }

    execution_ok = all(
        record["status"] == "completed" for record in candidate_records
    )
    gates.append({"name": "candidate_execution", "passed": execution_ok})
    critical_ok = not any(
        not grade["passed"] and grade["severity"] == "critical"
        for record in candidate_records
        for grade in record.get("grades", [])
    )
    gates.append({"name": "critical_graders", "passed": critical_ok})

    success_min = thresholds["candidate_success_min"]
    success_ok = (
        candidate["success_rate"] is not None
        and candidate["success_rate"] >= success_min
    )
    gates.append(
        {
            "name": "candidate_success_rate",
            "passed": success_ok,
            "actual": candidate["success_rate"],
            "threshold": success_min,
        }
    )

    performance_limit = thresholds.get("max_p95_duration_ms")
    p95 = candidate["duration_ms"]["p95"]
    performance_ok = (
        True if performance_limit is None else p95 is not None and p95 <= performance_limit
    )
    gates.append(
        {
            "name": "candidate_p95_duration",
            "passed": performance_ok,
            "actual": p95,
            "threshold": performance_limit,
        }
    )

    trigger_f1 = candidate["trigger"]["f1"]
    trigger_required = mode == "release"
    trigger_ok = (
        not trigger_required
        or trigger_f1 is not None
        and trigger_f1 >= thresholds["trigger_f1_min"]
    )
    gates.append(
        {
            "name": "trigger_f1",
            "passed": trigger_ok,
            "actual": trigger_f1,
            "threshold": thresholds["trigger_f1_min"]
            if trigger_required
            else None,
        }
    )

    uplift = (
        aggregate["comparisons"]
        .get("candidate_vs_baseline", {})
        .get("success_rate_delta")
    )
    baseline_available = baseline is not None
    uplift_ok = uplift is not None and uplift >= thresholds["uplift_min"]
    gates.append(
        {
            "name": "baseline_uplift",
            "passed": uplift_ok,
            "actual": uplift,
            "threshold": thresholds["uplift_min"],
        }
    )

    hard_failures = [
        gate["name"]
        for gate in gates
        if not gate["passed"]
        and gate["name"]
        in {
            "candidate_execution",
            "critical_graders",
            "candidate_success_rate",
            "candidate_p95_duration",
            "trigger_f1",
        }
    ]
    if hard_failures:
        status = "reject"
        reasons = [f"hard gate failed: {name}" for name in hard_failures]
    elif not baseline_available:
        status = "inconclusive"
        reasons = ["no baseline arm is available"]
    elif not uplift_ok:
        status = "conditional"
        reasons = ["candidate uplift does not meet the configured threshold"]
    elif len(candidate_records) < 3:
        status = "conditional"
        reasons = ["fewer than three candidate observations"]
    else:
        status = "pass"
        reasons = ["all hard gates and comparison thresholds passed"]
    return {
        "status": status,
        "confidence": "high"
        if len(candidate_records) >= 10
        else ("medium" if len(candidate_records) >= 3 else "low"),
        "reasons": reasons,
        "gates": gates,
    }
