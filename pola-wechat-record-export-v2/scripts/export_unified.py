#!/usr/bin/env python3
"""Export private chats, group chats and locally cached Moments to one schema.

The reader opens decrypted SQLite databases read-only. It never reads key files,
modifies WeChat, or uploads data. Moments coverage is explicitly local-cache-only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from export_all import (
    MSG_TYPE_MAP,
    build_sender_id_map,
    collect_all_usernames,
    get_all_msg_dbs,
    load_contacts,
    username_to_table,
)

SCHEMA_VERSION = "pola.wechat.record/v1"
FRESHNESS_WARNING = "朋友圈仅覆盖本机已缓存且当前账号可见的数据；缺失不代表删除、屏蔽或权限变化"
MAX_RECORDS_DEFAULT = 250_000
MAX_TEXT_BYTES_DEFAULT = 2 * 1024 * 1024


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def identity(value: str, mode: str) -> str:
    return value if mode == "clear" else f"sha256:{digest(value)}"


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def decode_text(value: object, max_bytes: int) -> tuple[str, bool]:
    if value is None:
        return "", False
    if isinstance(value, bytes):
        raw = value
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
        raw = text.encode("utf-8", errors="replace")
    truncated = len(raw) > max_bytes
    if truncated:
        text = raw[:max_bytes].decode("utf-8", errors="ignore") + "…"
    return text, truncated


def message_kind(local_type: int) -> str:
    mapping = {
        1: "text", 3: "image", 34: "voice", 42: "contact_card",
        43: "video", 47: "sticker", 48: "location", 49: "link_or_file",
        10000: "system", 10002: "recalled",
    }
    return mapping.get(local_type, f"wechat_type_{local_type}")


def safe_xml(value: object) -> ET.Element | None:
    text, _ = decode_text(value, MAX_TEXT_BYTES_DEFAULT)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    return root if root.tag == "TimelineObject" else root.find(".//TimelineObject")


def child_text(root: ET.Element, path: str) -> str:
    return (root.findtext(path) or "").strip()


def base_record(record_id: str, stream: str, occurred_at: str, author: str,
                conversation: str | None, content_type: str, text: str,
                source: dict, warning: str | None = None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "record_id": record_id,
        "stream": stream,
        "occurred_at": occurred_at,
        "author": author,
        "conversation": conversation,
        "content_type": content_type,
        "text": text,
        "source": source,
        "coverage_warning": warning,
    }


def iter_messages(decrypted_dir: Path, identity_mode: str, start: int | None,
                  end: int | None, max_text_bytes: int):
    _, contacts = load_contacts(str(decrypted_dir))
    dbs = [Path(value) for value in get_all_msg_dbs(str(decrypted_dir))]
    username_dbs = collect_all_usernames([str(value) for value in dbs])
    sender_maps = {str(path): build_sender_id_map(str(path)) for path in dbs}
    for username, source_dbs in sorted(username_dbs.items()):
        table = username_to_table(username)
        stream = "group" if username.endswith("@chatroom") else "private"
        conversation = identity(contacts.get(username, username), identity_mode)
        for db_value in sorted(source_dbs):
            db_path = Path(db_value)
            with connect_ro(db_path) as conn:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if not exists:
                    continue
                clauses, params = [], []
                if start is not None:
                    clauses.append("create_time>=?")
                    params.append(start)
                if end is not None:
                    clauses.append("create_time<=?")
                    params.append(end)
                where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
                rows = conn.execute(
                    f'SELECT local_id,local_type,create_time,real_sender_id,message_content '
                    f'FROM "{table}"{where} ORDER BY create_time,local_id', params
                )
                sender_map = sender_maps.get(str(db_path), {})
                for local_id, local_type, created, sender_id, content in rows:
                    text, truncated = decode_text(content, max_text_bytes)
                    sender_username = sender_map.get(sender_id, "") if sender_id is not None else ""
                    if stream == "group" and ":\n" in text:
                        raw_sender, text = text.split(":\n", 1)
                        sender_username = raw_sender
                    author = identity(
                        contacts.get(sender_username, sender_username or "unknown"),
                        identity_mode,
                    )
                    occurred = datetime.fromtimestamp(int(created), tz=timezone.utc).isoformat()
                    source_id = f"{db_path.name}:{table}:{int(local_id)}"
                    yield base_record(
                        digest(source_id), stream, occurred, author, conversation,
                        message_kind(int(local_type or 0)), text,
                        {
                            "database": db_path.name,
                            "table_hash": digest(table),
                            "local_id": int(local_id),
                            "wechat_type": int(local_type or 0),
                            "wechat_type_label": MSG_TYPE_MAP.get(int(local_type or 0)),
                            "text_truncated": truncated,
                        },
                    )


def iter_moments(sns_db: Path, identity_mode: str, start: int | None,
                 end: int | None, max_text_bytes: int):
    with connect_ro(sns_db) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='SnsTimeLine'"
        ).fetchone()
        if not exists:
            raise RuntimeError("sns.db does not contain SnsTimeLine")
        for tid, username, content in conn.execute("SELECT tid,user_name,content FROM SnsTimeLine"):
            root = safe_xml(content)
            if root is None:
                continue
            created_text = child_text(root, "createTime")
            if not created_text.isdigit():
                continue
            created = int(created_text)
            if start is not None and created < start:
                continue
            if end is not None and created > end:
                continue
            author_raw = child_text(root, "username") or decode_text(username, 4096)[0] or "unknown"
            body, truncated = decode_text(child_text(root, "contentDesc"), max_text_bytes)
            media = root.findall(".//media")
            urls = {
                (node.text or "").strip()
                for path in ("ContentObject/contentUrl", ".//url")
                for node in root.findall(path)
                if (node.text or "").strip()
            }
            occurred = datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
            yield base_record(
                digest(f"sns:{tid}"), "moments", occurred,
                identity(author_raw, identity_mode), None, "moment", body,
                {
                    "database": sns_db.name,
                    "tid": int(tid),
                    "cache_scope": "local_seen_only",
                    "local_cache_incomplete": True,
                    "media_count": len(media),
                    "link_count": len(urls),
                    "link_hashes": sorted(digest(url) for url in urls)[:32],
                    "text_truncated": truncated,
                },
                FRESHNESS_WARNING,
            )


def write_outputs(records: list[dict], output: Path, source_meta: dict) -> None:
    output.mkdir(parents=True, exist_ok=False)
    records.sort(key=lambda item: (item["occurred_at"], item["record_id"]))
    ndjson = output / "records.ndjson"
    with ndjson.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    fields = [
        "record_id", "stream", "occurred_at", "author", "conversation",
        "content_type", "text", "coverage_warning", "source_json",
    ]
    with (output / "records.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = {key: record.get(key) for key in fields if key != "source_json"}
            row["source_json"] = json.dumps(record["source"], ensure_ascii=False, sort_keys=True)
            writer.writerow(row)
    with (output / "timeline.md").open("w", encoding="utf-8") as handle:
        handle.write("# 微信记录统一时间线\n\n")
        handle.write("> 朋友圈只表示本机已缓存且当前账号可见的内容。\n\n")
        day = None
        for record in records:
            current_day = record["occurred_at"][:10]
            if current_day != day:
                day = current_day
                handle.write(f"## {day}\n\n")
            label = {"private": "单聊", "group": "群聊", "moments": "朋友圈"}[record["stream"]]
            target = f" · {record['conversation']}" if record["conversation"] else ""
            text = record["text"].replace("\n", " ").strip() or f"[{record['content_type']}]"
            handle.write(f"- {record['occurred_at'][11:19]} · **{label}**{target} · "
                         f"{record['author']}：{text}\n")
    counts = Counter(item["stream"] for item in records)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "counts_by_stream": dict(sorted(counts.items())),
        "time_range": [records[0]["occurred_at"], records[-1]["occurred_at"]] if records else None,
        "files": {
            name: {"sha256": hashlib.sha256((output / name).read_bytes()).hexdigest(),
                   "bytes": (output / name).stat().st_size}
            for name in ("records.ndjson", "records.csv", "timeline.md")
        },
        "source": source_meta,
        "warnings": [FRESHNESS_WARNING] if counts.get("moments") else [],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def parse_epoch(value: str | None, end_of_day: bool = False) -> int | None:
    if not value:
        return None
    parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(parsed.timestamp()) + (86_399 if end_of_day else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified read-only WeChat record exporter")
    parser.add_argument("--decrypted-dir", type=Path, help="decrypted account directory")
    parser.add_argument("--sns-db", type=Path, help="decrypted sns.db (local cache only)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", help="UTC start date, YYYY-MM-DD")
    parser.add_argument("--end", help="UTC end date, YYYY-MM-DD, inclusive")
    parser.add_argument("--identity-mode", choices=["clear", "hash"], default="clear")
    parser.add_argument("--max-records", type=int, default=MAX_RECORDS_DEFAULT)
    parser.add_argument("--max-text-bytes", type=int, default=MAX_TEXT_BYTES_DEFAULT)
    args = parser.parse_args()
    if not args.decrypted_dir and not args.sns_db:
        parser.error("provide --decrypted-dir and/or --sns-db")
    if args.max_records < 1 or args.max_records > 2_000_000:
        parser.error("--max-records must be between 1 and 2000000")
    if args.max_text_bytes < 1 or args.max_text_bytes > 16 * 1024 * 1024:
        parser.error("--max-text-bytes must be between 1 and 16777216")
    start, end = parse_epoch(args.start), parse_epoch(args.end, True)
    records: list[dict] = []
    if args.decrypted_dir:
        records.extend(iter_messages(args.decrypted_dir.resolve(), args.identity_mode, start, end, args.max_text_bytes))
    if args.sns_db:
        records.extend(iter_moments(args.sns_db.resolve(), args.identity_mode, start, end, args.max_text_bytes))
    if len(records) > args.max_records:
        raise RuntimeError(f"record limit exceeded: {len(records)} > {args.max_records}")
    source_meta = {
        "decrypted_dir_present": bool(args.decrypted_dir),
        "sns_db_present": bool(args.sns_db),
        "identity_mode": args.identity_mode,
        "read_only": True,
        "start": args.start,
        "end": args.end,
        "max_records": args.max_records,
        "max_text_bytes": args.max_text_bytes,
    }
    write_outputs(records, args.output.resolve(), source_meta)
    print(json.dumps({"ok": True, "output": str(args.output.resolve()), "records": len(records)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, sqlite3.DatabaseError, RuntimeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
