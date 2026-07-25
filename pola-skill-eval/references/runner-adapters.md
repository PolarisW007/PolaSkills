# Runner Adapter Contract

The evaluator is host-agnostic. A runner adapter translates one case into a Codex, Claude, local Agent, or test-double execution.

## Configuration

```json
{
  "argv": [
    "agent-runner",
    "--skill",
    "{skill_path}",
    "--prompt-file",
    "{prompt_file}",
    "--workspace",
    "{workspace}",
    "--response-file",
    "{response_file}",
    "--trace-file",
    "{trace_file}"
  ],
  "cwd": "{workspace}",
  "env": {
    "RUNNER_MODE": "offline"
  }
}
```

`argv` is required. It is limited to 128 arguments and 8192 characters per argument. `cwd` and `env` are optional. Commands are executed directly with no shell. When set, `cwd` must resolve inside `{workspace}`; invoke an external adapter by absolute argv path instead of changing into its source tree.

## Placeholders

- `{skill_path}`: selected arm Skill path; empty for `NONE` baseline.
- `{prompt_file}`: UTF-8 prompt file.
- `{workspace}`: fresh writable workspace for this run.
- `{response_file}`: runner should write its final response here.
- `{trace_file}`: runner may write structured trace here.
- `{arm}`: `baseline`, `candidate`, or `old`.
- `{case_id}`: current case ID.
- `{trial}`: 1-based trial number.
- `{config_dir}`: directory containing the runner JSON.

Unknown placeholders are rejected.

## Response

Write the final Agent response as UTF-8 to `{response_file}`. If it is absent, stdout becomes the response evidence. Keep diagnostic logging on stderr.

## Trace

Write JSON when the host exposes the signal:

```json
{
  "skill_invoked": true,
  "tool_calls": 3,
  "usage": {
    "input_tokens": 850,
    "output_tokens": 240,
    "cost_usd": 0.006
  }
}
```

Only these fields are imported into the aggregate report. Do not place prompts, credentials, cookies, or raw tool payloads in trace.

## Baseline

When the CLI receives `--baseline NONE`, `{skill_path}` is empty and `{arm}` is `baseline`. The adapter must disable the candidate Skill for that run. If the host cannot disable Skills, use a clean profile or an explicit baseline wrapper.

Do not simulate baseline output from candidate output. Both arms must actually run.

## Isolation

- Treat `{workspace}` as the only writable task directory.
- Do not reuse Agent conversation state across jobs.
- Do not read other run directories.
- Do not use a global output filename.
- Set the host's own network and permission policy explicitly.

## Environment

The evaluator inherits a minimal execution environment and applies up to 32 configured non-sensitive values of at most 4096 characters each. Environment keys containing token, secret, password, cookie, private, credential, or access/API-key markers are rejected.

If a real runner needs credentials, inject them outside this evaluator in a restricted execution wrapper and ensure its outputs are redacted. Prefer test accounts and synthetic data.

## Exit Semantics

- Exit zero when the runner completed, even if the Agent answer is wrong; graders decide answer quality.
- Exit nonzero for runner or host failure.
- Respect termination signals on timeout/output cap.
- Flush response and trace atomically where possible.

## Adapter Acceptance

Before using a real adapter:

1. Run one dry plan.
2. Run one synthetic case with no credentials.
3. Verify baseline truly disables the Skill.
4. Verify candidate trace records invocation.
5. Verify workspace isolation.
6. Intentionally trigger timeout and confirm the process stops.
7. Intentionally exceed the output limit and confirm truncation.
