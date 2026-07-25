#!/usr/bin/env python3
"""Pola WeChat public-account monitor CLI.

Module: command-line entry
Purpose: validate targets, execute one idempotent poll, or print a safe cron line
Created: 2026-07-24
Author: Codex
Dependencies: Python 3.10+ standard library and pola_wechat_reader package
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import shlex
import sys
from contextlib import contextmanager
from pathlib import Path

from pola_wechat_reader.config import load_config
from pola_wechat_reader.models import ConfigError, RunLocked
from pola_wechat_reader.runner import run_monitor, select_targets


CONFIG_LOAD_DEADLINE_SECONDS = 10


class ProcessDeadlineExceeded(RuntimeError):
    """Raised by the outer alarm so transport retry handlers cannot swallow it."""


@contextmanager
def _process_deadline(seconds: float):
    """Provide a final Unix process guard around DNS, parsing and file I/O."""
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def expire(signum, frame):
        raise ProcessDeadlineExceeded(
            "monitor process exceeded its configured deadline"
        )

    previous_handler = signal.signal(signal.SIGALRM, expire)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor WeChat public accounts through public internet sources."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    validate = subcommands.add_parser("validate", help="validate config without network access")
    validate.add_argument("--config", required=True)
    _private_use_flag(validate)

    run = subcommands.add_parser("run", help="execute one monitor run")
    _common_paths(run)
    run.add_argument("--lookback-hours", type=int)
    run.add_argument("--backfill", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    _target_filters(run)

    cron = subcommands.add_parser("cron", help="print, but do not install, a cron entry")
    _common_paths(cron)
    cron.add_argument(
        "--schedule",
        default="17 * * * *",
        help='five-field cron expression, for example "17 * * * *"',
    )
    cron.add_argument("--lookback-hours", type=int, default=72)
    _target_filters(cron)
    return parser


def _common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--output", required=True)
    _private_use_flag(parser)


def _private_use_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--private-use",
        action="store_true",
        help=(
            "use private_opt_in source policy; this enables only sources "
            "explicitly marked enable_in_private_mode and keeps technical "
            "safety gates"
        ),
    )


def _target_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--target",
        dest="target_ids",
        action="append",
        default=[],
        help="include this exact enabled target ID; repeat for multiple targets",
    )
    parser.add_argument(
        "--exclude-target",
        dest="exclude_target_ids",
        action="append",
        default=[],
        help="exclude this exact target ID; repeat for multiple targets",
    )
    parser.add_argument(
        "--priority",
        dest="priorities",
        choices=("critical", "normal", "low"),
        action="append",
        default=[],
        help="include this priority; repeat to include more than one",
    )


def _validate_command(config_path: str, *, private_use: bool = False) -> int:
    config, warnings = load_config(
        config_path,
        private_use_override=private_use,
    )
    enabled = [target for target in config["targets"] if target["enabled"]]
    selected_sources = [
        (target, source)
        for target in enabled
        for source in target["sources"]
        if source["enabled"]
    ]
    payload = {
        "status": "valid",
        "config": str(Path(config_path).expanduser().resolve()),
        "source_permission_policy": config["defaults"][
            "source_permission_policy"
        ],
        "configured_targets": len(config["targets"]),
        "enabled_targets": len(enabled),
        "disabled_targets": sum(
            not target["enabled"] for target in config["targets"]
        ),
        "enabled_sources": sum(
            source["enabled"] for target in enabled for source in target["sources"]
        ),
        "private_mode_enabled_sources": [
            f"{target['id']}/{source['key']}"
            for target, source in selected_sources
            if source.get("enabled_by_private_mode")
        ],
        "private_mode_content_sources": [
            f"{target['id']}/{source['key']}"
            for target, source in selected_sources
            if source.get("fetch_content_by_private_mode")
        ],
        "permission_overrides": [
            f"{target['id']}/{source['key']}"
            for target, source in selected_sources
            if source.get("permission_override_active")
        ],
        "targets": [
            {
                "id": target["id"],
                "enabled": target["enabled"],
                "priority": target["priority"],
                "enabled_sources": sum(
                    source["enabled"] for source in target["sources"]
                ),
            }
            for target in config["targets"]
        ],
        "warnings": warnings,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _run_command(args: argparse.Namespace) -> int:
    with _process_deadline(CONFIG_LOAD_DEADLINE_SECONDS):
        loaded_config = load_config(
            args.config,
            private_use_override=getattr(args, "private_use", False),
        )
    config, _ = loaded_config
    with _process_deadline(config["defaults"]["max_run_seconds"] + 5):
        result = run_monitor(
            config_path=args.config,
            state_path=args.state,
            output_dir=args.output,
            dry_run=args.dry_run,
            lookback_hours=args.lookback_hours,
            backfill=args.backfill,
            loaded_config=loaded_config,
            target_ids=getattr(args, "target_ids", []),
            exclude_target_ids=getattr(args, "exclude_target_ids", []),
            priorities=getattr(args, "priorities", []),
            private_use=getattr(args, "private_use", False),
        )
    report = result["report"]
    print(
        json.dumps(
            {
                "run_id": report["run_id"],
                "status": report["run_status"],
                "coverage": report["coverage"],
                "new_articles": report["counts"]["new"],
                "source_failures": report["counts"]["source_failures"],
                "targets_with_updates": report["counts"].get(
                    "targets_with_updates", 0
                ),
                "targets_with_failures": report["counts"].get(
                    "targets_with_failures", 0
                ),
                "selected_targets": report.get("selection", {}).get(
                    "selected_target_count"
                ),
                "source_permission_policy": report.get("selection", {}).get(
                    "source_permission_policy"
                ),
                "json_report": result["json_path"],
                "markdown_report": result["markdown_path"],
            },
            ensure_ascii=False,
        )
    )
    return int(result["exit_code"])


def _validate_crontab_component(label: str, value: str) -> None:
    if any(character in value for character in "\r\n\0%"):
        raise ConfigError(f"{label} path is unsafe for crontab rendering")


def _cron_command(args: argparse.Namespace) -> int:
    for label, value in (
        ("config", args.config),
        ("state", args.state),
        ("output", args.output),
    ):
        _validate_crontab_component(label, value)
    loaded_config, _ = load_config(
        args.config,
        private_use_override=getattr(args, "private_use", False),
    )
    select_targets(
        loaded_config,
        target_ids=getattr(args, "target_ids", []),
        exclude_target_ids=getattr(args, "exclude_target_ids", []),
        priorities=getattr(args, "priorities", []),
    )
    if any(character in args.schedule for character in "\r\n\0"):
        raise ConfigError("schedule contains a forbidden control character")
    fields = args.schedule.split()
    if len(fields) != 5 or any(
        not re.fullmatch(r"[0-9*/,\-]+", field) for field in fields
    ):
        raise ConfigError("schedule must be a safe five-field cron expression")
    if args.lookback_hours < 1 or args.lookback_hours > 24 * 365:
        raise ConfigError("lookback-hours is out of bounds")

    script = str(Path(__file__).resolve())
    config = str(Path(args.config).expanduser().resolve())
    state = str(Path(args.state).expanduser().resolve())
    output = str(Path(args.output).expanduser().resolve())
    log_path = str(Path(output) / "last-run.log")
    for label, value in (
        ("interpreter", sys.executable),
        ("script", script),
        ("resolved config", config),
        ("resolved state", state),
        ("resolved output", output),
        ("log", log_path),
    ):
        _validate_crontab_component(label, value)
    command = [
        sys.executable,
        script,
        "run",
        "--config",
        config,
        "--state",
        state,
        "--output",
        output,
        "--lookback-hours",
        str(args.lookback_hours),
    ]
    if getattr(args, "private_use", False):
        command.append("--private-use")
    for target_id in getattr(args, "target_ids", []):
        command.extend(["--target", target_id])
    for target_id in getattr(args, "exclude_target_ids", []):
        command.extend(["--exclude-target", target_id])
    for priority in getattr(args, "priorities", []):
        command.extend(["--priority", priority])
    quoted = " ".join(shlex.quote(part) for part in command)
    schedule = " ".join(fields)
    print(f"{schedule} {quoted} > {shlex.quote(log_path)} 2>&1")
    print("# Not installed. Review the line, then add it with your scheduler.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            return _validate_command(
                args.config,
                private_use=getattr(args, "private_use", False),
            )
        if args.command == "run":
            return _run_command(args)
        if args.command == "cron":
            return _cron_command(args)
    except RunLocked as exc:
        print(json.dumps({"status": "locked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(json.dumps({"status": "invalid_config", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, ProcessDeadlineExceeded) as exc:
        print(
            json.dumps(
                {"status": "failed", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
