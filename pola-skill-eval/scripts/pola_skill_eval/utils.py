"""Shared helpers with no third-party dependencies."""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Iterable


MAX_JSON_BYTES = 10 * 1024 * 1024
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def load_json(path: Path, max_bytes: int = MAX_JSON_BYTES) -> Any:
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"JSON file exceeds {max_bytes} bytes: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def validate_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path must be a non-empty string")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"path must remain relative to the run directory: {value}")
    normalized = candidate.as_posix()
    if normalized in {".", ""}:
        raise ValueError("path must identify a file")
    return normalized


def percentile(values: Iterable[float], percent: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percent / 100.0
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def estimated_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def parse_frontmatter(text: str) -> tuple[dict[str, str], str, list[str]]:
    errors: list[str] = []
    if not text.startswith("---\n"):
        return {}, text, ["SKILL.md must start with YAML frontmatter delimited by ---"]
    closing = text.find("\n---\n", 4)
    if closing < 0:
        return {}, text, ["SKILL.md frontmatter has no closing --- delimiter"]
    header_text = text[4:closing]
    body = text[closing + 5 :]
    header: dict[str, str] = {}
    for line_number, raw_line in enumerate(header_text.splitlines(), start=2):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line[:1].isspace() or ":" not in raw_line:
            errors.append(
                f"unsupported or malformed frontmatter at line {line_number}: {raw_line!r}"
            )
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            errors.append(f"empty frontmatter key/value at line {line_number}")
            continue
        if key in header:
            errors.append(f"duplicate frontmatter key {key!r} at line {line_number}")
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        header[key] = value
    return header, body, errors


def finding(
    code: str,
    severity: str,
    message: str,
    *,
    path: str | None = None,
    line: int | None = None,
    remediation: str | None = None,
    gate: bool = False,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": message,
        "gate": gate,
    }
    if path is not None:
        item["path"] = path
    if line is not None:
        item["line"] = line
    if remediation is not None:
        item["remediation"] = remediation
    return item


def safe_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    allowed_exact = {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PYTHONPATH",
        "PYTHONHOME",
        "SYSTEMROOT",
        "TMPDIR",
        "TEMP",
        "TMP",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed_exact}
    if extra:
        env.update(extra)
    return env
