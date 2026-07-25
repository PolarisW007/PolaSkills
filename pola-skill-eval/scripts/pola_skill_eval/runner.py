"""Safe-by-default paired runner for Skill evaluation matrices."""

from __future__ import annotations

import json
import os
import signal
import string
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .grading import grade_run, run_passed
from .reporting import matrix_markdown
from .statistics import aggregate_runs, decide
from .utils import is_within, load_json, safe_environment, write_json


PLACEHOLDERS = {
    "skill_path",
    "prompt_file",
    "workspace",
    "response_file",
    "trace_file",
    "arm",
    "case_id",
    "trial",
    "config_dir",
}
SENSITIVE_ENV = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "COOKIE",
    "PRIVATE",
    "CREDENTIAL",
    "API_KEY",
    "ACCESS_KEY",
)


class RunnerConfigError(ValueError):
    """Raised when a runner adapter is unsafe or malformed."""


def _fields(template: str) -> set[str]:
    fields: set[str] = set()
    for _, field_name, _, _ in string.Formatter().parse(template):
        if field_name:
            fields.add(field_name)
    return fields


def load_runner_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    data = load_json(config_path)
    if not isinstance(data, dict):
        raise RunnerConfigError("runner config must be an object")
    argv = data.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(value, str) and value for value in argv)
    ):
        raise RunnerConfigError("runner argv must be a non-empty array of strings")
    if len(argv) > 128 or any(len(value) > 8192 for value in argv):
        raise RunnerConfigError("runner argv exceeds the 128 argument / 8192 character limits")
    for index, value in enumerate(argv):
        unknown = _fields(value) - PLACEHOLDERS
        if unknown:
            raise RunnerConfigError(
                f"runner argv[{index}] has unknown placeholders: {sorted(unknown)}"
            )
    cwd = data.get("cwd")
    if cwd is not None:
        if not isinstance(cwd, str) or not cwd:
            raise RunnerConfigError("runner cwd must be a non-empty string")
        unknown = _fields(cwd) - PLACEHOLDERS
        if unknown:
            raise RunnerConfigError(
                f"runner cwd has unknown placeholders: {sorted(unknown)}"
            )
    extra_env = data.get("env", {})
    if not isinstance(extra_env, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in extra_env.items()
    ):
        raise RunnerConfigError("runner env must be a string-to-string object")
    if len(extra_env) > 32 or any(len(value) > 4096 for value in extra_env.values()):
        raise RunnerConfigError("runner env exceeds the 32 entry / 4096 character limits")
    for key in extra_env:
        upper = key.upper()
        if any(fragment in upper for fragment in SENSITIVE_ENV):
            raise RunnerConfigError(f"runner env key looks sensitive and is refused: {key}")
    return {
        "argv": argv,
        "cwd": cwd,
        "env": extra_env,
        "config_dir": str(config_path.parent),
    }


def normalize_arms(
    *, candidate: str | Path, baseline: str | Path | None, old: str | Path | None = None
) -> dict[str, str]:
    arms: dict[str, str] = {}
    candidate_path = Path(candidate).expanduser().resolve()
    if not candidate_path.is_dir():
        raise ValueError(f"candidate Skill directory does not exist: {candidate_path}")
    arms["candidate"] = str(candidate_path)
    if baseline is None or str(baseline).upper() == "NONE":
        arms["baseline"] = ""
    else:
        baseline_path = Path(baseline).expanduser().resolve()
        if not baseline_path.is_dir():
            raise ValueError(f"baseline Skill directory does not exist: {baseline_path}")
        arms["baseline"] = str(baseline_path)
    if old is not None and str(old).upper() != "NONE":
        old_path = Path(old).expanduser().resolve()
        if not old_path.is_dir():
            raise ValueError(f"old Skill directory does not exist: {old_path}")
        arms["old"] = str(old_path)
    return arms


def build_plan(
    suite: dict[str, Any], arms: dict[str, str], *, concurrency: int = 1
) -> dict[str, Any]:
    if not isinstance(concurrency, int) or concurrency < 1 or concurrency > 8:
        raise ValueError("concurrency must be between 1 and 8")
    jobs: list[dict[str, Any]] = []
    for case in suite["cases"]:
        for arm in sorted(arms):
            for trial in range(1, case["trials"] + 1):
                jobs.append(
                    {
                        "arm": arm,
                        "skill_path": arms[arm],
                        "case_id": case["id"],
                        "trial": trial,
                        "timeout_seconds": case["timeout_seconds"],
                        "max_output_bytes": case["max_output_bytes"],
                    }
                )
    if len(jobs) > 5000:
        raise ValueError("evaluation plan exceeds the 5000-job safety limit")
    return {
        "suite": suite["name"],
        "mode": suite["mode"],
        "concurrency": concurrency,
        "arms": arms,
        "jobs": jobs,
        "execute": False,
    }


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except OSError:
            pass


