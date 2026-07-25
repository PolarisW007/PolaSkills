# Eval Suite Format

Use UTF-8 JSON with `schema_version: "1.0"`.

## Minimal Example

```json
{
  "schema_version": "1.0",
  "name": "example-skill-release",
  "mode": "standard",
  "defaults": {
    "trials": 3,
    "timeout_seconds": 60,
    "max_output_bytes": 1048576
  },
  "thresholds": {
    "candidate_success_min": 0.8,
    "uplift_min": 0.1,
    "trigger_f1_min": 0.8,
    "max_p95_duration_ms": 30000
  },
  "cases": [
    {
      "id": "core-task",
      "category": "task_success",
      "split": "holdout",
      "prompt": "Perform the representative user task.",
      "should_trigger": true,
      "graders": [
        {
          "type": "exit_code",
          "expected": 0,
          "severity": "critical"
        },
        {
          "type": "file_exists",
          "path": "workspace/result.json",
          "severity": "critical"
        }
      ]
    }
  ]
}
```

## Top-Level Fields

- `schema_version`: required, currently `"1.0"`.
- `name`: required suite identity.
- `mode`: `quick`, `standard`, or `release`.
- `defaults.trials`: 1–20.
- `defaults.timeout_seconds`: 1–600.
- `defaults.max_output_bytes`: 1 KiB–10 MiB.
- `thresholds.candidate_success_min`: 0–1.
- `thresholds.uplift_min`: -1–1.
- `thresholds.trigger_f1_min`: 0–1.
- `thresholds.max_p95_duration_ms`: optional 1–600000.
- `cases`: non-empty array with at most 200 entries.

## Case Fields

- `id`: unique lowercase identifier containing letters, digits, `.`, `_`, or `-`.
- `category`: `trigger_positive`, `trigger_negative`, `task_success`, `robustness`, `security`, `performance`, or `coexistence`.
- `split`: `authoring`, `holdout`, or `regression`.
- `prompt`: full task prompt passed through a prompt file, at most 1 MiB.
- `should_trigger`: boolean when trigger evidence is expected.
- `trials`, `timeout_seconds`, `max_output_bytes`: optional per-case overrides.
- `graders`: 1–50 deterministic graders.

Release mode requires trigger positive, trigger negative, task success, robustness, security, and performance categories.

## Graders

### Exit and Text

```json
{"type": "exit_code", "expected": 0, "severity": "critical"}
{"type": "stdout_contains", "value": "DONE", "severity": "high"}
{"type": "stdout_not_contains", "value": "secret", "severity": "critical"}
{"type": "response_contains", "value": "TASK_OK", "severity": "critical"}
{"type": "response_not_contains", "value": "fabricated", "severity": "high"}
{"type": "response_regex", "value": "^result: [0-9]+$", "severity": "high"}
```

### Files and JSON

Paths are relative to the run evidence directory. Absolute paths and `..` are rejected.

```json
{"type": "file_exists", "path": "workspace/output.json", "severity": "critical"}
{
  "type": "json_path_equals",
  "path": "workspace/output.json",
  "json_path": "status",
  "expected": "ok",
  "severity": "critical"
}
```

Dotted JSON paths can traverse dictionaries and numeric list indexes, for example `items.0.status`.

### Performance and Triggering

```json
{"type": "max_duration_ms", "value": 30000, "severity": "critical"}
{"type": "max_output_bytes", "value": 1048576, "severity": "high"}
{"type": "skill_invoked", "expected": true, "severity": "critical"}
```

`skill_invoked` needs runner trace evidence. Missing trace is a failure, not `false`.

## Severity

- `critical`: release-defining gate.
- `high`: major expected behavior.
- `medium`: quality or maintainability risk.
- `info`: observation; does not fail a run.

## Authoring Guidance

- Grade outcomes, not exact wording, unless exact wording is the requirement.
- Give baseline and candidate the same case.
- Keep fixture data synthetic.
- Use holdouts for final claims.
- Include negative triggers to expose over-eager Skills.
- Use explicit budgets rather than “fast enough.”
