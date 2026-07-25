---
name: pola-skill-eval
description: Evaluate Agent Skills before release with static gates, baseline-versus-candidate task trials, robustness probes, trigger checks, and performance evidence. Use when reviewing a generated or edited SKILL.md package, comparing Skill versions, building a Skill eval suite, diagnosing why a Skill is unreliable or slow, or deciding whether a Skill is ready to publish.
---

# Pola Skill Eval

Evaluate a Skill as an executable product, not only as prose. Establish a baseline, run repeatable tasks in isolated workspaces, grade observable outcomes, measure cost and latency, and keep hard failures separate from soft scores.

## Choose the Evaluation Depth

Use the smallest mode that can answer the release question:

- **Quick**: Run static inspection while drafting. It checks structure, metadata, references, syntax, risky code patterns, and context/package size.
- **Standard**: Add paired `baseline` and `candidate` task trials. Use this for normal acceptance.
- **Release**: Add trigger positives and negatives, robustness, security, performance, multiple trials, and an optional `old` arm. Prefer holdout cases that were not used to write the Skill.

Do not claim that a Skill is effective from static inspection alone. A well-formed Skill can still reduce task success.

## Evaluation Workflow

### 1. Define the Contract

Before scoring, write down:

- The user jobs the Skill must improve.
- The behavior that must remain unchanged without the Skill.
- Inputs, outputs, side effects, permissions, and forbidden actions.
- Expected trigger and non-trigger requests.
- Failure modes: missing files, malformed input, unavailable tools, timeout, partial results, retry, and repeated execution.
- Performance budgets: latency, context size, output size, tool calls, token usage, and cost when available.

Turn each acceptance criterion into an observable grader. Prefer exit codes, files, JSON fields, response text, and bounded timing over model opinion.

Read [evaluation-method.md](references/evaluation-method.md) when designing the contract or deciding release thresholds.

### 2. Run Static Inspection

Resolve this Skill's directory as `EVAL_SKILL_DIR`, then run:

```bash
python3 "$EVAL_SKILL_DIR/scripts/inspect_skill.py" \
  /absolute/path/to/candidate-skill \
  --profile auto \
  --json /tmp/skill-inspection.json \
  --markdown /tmp/skill-inspection.md
```

Treat these as hard failures:

- Missing or malformed `SKILL.md`.
- Missing `name` or `description`, invalid name, or directory/name mismatch.
- Missing or escaping local references.
- Python or shell syntax errors.
- Critical secret, destructive-command, or shell-injection findings.

Treat excessive body length, package size, unbounded network/process calls, and unclear recovery instructions as risks that require evidence or remediation.

Stop before dynamic execution when static critical gates fail.

### 3. Build an Eval Suite

Copy the format in [suite-format.md](references/suite-format.md). Include:

- Core task-success cases.
- At least one request that should trigger the Skill.
- At least one adjacent request that must not trigger it.
- Invalid, missing, oversized, repeated, and partial inputs where relevant.
- A safe failure/recovery case.
- A performance-budget case.
- A security or permission-boundary case for release evaluations.

Keep authoring cases and holdout cases separate with `split`. Use at least three trials for variable Agent behavior. Never put secrets or real user data in fixtures.

Validate before running:

```bash
python3 "$EVAL_SKILL_DIR/scripts/validate_suite.py" \
  /absolute/path/to/suite.json \
  --mode release
```

Release mode rejects incomplete category coverage, unsafe paths, unknown graders, invalid budgets, duplicate IDs, and cases without deterministic evidence.

### 4. Configure the Runner

Provide a runner adapter as an argv array. The harness never invokes a shell. Read [runner-adapters.md](references/runner-adapters.md) for the contract and examples.

The runner receives separate paths for:

- Candidate or baseline Skill.
- Prompt input.
- Fresh workspace.
- Response output.
- Optional trace output.
- Arm, case ID, and trial number.

Have the runner write `trace.json` with `skill_invoked`, `tool_calls`, and token/cost usage when the host exposes them. Missing trace data must remain missing; do not invent it.

### 5. Preview, Then Execute the Matrix

Preview the validated plan first:

```bash
python3 "$EVAL_SKILL_DIR/scripts/run_matrix.py" \
  --suite /absolute/path/to/suite.json \
  --runner /absolute/path/to/runner.json \
  --candidate /absolute/path/to/candidate-skill \
  --baseline NONE \
  --output /tmp/skill-eval-run
```