def _run_process(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: float,
    max_output_bytes: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        return {
            "status": "start_error",
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "captured_bytes": 0,
        }

    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    state = {"total": 0}
    lock = threading.Lock()
    output_exceeded = threading.Event()

    def reader(name: str, pipe: Any) -> None:
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                break
            with lock:
                remaining = max(0, max_output_bytes - state["total"])
                if remaining:
                    buffers[name].extend(chunk[:remaining])
                state["total"] += len(chunk)
                if state["total"] > max_output_bytes:
                    output_exceeded.set()
        pipe.close()

    threads = [
        threading.Thread(target=reader, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=reader, args=("stderr", process.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()

    status = "completed"
    deadline = started + timeout_seconds
    while process.poll() is None:
        if output_exceeded.is_set():
            status = "output_limit"
            _terminate_process(process)
            break
        if time.perf_counter() >= deadline:
            status = "timeout"
            _terminate_process(process)
            break
        time.sleep(0.01)
    for thread in threads:
        thread.join(timeout=2)
    return {
        "status": status,
        "exit_code": process.poll(),
        "stdout": buffers["stdout"].decode("utf-8", errors="replace"),
        "stderr": buffers["stderr"].decode("utf-8", errors="replace"),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "captured_bytes": min(state["total"], max_output_bytes),
        "observed_stream_bytes": state["total"],
    }


def _safe_trace(path: Path, max_bytes: int) -> dict[str, Any] | None:
    if not path.is_file() or path.stat().st_size > max_bytes:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    trace: dict[str, Any] = {}
    if isinstance(raw.get("skill_invoked"), bool):
        trace["skill_invoked"] = raw["skill_invoked"]
    if isinstance(raw.get("tool_calls"), (int, float)) and raw["tool_calls"] >= 0:
        trace["tool_calls"] = raw["tool_calls"]
    usage = raw.get("usage")
    if isinstance(usage, dict):
        safe_usage = {
            key: usage[key]
            for key in ("input_tokens", "output_tokens", "cost_usd")
            if isinstance(usage.get(key), (int, float)) and usage[key] >= 0
        }
        if safe_usage:
            trace["usage"] = safe_usage
    return trace or None


def _run_job(
    *,
    job: dict[str, Any],
    case: dict[str, Any],
    runner: dict[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    run_dir = (
        output_root
        / "runs"
        / job["arm"]
        / job["case_id"]
        / f"trial-{job['trial']:03d}"
    )
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=False)
    prompt_file = run_dir / "prompt.txt"
    response_file = run_dir / "response.txt"
    trace_file = run_dir / "trace.json"
    prompt_file.write_text(case["prompt"], encoding="utf-8")
    values = {
        "skill_path": job["skill_path"],
        "prompt_file": str(prompt_file),
        "workspace": str(workspace),
        "response_file": str(response_file),
        "trace_file": str(trace_file),
        "arm": job["arm"],
        "case_id": job["case_id"],
        "trial": str(job["trial"]),
        "config_dir": runner["config_dir"],
    }
    argv = [value.format_map(values) for value in runner["argv"]]
    cwd_text = runner["cwd"].format_map(values) if runner["cwd"] else str(workspace)
    cwd = Path(cwd_text).expanduser().resolve()
    if not cwd.is_dir():
        record = {
            **job,
            "category": case["category"],
            "split": case["split"],
            "should_trigger": case.get("should_trigger"),
            "status": "start_error",
            "exit_code": None,
            "duration_ms": 0.0,
            "output_bytes": 0,
            "trace": None,
            "grades": [],
            "passed": False,
            "evidence_dir": str(run_dir),
            "error": f"runner cwd does not exist: {cwd}",
        }
        write_json(run_dir / "run.json", record)
        return record
    if not is_within(cwd, workspace):
        record = {
            **job,
            "category": case["category"],
            "split": case["split"],
            "should_trigger": case.get("should_trigger"),
            "status": "start_error",
            "exit_code": None,
            "duration_ms": 0.0,
            "output_bytes": 0,
            "trace": None,
            "grades": [],
            "passed": False,
            "evidence_dir": str(run_dir),
            "error": f"runner cwd must remain inside its fresh workspace: {cwd}",
        }
        write_json(run_dir / "run.json", record)
        return record

    process = _run_process(
        argv,
        cwd=cwd,
        env=safe_environment(runner["env"]),
        timeout_seconds=float(job["timeout_seconds"]),
        max_output_bytes=int(job["max_output_bytes"]),
    )
    (run_dir / "stdout.txt").write_text(process["stdout"], encoding="utf-8")
    (run_dir / "stderr.txt").write_text(process["stderr"], encoding="utf-8")

    response = process["stdout"]
    response_bytes = 0
    if response_file.is_file():
        response_size = response_file.stat().st_size
        response_bytes = response_size
        if response_size <= job["max_output_bytes"]:
            response = response_file.read_text(encoding="utf-8", errors="replace")
        else:
            with response_file.open("rb") as handle:
                response = handle.read(job["max_output_bytes"]).decode(
                    "utf-8", errors="replace"
                )
            process["status"] = "output_limit"
    trace = _safe_trace(trace_file, int(job["max_output_bytes"]))
    trace_bytes = trace_file.stat().st_size if trace_file.is_file() else 0
    output_bytes = (
        int(process["observed_stream_bytes"]) + response_bytes + trace_bytes
    )
    if output_bytes > job["max_output_bytes"]:
        process["status"] = "output_limit"
    internal = {
        **job,
        "category": case["category"],
        "split": case["split"],
        "should_trigger": case.get("should_trigger"),
        "status": process["status"],
        "exit_code": process["exit_code"],
        "duration_ms": process["duration_ms"],
        "output_bytes": output_bytes,
        "trace": trace,
        "stdout": process["stdout"],
        "stderr": process["stderr"],
        "response": response,
        "evidence_dir": str(run_dir),
    }
    grades = grade_run(internal, case, run_dir)
    internal["grades"] = grades
    internal["passed"] = run_passed(internal, grades)
    record = {
        key: value
        for key, value in internal.items()
        if key not in {"stdout", "stderr", "response"}
    }
    write_json(run_dir / "run.json", record)
    return record


def execute_matrix(
    *,
    suite: dict[str, Any],
    runner: dict[str, Any],
    arms: dict[str, str],
    output_dir: str | Path,
    concurrency: int = 1,
) -> dict[str, Any]:
    plan = build_plan(suite, arms, concurrency=concurrency)
    output_root = Path(output_dir).expanduser().resolve(strict=False)
    if output_root.exists():
        raise FileExistsError(f"output directory already exists: {output_root}")
    for path in arms.values():
        if path and is_within(output_root, Path(path)):
            raise ValueError("output directory must not be inside an evaluated Skill")
    output_root.mkdir(parents=True)
    write_json(output_root / "plan.json", {**plan, "execute": True})

    cases = {case["id"]: case for case in suite["cases"]}
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(
                _run_job,
                job=job,
                case=cases[job["case_id"]],
                runner=runner,
                output_root=output_root,
            ): job
            for job in plan["jobs"]
        }
        for future in as_completed(futures):
            job = futures[future]
            try:
                records.append(future.result())
            except Exception as exc:
                run_dir = (
                    output_root
                    / "runs"
                    / job["arm"]
                    / job["case_id"]
                    / f"trial-{job['trial']:03d}"
                )
                run_dir.mkdir(parents=True, exist_ok=True)
                record = {
                    **job,
                    "category": cases[job["case_id"]]["category"],
                    "split": cases[job["case_id"]]["split"],
                    "should_trigger": cases[job["case_id"]].get("should_trigger"),
                    "status": "internal_error",
                    "exit_code": None,
                    "duration_ms": None,
                    "output_bytes": 0,
                    "trace": None,
                    "grades": [],
                    "passed": False,
                    "evidence_dir": str(run_dir),
                    "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                }
                write_json(run_dir / "run.json", record)
                records.append(record)
    records.sort(key=lambda item: (item["arm"], item["case_id"], item["trial"]))
    aggregate = aggregate_runs(records)
    decision = decide(
        records, aggregate, suite["thresholds"], mode=suite["mode"]
    )
    report = {
        "schema_version": "1.0",
        "suite": {
            "name": suite["name"],
            "mode": suite["mode"],
            "cases": len(suite["cases"]),
            "warnings": suite["warnings"],
        },
        "limits": {"concurrency": concurrency},
        "decision": decision,
        "aggregate": aggregate,
        "runs": records,
    }
    write_json(output_root / "report.json", report)
    (output_root / "report.md").write_text(
        matrix_markdown(report), encoding="utf-8"
    )
    return report
