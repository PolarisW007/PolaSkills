"""Static inspection for Agent Skill packages."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .utils import estimated_tokens, finding, is_within, parse_frontmatter


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
ALLOWED_FRONTMATTER = {
    "codex": {"name", "description"},
    "agent-skills": {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
    },
    "generic": None,
}
TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".txt",
}


def _line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _local_links(text: str) -> list[tuple[str, int]]:
    links: list[tuple[str, int]] = []
    for match in LINK_RE.finditer(text):
        raw = match.group(1).strip()
        if raw.startswith("<") and raw.endswith(">"):
            raw = raw[1:-1].strip()
        target = raw.split("#", 1)[0].strip()
        if not target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            continue
        links.append((unquote(target), _line_for_offset(text, match.start(1))))
    return links


def _python_findings(path: Path, relative: str, text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    try:
        tree = ast.parse(text, filename=relative)
    except SyntaxError as exc:
        findings.append(
            finding(
                "PYTHON_SYNTAX",
                "critical",
                exc.msg,
                path=relative,
                line=exc.lineno,
                remediation="Fix Python syntax before evaluation.",
                gate=True,
            )
        )
        return findings

    def call_name(node: ast.Call) -> str:
        current: ast.AST = node.func
        names: list[str] = []
        while isinstance(current, ast.Attribute):
            names.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            names.append(current.id)
        return ".".join(reversed(names))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = call_name(node)
            if name in {"eval", "exec", "os.system"}:
                findings.append(
                    finding(
                        "DYNAMIC_EXECUTION",
                        "high",
                        f"Dynamic execution call {name} requires a documented boundary.",
                        path=relative,
                        line=getattr(node, "lineno", None),
                        remediation="Use structured parsing or argv-based subprocess calls.",
                    )
                )
            if name.startswith("subprocess."):
                shell_true = any(
                    keyword.arg == "shell"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                    for keyword in node.keywords
                )
                if shell_true:
                    findings.append(
                        finding(
                            "SUBPROCESS_SHELL",
                            "critical",
                            "subprocess shell execution permits command injection.",
                            path=relative,
                            line=getattr(node, "lineno", None),
                            remediation="Pass an argv list with shell disabled.",
                            gate=True,
                        )
                    )
                timeout_present = any(
                    keyword.arg == "timeout" for keyword in node.keywords
                )
                if name in {
                    "subprocess.run",
                    "subprocess.call",
                    "subprocess.check_call",
                    "subprocess.check_output",
                } and not timeout_present:
                    findings.append(
                        finding(
                            "SUBPROCESS_NO_TIMEOUT",
                            "medium",
                            f"{name} has no explicit timeout.",
                            path=relative,
                            line=getattr(node, "lineno", None),
                            remediation="Set a bounded timeout and handle expiration.",
                        )
                    )
            if name in {"urllib.request.urlopen", "requests.get", "requests.post"}:
                if not any(keyword.arg == "timeout" for keyword in node.keywords):
                    findings.append(
                        finding(
                            "NETWORK_NO_TIMEOUT",
                            "high",
                            f"{name} has no explicit timeout.",
                            path=relative,
                            line=getattr(node, "lineno", None),
                            remediation="Set connection/read timeouts and handle failure.",
                        )
                    )
        if isinstance(node, (ast.While, ast.For)):
            is_unbounded = isinstance(node, ast.While) and isinstance(
                node.test, ast.Constant
            ) and node.test.value is True
            if is_unbounded and not any(
                isinstance(child, ast.Break) for child in ast.walk(node)
            ):
                findings.append(
                    finding(
                        "UNBOUNDED_LOOP",
                        "medium",
                        "Potentially unbounded loop has no break statement.",
                        path=relative,
                        line=getattr(node, "lineno", None),
                        remediation="Add a bound, deadline, or explicit break condition.",
                    )
                )
    return findings


def _shell_findings(path: Path, relative: str, text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    try:
        completed = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        findings.append(
            finding(
                "SHELL_CHECK_FAILED",
                "high",
                f"Could not run bash syntax check: {exc}",
                path=relative,
                remediation="Validate the shell script in a supported environment.",
            )
        )
    else:
        if completed.returncode != 0:
            findings.append(
                finding(
                    "SHELL_SYNTAX",
                    "critical",
                    completed.stderr.strip() or "bash syntax check failed",
                    path=relative,
                    remediation="Fix shell syntax before evaluation.",
                    gate=True,
                )
            )

    broad_remove = re.compile(
        r"\brm\s+(?:-[A-Za-z]*r[A-Za-z]*f[A-Za-z]*|-[A-Za-z]*f[A-Za-z]*r[A-Za-z]*)\s+(?:/|~|\$\{?HOME\}?)"
    )
    pipe_shell = re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:ba|z)?sh\b")
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if broad_remove.search(line):
            findings.append(
                finding(
                    "BROAD_DESTRUCTIVE_REMOVE",
                    "critical",
                    "Broad recursive removal can destroy user or system data.",
                    path=relative,
                    line=number,
                    remediation="Use an explicit validated task-specific path.",
                    gate=True,
                )
            )
        if pipe_shell.search(line):
            findings.append(
                finding(
                    "REMOTE_PIPE_TO_SHELL",
                    "critical",
                    "Remote content is piped directly to a shell.",
                    path=relative,
                    line=number,
                    remediation="Download, verify, and execute a pinned artifact separately.",
                    gate=True,
                )
            )
        if re.search(r"(^|\s)sudo(\s|$)", line):
            findings.append(
                finding(
                    "SUDO_USAGE",
                    "high",
                    "Privileged command requires explicit authorization and rollback.",
                    path=relative,
                    line=number,
                    remediation="Remove sudo or document the exact privileged boundary.",
                )
            )
    return findings


def _secret_findings(relative: str, text: str) -> list[dict[str, Any]]:
    fragments = [
        ("OPENAI_TOKEN_LITERAL", "sk" + r"-[A-Za-z0-9_-]{20,}"),
        ("GITHUB_TOKEN_LITERAL", "ghp" + r"_[A-Za-z0-9]{20,}"),
        (
            "PRIVATE_KEY_LITERAL",
            "-" * 5 + r"BEGIN [A-Z0-9 ]+ PRIVATE KEY" + "-" * 5,
        ),
    ]
    findings: list[dict[str, Any]] = []
    for code, pattern in fragments:
        match = re.search(pattern, text)
        if match:
            findings.append(
                finding(
                    code,
                    "critical",
                    "Potential credential material is committed in the Skill.",
                    path=relative,
                    line=_line_for_offset(text, match.start()),
                    remediation="Remove the secret, rotate it, and use runtime injection.",
                    gate=True,
                )
            )
    return findings


def _dimension_scores(findings: list[dict[str, Any]]) -> dict[str, int]:
    scores = {
        "usability": 100,
        "robustness": 100,
        "efficiency": 100,
        "maintainability": 100,
        "security": 100,
    }
    severity_cost = {"critical": 45, "high": 20, "medium": 8, "low": 3, "info": 0}
    mappings = {
        "security": {
            "BROAD_DESTRUCTIVE_REMOVE",
            "REMOTE_PIPE_TO_SHELL",
            "SUBPROCESS_SHELL",
            "DYNAMIC_EXECUTION",
            "SUDO_USAGE",
            "OPENAI_TOKEN_LITERAL",
            "GITHUB_TOKEN_LITERAL",
            "PRIVATE_KEY_LITERAL",
        },
        "robustness": {
            "PYTHON_SYNTAX",
            "SHELL_SYNTAX",
            "SUBPROCESS_NO_TIMEOUT",
            "NETWORK_NO_TIMEOUT",
            "UNBOUNDED_LOOP",
            "REFERENCE_MISSING",
            "REFERENCE_ESCAPE",
        },
        "efficiency": {
            "SKILL_BODY_LARGE",
            "SKILL_CONTEXT_LARGE",
            "PACKAGE_LARGE",
            "TOO_MANY_FILES",
            "FILE_TOO_LARGE",
        },
        "usability": {
            "SKILL_MISSING",
            "FRONTMATTER_INVALID",
            "NAME_INVALID",
            "NAME_DIRECTORY_MISMATCH",
            "DESCRIPTION_WEAK",
            "NO_USAGE_WORKFLOW",
        },
    }
    for item in findings:
        cost = severity_cost.get(item["severity"], 0)
        dimensions = [
            dimension for dimension, codes in mappings.items() if item["code"] in codes
        ]
        if not dimensions:
            dimensions = ["maintainability"]
        for dimension in dimensions:
            scores[dimension] = max(0, scores[dimension] - cost)
    return scores


def inspect_skill(
    skill_path: str | Path,
    *,
    profile: str = "auto",
    max_file_bytes: int = 1024 * 1024,
    max_package_bytes: int = 10 * 1024 * 1024,
    max_files: int = 200,
) -> dict[str, Any]:
    started = time.perf_counter()
    root = Path(skill_path).expanduser().resolve(strict=False)
    findings: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    total_bytes = 0

    if profile not in {"auto", *ALLOWED_FRONTMATTER.keys()}:
        raise ValueError(f"unsupported profile: {profile}")
    if not root.is_dir():
        return {
            "schema_version": "1.0",
            "skill_path": str(root),
            "profile": profile,
            "status": "reject",
            "gates": [{"name": "skill_directory", "passed": False}],
            "findings": [
                finding(
                    "SKILL_DIRECTORY_MISSING",
                    "critical",
                    "Skill directory does not exist.",
                    path=str(root),
                    gate=True,
                )
            ],
            "inventory": {"files": 0, "bytes": 0, "items": []},
            "dimensions": {
                "usability": 0,
                "robustness": 0,
                "efficiency": 0,
                "maintainability": 0,
                "security": 100,
            },
            "metrics": {
                "inspection_ms": round((time.perf_counter() - started) * 1000, 3)
            },
        }

    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        current_path = Path(current)
        for directory in list(dirnames):
            directory_path = current_path / directory
            relative = directory_path.relative_to(root).as_posix()
            if directory_path.is_symlink():
                inventory.append({"path": relative, "type": "symlink", "bytes": 0})
                try:
                    escapes = not is_within(
                        directory_path.resolve(strict=False), root
                    )
                except (OSError, RuntimeError):
                    escapes = True
                if escapes:
                    findings.append(
                        finding(
                            "SYMLINK_ESCAPE",
                            "critical",
                            "Symlink points outside the Skill directory.",
                            path=relative,
                            remediation="Package real files or an internal relative symlink.",
                            gate=True,
                        )
                    )
                dirnames.remove(directory)
        for filename in filenames:
            path = current_path / filename
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                inventory.append({"path": relative, "type": "symlink", "bytes": 0})
                try:
                    escapes = not is_within(path.resolve(strict=False), root)
                except (OSError, RuntimeError):
                    escapes = True
                if escapes:
                    findings.append(
                        finding(
                            "SYMLINK_ESCAPE",
                            "critical",
                            "Symlink points outside the Skill directory.",
                            path=relative,
                            remediation="Package real files or an internal relative symlink.",
                            gate=True,
                        )
                    )
                continue
            if not path.is_file():
                inventory.append({"path": relative, "type": "special", "bytes": 0})
                findings.append(
                    finding(
                        "SPECIAL_FILE",
                        "high",
                        "Special files are not portable Skill resources.",
                        path=relative,
                        remediation="Remove sockets, devices, and named pipes.",
                    )
                )
                continue
            size = path.stat().st_size
            total_bytes += size
            inventory.append({"path": relative, "type": "file", "bytes": size})
            if size > max_file_bytes:
                findings.append(
                    finding(
                        "FILE_TOO_LARGE",
                        "high",
                        f"File exceeds the {max_file_bytes}-byte inspection limit.",
                        path=relative,
                        remediation="Split or remove large generated/binary content.",
                    )
                )
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                findings.append(
                    finding(
                        "TEXT_DECODE_FAILED",
                        "medium",
                        "Text-like file is not valid UTF-8.",
                        path=relative,
                        remediation="Encode portable text resources as UTF-8.",
                    )
                )
                continue
            findings.extend(_secret_findings(relative, text))
            if path.suffix.lower() == ".py":
                findings.extend(_python_findings(path, relative, text))
            elif path.suffix.lower() in {".sh", ".bash"}:
                findings.extend(_shell_findings(path, relative, text))

    if len(inventory) > max_files:
        findings.append(
            finding(
                "TOO_MANY_FILES",
                "medium",
                f"Package has {len(inventory)} items; expected at most {max_files}.",
                remediation="Remove generated artifacts or split optional resources.",
            )
        )
    if total_bytes > max_package_bytes:
        findings.append(
            finding(
                "PACKAGE_LARGE",
                "high",
                f"Package has {total_bytes} bytes; expected at most {max_package_bytes}.",
                remediation="Remove caches, outputs, vendored dependencies, or large assets.",
            )
        )

    skill_file = root / "SKILL.md"
    header: dict[str, str] = {}
    body = ""
    if not skill_file.is_file():
        findings.append(
            finding(
                "SKILL_MISSING",
                "critical",
                "SKILL.md is missing.",
                path="SKILL.md",
                remediation="Add the required Skill entry file.",
                gate=True,
            )
        )
    elif skill_file.stat().st_size <= max_file_bytes:
        try:
            raw_skill = skill_file.read_bytes()
            text = raw_skill.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(
                finding(
                    "SKILL_UTF8_INVALID",
                    "critical",
                    "SKILL.md is not valid UTF-8.",
                    path="SKILL.md",
                    remediation="Encode SKILL.md as UTF-8.",
                    gate=True,
                )
            )
            text = raw_skill.decode("utf-8", errors="replace")
        except OSError as exc:
            findings.append(
                finding(
                    "SKILL_READ_FAILED",
                    "critical",
                    f"SKILL.md could not be read: {exc}",
                    path="SKILL.md",
                    remediation="Fix file permissions and retry.",
                    gate=True,
                )
            )
            text = ""
        header, body, parse_errors = parse_frontmatter(text)
        for error in parse_errors:
            findings.append(
                finding(
                    "FRONTMATTER_INVALID",
                    "critical",
                    error,
                    path="SKILL.md",
                    remediation="Use flat YAML frontmatter delimited by ---.",
                    gate=True,
                )
            )
        detected_profile = profile
        if profile == "auto":
            if set(header) <= {"name", "description"}:
                detected_profile = "codex"
            elif set(header) <= (ALLOWED_FRONTMATTER["agent-skills"] or set()):
                detected_profile = "agent-skills"
            else:
                detected_profile = "generic"
        profile = detected_profile
        for required in ("name", "description"):
            if not header.get(required):
                findings.append(
                    finding(
                        "FRONTMATTER_REQUIRED",
                        "critical",
                        f"Required frontmatter field {required!r} is missing.",
                        path="SKILL.md",
                        remediation=f"Add a concise {required} value.",
                        gate=True,
                    )
                )
        name = header.get("name", "")
        if name and (len(name) > 64 or not NAME_RE.fullmatch(name)):
            findings.append(
                finding(
                    "NAME_INVALID",
                    "critical",
                    "Skill name must be lowercase hyphen-case and at most 64 characters.",
                    path="SKILL.md",
                    remediation="Rename the Skill and directory with lowercase hyphen-case.",
                    gate=True,
                )
            )
        if name and name != root.name:
            findings.append(
                finding(
                    "NAME_DIRECTORY_MISMATCH",
                    "critical",
                    f"Frontmatter name {name!r} does not match directory {root.name!r}.",
                    path="SKILL.md",
                    remediation="Make the name and directory identical.",
                    gate=True,
                )
            )
        description = header.get("description", "")
        if description and (len(description) < 40 or len(description) > 1024):
            findings.append(
                finding(
                    "DESCRIPTION_WEAK",
                    "medium",
                    "Description should clearly state capabilities and triggering situations.",
                    path="SKILL.md",
                    remediation="Describe what the Skill does and when it should be used.",
                )
            )
        allowed = ALLOWED_FRONTMATTER.get(profile)
        if allowed is not None:
            for extra in sorted(set(header) - allowed):
                findings.append(
                    finding(
                        "FRONTMATTER_EXTRA",
                        "high",
                        f"Field {extra!r} is not allowed by the {profile} profile.",
                        path="SKILL.md",
                        remediation="Remove the field or choose the correct profile.",
                    )
                )
        body_lines = len(body.splitlines())
        body_tokens = estimated_tokens(body)
        if body_lines > 500:
            findings.append(
                finding(
                    "SKILL_BODY_LARGE",
                    "high",
                    f"SKILL.md body has {body_lines} lines; target at most 500.",
                    path="SKILL.md",
                    remediation="Move detailed material to references and keep routing concise.",
                )
            )
        if body_tokens > 5000:
            findings.append(
                finding(
                    "SKILL_CONTEXT_LARGE",
                    "medium",
                    f"SKILL.md body is approximately {body_tokens} tokens.",
                    path="SKILL.md",
                    remediation="Use progressive disclosure to reduce always-loaded context.",
                )
            )
        lower_body = body.lower()
        if not any(word in lower_body for word in ("workflow", "步骤", "run ", "use ")):
            findings.append(
                finding(
                    "NO_USAGE_WORKFLOW",
                    "medium",
                    "No clear workflow or execution guidance was detected.",
                    path="SKILL.md",
                    remediation="Add a short ordered workflow with commands or decision points.",
                )
            )
        for target, line in _local_links(text):
            link_path = Path(target)
            resolved = (root / link_path).resolve(strict=False)
            if link_path.is_absolute() or not is_within(resolved, root):
                findings.append(
                    finding(
                        "REFERENCE_ESCAPE",
                        "critical",
                        f"Local reference escapes the Skill package: {target}",
                        path="SKILL.md",
                        line=line,
                        remediation="Use a relative path inside the Skill directory.",
                        gate=True,
                    )
                )
            elif not resolved.exists():
                findings.append(
                    finding(
                        "REFERENCE_MISSING",
                        "critical",
                        f"Local reference does not exist: {target}",
                        path="SKILL.md",
                        line=line,
                        remediation="Add the resource or correct the link.",
                        gate=True,
                    )
                )
    else:
        findings.append(
            finding(
                "SKILL_FILE_TOO_LARGE",
                "critical",
                "SKILL.md exceeds the safe inspection limit.",
                path="SKILL.md",
                remediation="Reduce SKILL.md and move details to references.",
                gate=True,
            )
        )

    gate_findings = [item for item in findings if item.get("gate")]
    critical = [item for item in findings if item["severity"] == "critical"]
    high = [item for item in findings if item["severity"] == "high"]
    status = "reject" if gate_findings or critical else ("conditional" if high else "pass")
    body_text = body if body else ""
    metrics = {
        "inspection_ms": round((time.perf_counter() - started) * 1000, 3),
        "skill_body_lines": len(body_text.splitlines()),
        "skill_body_estimated_tokens": estimated_tokens(body_text),
        "package_bytes": total_bytes,
        "package_items": len(inventory),
    }
    return {
        "schema_version": "1.0",
        "skill_path": str(root),
        "profile": profile,
        "status": status,
        "gates": [
            {
                "name": "static_critical",
                "passed": not gate_findings and not critical,
                "failures": [item["code"] for item in gate_findings or critical],
            }
        ],
        "identity": {
            "name": header.get("name"),
            "description": header.get("description"),
        },
        "findings": sorted(
            findings,
            key=lambda item: (
                {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(
                    item["severity"], 5
                ),
                item.get("path", ""),
                item.get("line", 0),
                item["code"],
            ),
        ),
        "dimensions": _dimension_scores(findings),
        "inventory": {
            "files": sum(item["type"] == "file" for item in inventory),
            "bytes": total_bytes,
            "items": inventory,
        },
        "metrics": metrics,
    }