The preview must not start the runner or create the output directory.

Execute only after the user has authorized running the configured command:

```bash
python3 "$EVAL_SKILL_DIR/scripts/run_matrix.py" \
  --suite /absolute/path/to/suite.json \
  --runner /absolute/path/to/runner.json \
  --candidate /absolute/path/to/candidate-skill \
  --baseline NONE \
  --old /absolute/path/to/old-skill \
  --output /tmp/skill-eval-run \
  --concurrency 2 \
  --execute
```

Use `NONE` for the no-Skill baseline when the runner supports it. Never point the output directory inside the candidate Skill.

The harness enforces:

- Fresh workspace per case, arm, and trial.
- Maximum concurrency of 8.
- Per-run timeout of 1–600 seconds.
- Combined stdout/stderr cap and response-size evidence.
- Minimal inherited environment without credential-like variables.
- No overwrite of an existing evidence directory.

For untrusted generated scripts, run the entire harness inside a container or restricted operating-system account. Process limits are not a complete sandbox.

### 6. Interpret Evidence

Read `report.json` for automation and `report.md` for review.

Evaluate in this order:

1. **Hard gates**: structure, security, execution integrity, and critical graders.
2. **Usability**: candidate task success and uplift over baseline.
3. **Robustness**: invalid inputs, failure recovery, timeout behavior, idempotence, and bounded side effects.
4. **Trigger quality**: precision, recall, F1, and coexistence with adjacent Skills.
5. **Efficiency**: p50/p95 duration, output bytes, static context estimate, tool calls, tokens, and cost.
6. **Maintainability and generalization**: clear routing, deterministic scripts, representative cases, and holdout performance.

Use the report status literally:

- `pass`: hard gates pass and evidence meets thresholds.
- `conditional`: no critical failure, but uplift, sample size, or high-risk findings need review.
- `inconclusive`: evidence cannot support a comparison.
- `blocked`: configuration or environment prevented evaluation.
- `reject`: a hard gate or critical expected behavior failed.

Do not average a critical failure into a passing total score.

### 7. Produce the Review

Report:

- Decision and confidence.
- Hard-gate failures first.
- Candidate success and uplift versus baseline and old version.
- Trigger confusion matrix.
- Robustness failures with reproduction paths.
- p50/p95 and available token/tool/cost deltas.
- Static context and package-size findings.
- Exact remediation and the cases that must be rerun.

If subjective judgment is required, label it separately from deterministic graders and include the judge prompt, model, rubric, and raw evidence.

## Built-in Graders

Use these grader types in suites:

- `exit_code`
- `stdout_contains`
- `stdout_not_contains`
- `response_contains`
- `response_not_contains`
- `response_regex`
- `file_exists`
- `json_path_equals`
- `max_duration_ms`
- `max_output_bytes`
- `skill_invoked`

Use `critical` for release-defining behavior, `high` for major behavior, `medium` for quality risks, and `info` for observations.

## Performance Rules

- Compare performance against the same baseline and task mix.
- Report sample counts with percentiles.
- Do not trade correctness for speed without an explicit product threshold.
- Flag large `SKILL.md` bodies and packages because they increase discovery and context cost.
- Prefer small routing instructions in `SKILL.md`; move detailed knowledge to references and deterministic work to scripts.
- When token, cost, or tool-call trace is unavailable, report `null`, not zero.
- Investigate both regressions: “slower for the same quality” and “faster because work was skipped.”

## Self-Test

After modifying this evaluator, run:

```bash
python3 "$EVAL_SKILL_DIR/scripts/run_harness.py"
```

The self-test covers static good/bad fixtures, suite validation, paired execution, trigger quality, timeouts, output limits, workspace isolation, reporting, and a performance budget.

## Reference Map

- Evaluation design and release logic: [evaluation-method.md](references/evaluation-method.md)
- Suite fields and graders: [suite-format.md](references/suite-format.md)
- Runner integration: [runner-adapters.md](references/runner-adapters.md)
- Execution boundary and threat model: [security-policy.md](references/security-policy.md)
- Machine-readable suite schema: [eval-suite.schema.json](references/eval-suite.schema.json)
- Machine-readable report schema: [eval-report.schema.json](references/eval-report.schema.json)
