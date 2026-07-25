"""Stable Markdown renderers for evaluator reports."""

from __future__ import annotations

from typing import Any


def _display(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def inspection_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Skill Static Inspection",
        "",
        f"- Status: `{report['status']}`",
        f"- Skill: `{report['skill_path']}`",
        f"- Profile: `{report['profile']}`",
        f"- Package: {report['inventory']['files']} files / {report['inventory']['bytes']} bytes",
        f"- Estimated Skill context: {report['metrics'].get('skill_body_estimated_tokens', 0)} tokens",
        f"- Inspection: {report['metrics']['inspection_ms']} ms",
        "",
        "## Dimensions",
        "",
    ]
    for name, score in report["dimensions"].items():
        lines.append(f"- {name}: {score}/100")
    lines.extend(["", "## Findings", ""])
    if not report["findings"]:
        lines.append("No findings.")
    for item in report["findings"]:
        location = item.get("path", "")
        if item.get("line"):
            location += f":{item['line']}"
        suffix = f" ({location})" if location else ""
        lines.append(
            f"- **{item['severity'].upper()} {item['code']}**{suffix}: {item['message']}"
        )
        if item.get("remediation"):
            lines.append(f"  - Remediation: {item['remediation']}")
    return "\n".join(lines) + "\n"


def matrix_markdown(report: dict[str, Any]) -> str:
    decision = report["decision"]
    aggregate = report["aggregate"]
    lines = [
        "# Skill Evaluation Report",
        "",
        f"- Decision: `{decision['status']}`",
        f"- Confidence: `{decision['confidence']}`",
        f"- Suite: `{report['suite']['name']}`",
        f"- Mode: `{report['suite']['mode']}`",
        f"- Runs: {len(report['runs'])}",
        "",
        "## Decision Reasons",
        "",
    ]
    for reason in decision["reasons"]:
        lines.append(f"- {reason}")
    lines.extend(
        [
            "",
            "## Hard Gates",
            "",
            "| Gate | Passed | Actual | Threshold |",
            "|---|---:|---:|---:|",
        ]
    )
    for gate in decision["gates"]:
        lines.append(
            f"| {gate['name']} | {gate['passed']} | "
            f"{_display(gate.get('actual'))} | {_display(gate.get('threshold'))} |"
        )
    lines.extend(
        [
            "",
            "## Arms",
            "",
            "| Arm | Success | p50 ms | p95 ms | p95 output bytes | Trigger F1 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for arm, metrics in aggregate["arms"].items():
        lines.append(
            f"| {arm} | {_display(metrics['success_rate'])} | "
            f"{_display(metrics['duration_ms']['p50'])} | "
            f"{_display(metrics['duration_ms']['p95'])} | "
            f"{_display(metrics['output_bytes']['p95'])} | "
            f"{_display(metrics['trigger']['f1'])} |"
        )
    lines.extend(["", "## Comparisons", ""])
    if not aggregate["comparisons"]:
        lines.append("No reference arm was available.")
    for comparison, metrics in aggregate["comparisons"].items():
        lines.append(
            f"- `{comparison}`: success delta "
            f"{_display(metrics['success_rate_delta'])}, p95 duration delta "
            f"{_display(metrics['p95_duration_delta_ms'])} ms, p95 output delta "
            f"{_display(metrics['p95_output_bytes_delta'])} bytes"
        )
    lines.extend(
        [
            "",
            "## Run Evidence",
            "",
            "| Arm | Case | Trial | Status | Passed | Duration ms | Output bytes |",
            "|---|---|---:|---|---:|---:|---:|",
        ]
    )
    for run in report["runs"]:
        lines.append(
            f"| {run['arm']} | {run['case_id']} | {run['trial']} | "
            f"{run['status']} | {run.get('passed', False)} | "
            f"{_display(run.get('duration_ms'))} | {_display(run.get('output_bytes'))} |"
        )
    return "\n".join(lines) + "\n"
