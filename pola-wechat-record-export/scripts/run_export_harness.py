#!/usr/bin/env python3
"""Harness checks for WeChat export completeness.

This validates the two highest-risk regressions:
1. one contact's history is merged across every message shard;
2. biz_message_*.db sources are discovered and counted.
It prints counts only, never raw chat content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from export_all import analyze_decrypted_sources, get_all_msg_dbs, username_to_table  # noqa: E402


def count_messages(decrypted_dir: Path, username: str) -> tuple[int, list[dict]]:
    table = username_to_table(username)
    parts = []
    total = 0
    for db_path in get_all_msg_dbs(str(decrypted_dir)):
        conn = sqlite3.connect(db_path)
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not exists:
                continue
            row = conn.execute(f"SELECT count(*), min(create_time), max(create_time) FROM [{table}]").fetchone()
            count = int(row[0] or 0)
            if count:
                total += count
                parts.append({
                    "db": os.path.basename(db_path),
                    "messages": count,
                    "first_ts": row[1],
                    "last_ts": row[2],
                })
        finally:
            conn.close()
    return total, parts


def count_biz_messages(decrypted_dir: Path) -> tuple[int, int]:
    total = 0
    tables = 0
    msg_dir = decrypted_dir / "message"
    for db_path in sorted(msg_dir.glob("biz_message_*.db")):
        conn = sqlite3.connect(db_path)
        try:
            msg_tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
                ).fetchall()
            ]
            for table in msg_tables:
                count = int(conn.execute(f"SELECT count(*) FROM [{table}]").fetchone()[0] or 0)
                if count:
                    tables += 1
                    total += count
        finally:
            conn.close()
    return total, tables


def find_export_dir(output_base: Path) -> Path:
    dirs = [path for path in output_base.iterdir() if path.is_dir()]
    if not dirs:
        raise RuntimeError("export produced no output directory")
    return max(dirs, key=lambda p: p.stat().st_mtime)


def run_export(account: str, decrypted_dir: Path, username: str, output_base: Path) -> tuple[dict, dict, Path]:
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "export_all.py"),
        "--account",
        account,
        "--decrypted-dir",
        str(decrypted_dir),
        "--contacts",
        username,
        "--output",
        str(output_base),
        "--fresh",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        tail = "\n".join((result.stdout + "\n" + result.stderr).splitlines()[-40:])
        raise RuntimeError(f"export_all.py failed with {result.returncode}:\n{tail}")

    export_dir = find_export_dir(output_base)
    with (export_dir / "summary.json").open(encoding="utf-8") as f:
        summary = json.load(f)
    with (export_dir / "export_log.json").open(encoding="utf-8") as f:
        export_log = json.load(f)
    return summary, export_log, export_dir


def read_header_message_count(export_dir: Path) -> int:
    chat_files = list((export_dir / "chats").glob("*/*.txt"))
    if len(chat_files) != 1:
        raise RuntimeError(f"expected 1 exported chat file, found {len(chat_files)}")
    with chat_files[0].open(encoding="utf-8") as f:
        for _ in range(8):
            line = f.readline()
            match = re.match(r"# Messages: (\d+)", line)
            if match:
                return int(match.group(1))
    raise RuntimeError("exported chat file has no # Messages header")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate WeChat export completeness")
    parser.add_argument("--account", default="2", choices=["1", "2"])
    parser.add_argument("--decrypted-dir", required=True)
    parser.add_argument("--username", default="loujian625050")
    parser.add_argument("--min-messages", type=int, default=1)
    parser.add_argument("--keep-output", action="store_true")
    args = parser.parse_args()

    decrypted_dir = Path(args.decrypted_dir).expanduser().resolve()
    output_base = Path(tempfile.mkdtemp(prefix="wechat_export_harness_"))
    checks = []

    try:
        source_report = analyze_decrypted_sources(str(decrypted_dir))
        chat_dbs = get_all_msg_dbs(str(decrypted_dir))
        biz_total, biz_tables = count_biz_messages(decrypted_dir)
        direct_total, parts = count_messages(decrypted_dir, args.username)
        summary, export_log, export_dir = run_export(args.account, decrypted_dir, args.username, output_base)
        header_count = read_header_message_count(export_dir)

        checks.append({"id": "WX-H01-chat-db-discovery", "passed": len(chat_dbs) >= 1})
        checks.append({"id": "WX-H02-biz-message-included", "passed": biz_total > 0 and biz_tables > 0})
        checks.append({"id": "WX-H03-target-cross-db-count", "passed": direct_total >= args.min_messages and len(parts) > 1})
        checks.append({"id": "WX-H04-export-count-matches-direct", "passed": summary["messages_total"] == direct_total})
        checks.append({"id": "WX-H05-header-count-matches-direct", "passed": header_count == direct_total})
        checks.append({
            "id": "WX-H06-export-log-source-report",
            "passed": export_log["source_report"]["biz_message_dbs"] == source_report["biz_message_dbs"],
        })

        ok = all(item["passed"] for item in checks)
        result = {
            "ok": ok,
            "checks": checks,
            "sample": {
                "account": args.account,
                "username_hash": hashlib.sha1(args.username.encode()).hexdigest()[:12],
                "chat_databases": len(chat_dbs),
                "message_databases": len(source_report["message_dbs"]),
                "biz_message_databases": len(source_report["biz_message_dbs"]),
                "biz_messages_total": biz_total,
                "biz_tables_nonempty": biz_tables,
                "target_messages_direct": direct_total,
                "target_source_parts": parts,
                "export_messages_total": summary["messages_total"],
                "export_dir": str(export_dir),
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    finally:
        if not args.keep_output:
            shutil.rmtree(output_base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
