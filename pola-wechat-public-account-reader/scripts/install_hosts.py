#!/usr/bin/env python3
"""Safely expose this canonical skill to supported agent hosts.

Module: host compatibility installer
Purpose: check or create host-specific symlinks without duplicating skill sources
Created: 2026-07-25
Author: Codex
Dependencies: Python 3.10+ standard library
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = 1
HOSTS = ("codex", "claude-code", "qoder", "qoder-work")
EXIT_READY = 0
EXIT_CHANGES_NEEDED = 1
EXIT_REFUSED = 2
EXIT_FAILED = 3
EXIT_INVALID_ARGUMENTS = 64


class JsonArgumentParser(argparse.ArgumentParser):
    """Render argument errors as machine-readable JSON."""

    def error(self, message: str) -> None:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "invalid_arguments",
                    "error": message,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(EXIT_INVALID_ARGUMENTS)


class InstallRefused(RuntimeError):
    """Raised when a target changed after preflight and must not be replaced."""


def _parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        description=(
            "Check host skill links, or create them only when --apply is explicit."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="create missing links; without this flag the command is read-only",
    )
    parser.add_argument(
        "--repair-broken",
        action="store_true",
        help=(
            "allow --apply to replace a broken symlink at the exact managed target "
            "name; unrelated links are never scanned or changed"
        ),
    )
    parser.add_argument(
        "--home",
        help=(
            "override the user home used for every host; intended for tests and "
            "isolated installs, and takes precedence over CODEX_HOME"
        ),
    )
    parser.add_argument(
        "--host",
        action="append",
        choices=HOSTS,
        help="limit work to one host; repeat to select more than one",
    )
    return parser


def _canonical_source() -> Path:
    source = Path(__file__).resolve(strict=True).parents[1]
    skill_file = source / "SKILL.md"
    if not skill_file.is_file():
        raise FileNotFoundError(f"canonical SKILL.md is missing: {skill_file}")
    return source


def _absolute_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _selected_hosts(values: list[str] | None) -> tuple[str, ...]:
    if not values:
        return HOSTS
    return tuple(dict.fromkeys(values))


def _target_paths(
    *,
    home: Path,
    explicit_home: bool,
    skill_name: str,
) -> dict[str, Path]:
    codex_home_value = None if explicit_home else os.environ.get("CODEX_HOME")
    codex_home = (
        _absolute_path(codex_home_value)
        if codex_home_value
        else home / ".codex"
    )
    return {
        "codex": codex_home / "skills" / skill_name,
        "claude-code": home / ".claude" / "skills" / skill_name,
        "qoder": home / ".qoder" / "skills" / skill_name,
        "qoder-work": home / ".qoderwork" / "skills" / skill_name,
    }


def _base_result(host: str, target: Path) -> dict[str, Any]:
    return {
        "host": host,
        "target": str(target),
        "state": "unknown",
        "planned_action": "none",
        "action": "none",
        "changed": False,
    }


def _inspect_target(host: str, target: Path, source: Path) -> dict[str, Any]:
    result = _base_result(host, target)
    try:
        mode = target.lstat().st_mode
    except FileNotFoundError:
        result["state"] = "missing"
        return result
    except OSError as exc:
        result.update(
            state="inspection_error",
            error=f"{type(exc).__name__}: {exc}",
        )
        return result

    if stat.S_ISLNK(mode):
        try:
            link_value = os.readlink(target)
        except OSError as exc:
            result.update(
                state="inspection_error",
                error=f"{type(exc).__name__}: {exc}",
            )
            return result
        result["link_value"] = link_value
        try:
            resolved = target.resolve(strict=True)
        except FileNotFoundError:
            result["state"] = "broken_symlink"
            return result
        except RuntimeError as exc:
            result.update(
                state="broken_symlink",
                error=f"{type(exc).__name__}: {exc}",
            )
            return result
        except OSError as exc:
            if exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
                result.update(
                    state="broken_symlink",
                    error=f"{type(exc).__name__}: {exc}",
                )
            else:
                result.update(
                    state="inspection_error",
                    error=f"{type(exc).__name__}: {exc}",
                )
            return result

        result["resolved_target"] = str(resolved)
        result["state"] = (
            "ready" if resolved == source else "conflict_symlink"
        )
        return result

    result["state"] = (
        "conflict_directory" if stat.S_ISDIR(mode) else "conflict_file"
    )
    return result


def _plan(result: dict[str, Any], *, repair_broken: bool) -> None:
    state = result["state"]
    if state == "missing":
        result["planned_action"] = "install"
        return
    if state == "broken_symlink":
        if repair_broken:
            result["planned_action"] = "repair"
        else:
            result.update(
                planned_action="refuse",
                reason="broken symlink requires --repair-broken together with --apply",
            )
        return
    if state.startswith("conflict_"):
        result.update(
            planned_action="refuse",
            reason="existing target is never overwritten",
        )
        return
    if state == "inspection_error":
        result.update(
            planned_action="fail",
            reason="target could not be inspected safely",
        )


def _is_blocker(result: dict[str, Any]) -> bool:
    return result["planned_action"] in {"refuse", "fail"}


def _create_missing_link(target: Path, source: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(source, target_is_directory=True)
        return True
    except FileExistsError as exc:
        current = _inspect_target("", target, source)
        if current["state"] == "ready":
            return False
        raise InstallRefused(
            f"target appeared after preflight and was not replaced: {target}"
        ) from exc


def _repair_broken_link(
    target: Path,
    source: Path,
    expected_link_value: str,
) -> bool:
    current = _inspect_target("", target, source)
    if current["state"] == "ready":
        return False
    if (
        current["state"] != "broken_symlink"
        or current.get("link_value") != expected_link_value
    ):
        raise InstallRefused(
            f"broken target changed after preflight and was not replaced: {target}"
        )

    target.unlink()
    try:
        target.symlink_to(source, target_is_directory=True)
    except OSError as exc:
        rollback_error = None
        try:
            if not target.is_symlink() and not target.exists():
                target.symlink_to(
                    expected_link_value,
                    target_is_directory=True,
                )
        except OSError as rollback_exc:
            rollback_error = f"; rollback failed: {rollback_exc}"
        raise OSError(
            f"could not replace broken link {target}: {exc}"
            f"{rollback_error or ''}"
        ) from exc
    return True


def _payload(
    *,
    source: Path,
    home: Path,
    mode: str,
    repair_broken: bool,
    results: list[dict[str, Any]],
    status: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "mode": mode,
        "skill_name": source.name,
        "source": str(source),
        "home": str(home),
        "repair_broken": repair_broken,
        "changed": any(result["changed"] for result in results),
        "results": results,
    }


def _check_status(results: list[dict[str, Any]]) -> tuple[str, int]:
    if any(result["planned_action"] == "fail" for result in results):
        return "failed", EXIT_FAILED
    if any(_is_blocker(result) for result in results):
        return "refused", EXIT_REFUSED
    if any(result["planned_action"] in {"install", "repair"} for result in results):
        return "changes_needed", EXIT_CHANGES_NEEDED
    return "ready", EXIT_READY


def run(argv: Sequence[str] | None = None) -> tuple[dict[str, Any], int]:
    args = _parser().parse_args(argv)
    explicit_home = args.home is not None
    home = _absolute_path(args.home) if explicit_home else Path.home().resolve()
    mode = "apply" if args.apply else "check"

    try:
        source = _canonical_source()
    except OSError as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "mode": mode,
            "error": f"{type(exc).__name__}: {exc}",
            "changed": False,
            "results": [],
        }
        return result, EXIT_FAILED

    targets = _target_paths(
        home=home,
        explicit_home=explicit_home,
        skill_name=source.name,
    )
    results = [
        _inspect_target(host, targets[host], source)
        for host in _selected_hosts(args.host)
    ]
    for result in results:
        _plan(result, repair_broken=args.repair_broken)

    if not args.apply:
        status, exit_code = _check_status(results)
        return (
            _payload(
                source=source,
                home=home,
                mode=mode,
                repair_broken=args.repair_broken,
                results=results,
                status=status,
            ),
            exit_code,
        )

    if any(_is_blocker(result) for result in results):
        status, exit_code = _check_status(results)
        for result in results:
            if result["planned_action"] in {"install", "repair"}:
                result["reason"] = "blocked by preflight; no targets were changed"
        return (
            _payload(
                source=source,
                home=home,
                mode=mode,
                repair_broken=args.repair_broken,
                results=results,
                status=status,
            ),
            exit_code,
        )

    operation_failed = False
    operation_refused = False
    for result in results:
        try:
            if result["planned_action"] == "install":
                changed = _create_missing_link(Path(result["target"]), source)
                result.update(
                    action="installed" if changed else "already_ready",
                    changed=changed,
                    state_after="ready",
                )
            elif result["planned_action"] == "repair":
                changed = _repair_broken_link(
                    Path(result["target"]),
                    source,
                    result["link_value"],
                )
                result.update(
                    action="repaired" if changed else "already_ready",
                    changed=changed,
                    state_after="ready",
                )
        except InstallRefused as exc:
            operation_refused = True
            result.update(action="refused", error=str(exc))
        except OSError as exc:
            operation_failed = True
            result.update(
                action="failed",
                error=f"{type(exc).__name__}: {exc}",
            )

    if operation_failed:
        status, exit_code = "failed", EXIT_FAILED
    elif operation_refused:
        status, exit_code = "refused", EXIT_REFUSED
    elif any(result["changed"] for result in results):
        status, exit_code = "updated", EXIT_READY
    else:
        status, exit_code = "ready", EXIT_READY
    return (
        _payload(
            source=source,
            home=home,
            mode=mode,
            repair_broken=args.repair_broken,
            results=results,
            status=status,
        ),
        exit_code,
    )


def main(argv: Sequence[str] | None = None) -> int:
    payload, exit_code = run(argv)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
