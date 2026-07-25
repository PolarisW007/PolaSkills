# Security Policy

## Threat Model

The evaluator may inspect Agent-generated instructions and execute a caller-provided runner. Generated Skill code can be buggy or malicious. The harness limits common failures but is not an operating-system sandbox.

## Default Boundary

- Static inspection is read-only.
- Dynamic execution is disabled unless `--execute` is supplied.
- Runner command is an argv array; shell expansion is unavailable.
- Each job gets a fresh workspace.
- Output directory cannot already exist or be inside an evaluated Skill.
- Concurrency, timeout, and output are bounded.
- Only a small environment allowlist is inherited.
- Credential-like configured environment names are refused.
- Grader paths must remain inside the run evidence directory.
- Static traversal does not follow external symlinks.

## What Static Inspection Detects

- Broad destructive shell removal.
- Remote content piped to a shell.
- Python dynamic execution and subprocess shell mode.
- Potential committed provider/GitHub token and private-key material.
- Network and common subprocess calls without timeout.
- Unbounded loops.
- Syntax errors, missing references, and escaping links/symlinks.

Pattern scanning is a review aid, not proof of safety. Obfuscated behavior, native binaries, imported dependencies, and indirect commands may evade it.

## Running Untrusted Skills

Use an additional container, VM, sandbox profile, or restricted operating-system account when the runner may execute candidate scripts. Restrict:

- Network egress.
- Filesystem mounts.
- Process count, CPU, memory, and disk.
- Device access.
- Cloud metadata endpoints.
- User credential directories.

Do not mount SSH keys, browser profiles, cloud configuration, production databases, or real user files.

## Evidence Handling

Runner stdout, stderr, response, and trace are untrusted evidence. Keep them in the explicit output directory and treat them as potentially sensitive. Use synthetic fixtures. Review before sharing.

The aggregate report imports only bounded previews and numeric/boolean trace fields. It does not import arbitrary trace payloads.

## Prohibited Use

Do not use the harness to:

- Test destructive behavior against production.
- Send real messages or make purchases without separate authorization.
- Bypass authentication, CAPTCHA, rate limits, or policy controls.
- Store secrets in suite, runner, Skill, or report files.
- Claim isolation stronger than the operating environment actually provides.
