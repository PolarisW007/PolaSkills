#!/usr/bin/env python3
"""Synthetic Harness for private/group/Moments unified export."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def msg_table(username: str) -> str:
    return "Msg_" + hashlib.md5(username.encode()).hexdigest()


def make_message_db(path: Path, rows: list[tuple[str, int, str]]) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE Name2Id(user_name TEXT)")
    for username, _, _ in rows:
        conn.execute("INSERT INTO Name2Id(user_name) VALUES (?)", (username,))
        table = msg_table(username)
        conn.execute(
            f'CREATE TABLE "{table}"(local_id INTEGER,local_type INTEGER,create_time INTEGER,'
            "real_sender_id INTEGER,message_content BLOB,source INTEGER,WCDB_CT_message_content INTEGER)"
        )
        conn.execute(
            f'INSERT INTO "{table}" VALUES (1,1,1704067200,1,?,0,0)', (rows[0][2],)
        )
    conn.commit()
    conn.close()


def make_fixtures(root: Path) -> tuple[Path, Path]:
    decrypted = root / "decrypted"
    (decrypted / "message").mkdir(parents=True)
    private, group = "friend_a", "team@chatroom"
    make_message_db(
        decrypted / "message" / "message_0.db",
        [(private, 1, "private hello"), (group, 1, "member_a:\ngroup hello")],
    )
    sns = decrypted / "sns.db"
    conn = sqlite3.connect(sns)
    conn.execute("CREATE TABLE SnsTimeLine(tid INTEGER,user_name TEXT,content BLOB)")
    xml = (
        "<TimelineObject><username>friend_a</username><createTime>1704067201</createTime>"
        "<contentDesc>moment hello</contentDesc><ContentObject><contentStyle>1</contentStyle>"
        "<mediaList><media><type>2</type><url>https://example.invalid/a</url></media></mediaList>"
        "</ContentObject></TimelineObject>"
    )
    conn.execute("INSERT INTO SnsTimeLine VALUES (?,?,?)", (7, "friend_a", xml))
    conn.commit()
    conn.close()
    return decrypted, sns


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pola_wechat_harness_") as tmp:
        root = Path(tmp)
        decrypted, sns = make_fixtures(root)
        output = root / "output"
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT_DIR / "export_unified.py"),
                "--decrypted-dir", str(decrypted), "--sns-db", str(sns),
                "--identity-mode", "hash", "--output", str(output),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            print(result.stdout + result.stderr, file=sys.stderr)
            return 1
        records = [
            json.loads(line)
            for line in (output / "records.ndjson").read_text(encoding="utf-8").splitlines()
        ]
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        checks = {
            "private_group_moments": {item["stream"] for item in records} == {"private", "group", "moments"},
            "machine_formats": (output / "records.csv").is_file() and (output / "records.ndjson").is_file(),
            "human_timeline": "朋友圈" in (output / "timeline.md").read_text(encoding="utf-8"),
            "cache_boundary": bool(manifest["warnings"]) and records[-1]["source"].get("local_cache_incomplete") is True,
            "identity_hash": all(item["author"].startswith("sha256:") for item in records),
            "integrity": all(
                hashlib.sha256((output / name).read_bytes()).hexdigest() == meta["sha256"]
                for name, meta in manifest["files"].items()
            ),
            "no_key_artifacts": not list(output.rglob("*key*")),
        }
        report = {"ok": all(checks.values()), "checks": checks, "counts": manifest["counts_by_stream"]}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
