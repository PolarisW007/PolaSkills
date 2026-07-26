#!/usr/bin/env python3
"""Conservatively inventory project and research-report candidates in a workspace."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "coverage",
    "target",
    ".venv",
    "venv",
    "env",
}

MANIFESTS = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Pipfile",
    "poetry.lock",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "composer.json",
    "mix.exs",
    "Package.swift",
    "docker-compose.yml",
    "docker-compose.yaml",
}

ENTRY_FILES = {
    "main.py",
    "app.py",
    "server.py",
    "index.js",
    "index.ts",
    "main.go",
    "main.rs",
    "Makefile",
}

EVIDENCE_NAMES = MANIFESTS | ENTRY_FILES | {
    "SKILL.md",
    "AGENTS.md",
    "Dockerfile",
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory project and research-report candidates without modifying them."
    )
    parser.add_argument("--root", required=True, type=Path, help="Workspace root to scan.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output path. JSON is always printed to stdout.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=3,
        help="Maximum directory depth for project-marker discovery (default: 3).",
    )
    return parser.parse_args()


def relative_depth(root: Path, path: Path) -> int:
    return len(path.relative_to(root).parts)


def is_excluded(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part in EXCLUDED_DIRS for part in parts)


def direct_signals(path: Path) -> dict[str, object]:
    try:
        children = list(path.iterdir())
    except OSError:
        return {
            "git": False,
            "readmes": [],
            "manifests": [],
            "skill": False,
            "entry_files": [],
        }

    names = {child.name for child in children}
    readmes = sorted(
        child.name
        for child in children
        if child.is_file() and child.name.lower().startswith("readme")
    )
    return {
        "git": (path / ".git").exists(),
        "readmes": readmes,
        "manifests": sorted(names & MANIFESTS),
        "skill": "SKILL.md" in names,
        "entry_files": sorted(names & ENTRY_FILES),
    }


def signal_score(signals: dict[str, object]) -> int:
    return (
        (4 if signals["git"] else 0)
        + (3 if signals["skill"] else 0)
        + (3 if signals["manifests"] else 0)
        + (1 if signals["readmes"] else 0)
        + (1 if signals["entry_files"] else 0)
    )


def likely_kind(signals: dict[str, object]) -> str:
    manifests = set(signals["manifests"])
    if signals["skill"]:
        return "agent-skill"
    if "Cargo.toml" in manifests:
        return "rust"
    if "go.mod" in manifests:
        return "go"
    if {"pyproject.toml", "requirements.txt", "Pipfile", "poetry.lock"} & manifests:
        return "python"
    if "package.json" in manifests:
        return "node"
    if {"pom.xml", "build.gradle", "build.gradle.kts"} & manifests:
        return "jvm"
    if signals["git"]:
        return "source-repository"
    return "documentation-or-lightweight-project"


def candidate_directories(root: Path, max_depth: int) -> list[Path]:
    found: list[Path] = []
    for current, dirs, _files in os.walk(root):
        current_path = Path(current)
        depth = relative_depth(root, current_path)
        dirs[:] = [
            item
            for item in dirs
            if item not in EXCLUDED_DIRS
            and not item.startswith(".")
            and depth < max_depth
        ]
        if depth > max_depth or is_excluded(current_path, root):
            continue

        signals = direct_signals(current_path)
        if signal_score(signals) < 3:
            continue

        if current_path == root:
            found.append(current_path)
            continue

        parent_candidates = [item for item in found if item in current_path.parents]
        if parent_candidates and not signals["git"] and not signals["skill"]:
            continue
        found.append(current_path)
    return found


def iter_visible_files(root: Path, suffix: str | None = None) -> Iterable[Path]:
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        dirs[:] = [
            item
            for item in dirs
            if item not in EXCLUDED_DIRS
            and (not item.startswith(".") or item == ".github")
        ]
        for name in files:
            path = current_path / name
            if suffix is None or path.suffix.lower() == suffix:
                yield path


def is_report_candidate(path: Path) -> bool:
    stem = path.stem.lower()
    name = path.name
    return (
        ("research" in stem and ("report" in stem or "analysis" in stem))
        or stem.endswith("analysis-report")
        or "研究报告" in name
        or "深度研究" in name
    )


def evidence_files(project: Path, workspace_root: Path, limit: int = 30) -> list[str]:
    items: list[Path] = []
    for path in iter_visible_files(project):
        depth = relative_depth(project, path)
        lower = path.name.lower()
        if (
            depth <= 3
            and (
                path.name in EVIDENCE_NAMES
                or lower.startswith("readme")
                or lower.startswith("license")
                or any(part in {"test", "tests"} for part in path.parts)
                or ".github" in path.parts
            )
        ):
            items.append(path)
    return [
        item.relative_to(workspace_root).as_posix()
        for item in sorted(items)[:limit]
    ]


def nearest_project(path: Path, projects: list[Path]) -> Path | None:
    owners = [project for project in projects if project == path or project in path.parents]
    if not owners:
        return None
    return max(owners, key=lambda item: len(item.parts))


def build_inventory(root: Path, max_depth: int) -> dict[str, object]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"workspace root is not a directory: {root}")
    if max_depth < 1 or max_depth > 12:
        raise ValueError("--max-depth must be between 1 and 12")

    projects = candidate_directories(root, max_depth)
    reports = sorted(
        path
        for path in iter_visible_files(root, ".md")
        if is_report_candidate(path)
    )
    reports_by_project: dict[Path, list[Path]] = {project: [] for project in projects}
    unassigned: list[Path] = []
    for report in reports:
        owner = nearest_project(report, projects)
        if owner is None:
            unassigned.append(report)
        else:
            reports_by_project[owner].append(report)

    project_items: list[dict[str, object]] = []
    for project in projects:
        signals = direct_signals(project)
        project_items.append(
            {
                "id": "workspace-root"
                if project == root
                else project.relative_to(root).as_posix().replace("/", "--"),
                "name": root.name if project == root else project.name,
                "path": "." if project == root else project.relative_to(root).as_posix(),
                "likely_kind": likely_kind(signals),
                "signals": signals,
                "evidence_files": evidence_files(project, root),
                "candidate_reports": [
                    path.relative_to(root).as_posix()
                    for path in reports_by_project[project]
                ],
                "requires_human_confirmation": True,
            }
        )

    return {
        "schema_version": "1.0",
        "scanned_root": str(root),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "project_candidate_count": len(project_items),
        "report_candidate_count": len(reports),
        "projects": project_items,
        "unassigned_report_candidates": [
            path.relative_to(root).as_posix() for path in unassigned
        ],
        "notes": [
            "Candidates are conservative hints, not the final research-object count.",
            "Review nested repositories, multi-report objects, vendored sources, and exclusions manually.",
        ],
    }


def main() -> int:
    args = parse_args()
    try:
        inventory = build_inventory(args.root, args.max_depth)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2

    text = json.dumps(inventory, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
