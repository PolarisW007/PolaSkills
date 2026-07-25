# Evaluation Method

## What Good Evidence Looks Like

A useful Skill should improve an Agent's behavior on representative work while preserving safety, non-trigger behavior, and acceptable efficiency. Evidence should be:

- **Comparative**: candidate versus no-Skill baseline and, when relevant, old version.
- **Observable**: tied to files, structured output, response properties, tool traces, or bounded timing.
- **Repeatable**: same case contract, isolated workspace, explicit limits, multiple trials.
- **Representative**: core tasks, adjacent tasks, edge cases, failure paths, and holdouts.
- **Auditable**: raw per-run evidence plus a stable aggregate report.

Static quality is necessary but insufficient. A polished `SKILL.md` can over-trigger, omit needed context, select the wrong tool, or make execution slower without improving results.

## Evaluation Layers

### Layer 1: Package and Instruction Integrity

Check:

- Valid Skill identity and trigger description.
- Compact routing instructions and progressive disclosure.
- Reachable local references.
- Script syntax and deterministic entry points.
- No generated outputs, caches, credentials, or unsafe links.
- Bounded network, subprocess, loop, file, and logging behavior.

This layer answers “can the Skill be loaded and reviewed safely?” It does not answer “does it work?”

### Layer 2: Trigger and Task Lift

Run the same prompts against:

- `baseline`: host without the Skill.
- `candidate`: host with the candidate Skill.
- `old`: optional prior version.

Use positive triggers, negative triggers, core tasks, and adjacent-domain coexistence cases. Require candidate task lift over baseline so the Skill must add value instead of merely passing easy prompts.

### Layer 3: Robustness

Probe:

- Missing, malformed, empty, oversized, duplicated, and reordered inputs.
- Tool unavailable, network unavailable, permission denied, partial response, timeout.
- Repeated execution and idempotence.
- Safe fallback and honest uncertainty.
- Output caps and cleanup behavior.

Do not use destructive production probes. Simulate faults with fixtures or runner controls.

### Layer 4: Efficiency

Measure:

- `SKILL.md` lines and approximate always-loaded tokens.
- Package files and bytes.
- End-to-end duration p50/p95.
- Output bytes.
- Token, cost, and tool-call count when the host provides trace data.

Interpret performance jointly with quality. A faster run that skipped required work is a functional failure.

### Layer 5: Generalization

Keep a holdout split not used to write the Skill. Vary nouns, ordering, file shape, and nearby intent. Avoid suites that only match exact phrases copied from `SKILL.md`.

## Grader Hierarchy

Prefer graders in this order:

1. Deterministic state or file checks.
2. Structured JSON assertions.
3. Exit code and bounded timing.
4. Exact semantic markers or regex.
5. External rubric judge, only when deterministic evidence is inadequate.

When using a judge, record its prompt, rubric, model/version, temperature, raw response, and disagreement handling. Never mix subjective and deterministic grades without labeling them.

## Hard Gates and Soft Signals

Hard gates include:

- Invalid Skill package.
- Critical security finding.
- Runner integrity failure.
- Critical task or trigger grader failure.
- Release performance budget violation.

Soft signals include:

- Description could be clearer.
- Package is larger than preferred.
- Minor stylistic inconsistency.
- Small performance variance inside budget.

Never convert hard gates into weighted deductions. Report dimension scores only as navigation aids.

## Statistical Interpretation

- Report the number of cases and trials.
- Use success rate for the full matrix.
- Use `pass@k` to answer whether at least one of k attempts succeeds.
- Use `pass^k` to answer whether all k attempts succeed.
- Use p50/p95 for duration and size; do not report percentiles without sample counts.
- Treat fewer than three candidate observations as low-confidence.
- Prefer at least three trials for nondeterministic Agent hosts.
- Do not claim statistical significance from a tiny suite.

## Trigger Metrics

From `should_trigger` and trace `skill_invoked`:

- True positive: should trigger and did.
- False positive: should not trigger but did.
- False negative: should trigger but did not.
- True negative: should not trigger and did not.

Report precision, recall, and F1. A Skill that always triggers can have high recall and terrible precision; a Skill that never triggers can look fast while being useless.

## Suggested Release Thresholds

Tune thresholds by risk, but a normal starting point is:

- Candidate critical grader pass rate: 100%.
- Overall candidate success: at least 80%.
- Success uplift over baseline: at least 10 percentage points.
- Trigger F1: at least 0.8.
- No critical static/security finding.
- p95 within the product budget.

For destructive, financial, security, production, or user-message actions, require stricter per-case gates and human review.

## Release Review Template

1. Decision and confidence.
2. Hard gates and failures.
3. Candidate versus baseline/old task success.
4. Trigger confusion matrix.
5. Robustness failures and reproduction.
6. Performance and resource deltas.
7. Static context/package findings.
8. Remediation and exact rerun scope.
