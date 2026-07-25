#!/usr/bin/env python3
"""WeChat chat record exporter.

Usage:
    python3 export_all.py --account 1
    python3 export_all.py --account 1 --contacts "张三,李四"
    python3 export_all.py --account 1 --start-date 2025-01-01 --end-date 2025-06-01
    python3 export_all.py --account 1 --type group --contacts "工作群"
    python3 export_all.py --account 1 --dry-run
"""

import sqlite3
import os
import re
import json
import hashlib
import argparse
from datetime import datetime
from pathlib import Path

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_OUTPUT = os.environ.get("WECHAT_EXPORT_OUTPUT", os.path.join(TOOL_DIR, "output"))

# Voice transcription globals (lazy loaded)
_whisper_model = None


def get_whisper_model():
    """Lazy-load Whisper model."""
    global _whisper_model
    if _whisper_model is None:
        import whisper
        print("  Loading Whisper model (base)...")
        _whisper_model = whisper.load_model("base")
    return _whisper_model


def transcribe_silk(voice_data):
    """Decode SILK voice data and transcribe to text."""
    import pilk
    import tempfile
    import warnings
    warnings.filterwarnings("ignore", message="FP16 is not supported")

    # Skip 0x02 prefix byte, SILK data starts with #!SILK_V3
    silk_data = voice_data[1:] if voice_data[0:1] == b'\x02' else voice_data

    if not silk_data.startswith(b'#!SILK_V3'):
        return ""

    tmp_dir = tempfile.gettempdir()
    tmp_silk = os.path.join(tmp_dir, "wx_voice.silk")
    tmp_pcm = os.path.join(tmp_dir, "wx_voice.pcm")
    tmp_wav = os.path.join(tmp_dir, "wx_voice.wav")

    try:
        with open(tmp_silk, "wb") as f:
            f.write(silk_data)

        if hasattr(pilk, "silk_to_wav"):
            pilk.silk_to_wav(tmp_silk, tmp_wav, rate=24000)
        else:
            import wave
            pilk.silk_to_pcm(tmp_silk, tmp_pcm, sample_rate=24000)
            with open(tmp_pcm, "rb") as pcm_f:
                pcm_data = pcm_f.read()
            with wave.open(tmp_wav, "wb") as wav_f:
                wav_f.setnchannels(1)
                wav_f.setsampwidth(2)
                wav_f.setframerate(24000)
                wav_f.writeframes(pcm_data)

        model = get_whisper_model()
        result = model.transcribe(tmp_wav, language="zh", fp16=False)
        return result.get("text", "").strip()
    except Exception:
        return ""
    finally:
        for fp in [tmp_silk, tmp_pcm, tmp_wav]:
            if os.path.exists(fp):
                os.remove(fp)


def build_voice_data_map(decrypted_dir, username):
    """Build mapping from local_id -> voice_data bytes for a conversation."""
    voice_map = {}
    msg_dir = os.path.join(decrypted_dir, "message")
    if not os.path.isdir(msg_dir):
        return voice_map

    media_dbs = sorted([
        os.path.join(msg_dir, f)
        for f in os.listdir(msg_dir)
        if re.match(r'^media_\d+\.db$', f)
    ])

    for mdb in media_dbs:
        conn = sqlite3.connect(mdb)
        try:
            row = conn.execute(
                "SELECT rowid FROM Name2Id WHERE user_name=?", (username,)
            ).fetchone()
            if not row:
                continue
            chat_name_id = row[0]
            rows = conn.execute(
                "SELECT local_id, voice_data FROM VoiceInfo WHERE chat_name_id=?",
                (chat_name_id,)
            ).fetchall()
            for local_id, vdata in rows:
                if vdata:
                    voice_map[local_id] = vdata
        except:
            pass
        finally:
            conn.close()

    return voice_map


def db_source_type(db_path):
    """Return the logical message source type for a decrypted DB path."""
    name = os.path.basename(db_path)
    if re.match(r"^biz_message_\d+\.db$", name):
        return "biz_message"
    if re.match(r"^message_\d+\.db$", name):
        return "message"
    return "unknown"


MSG_TYPE_MAP = {
    1: "text",
    3: "image",
    34: "voice",
    42: "card",
    43: "video",
    47: "emoji",
    48: "location",
    49: "link/file",
    10000: "system",
    10002: "revoke",
}


def load_contacts(decrypted_dir):
    """Load contacts from contact.db, return list of contact dicts and username->name map."""
    contact_db = os.path.join(decrypted_dir, "contact", "contact.db")
    contacts_list = []
    contacts_map = {}

    if not os.path.isfile(contact_db):
        return contacts_list, contacts_map

    conn = sqlite3.connect(contact_db)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(contact)").fetchall()]
        for row in conn.execute("SELECT * FROM contact"):
            record = dict(zip(cols, row))
            username = record.get("username", "")
            nick_name = record.get("nick_name", "")
            remark = record.get("remark", "")
            display_name = remark or nick_name or username

            contact_type = "unknown"
            if "@chatroom" in username:
                contact_type = "group"
            elif username.startswith("gh_"):
                contact_type = "official_account"
            elif username.startswith("wxid_") or (not username.startswith("weixin") and "@" not in username):
                contact_type = "friend"

            contacts_list.append({
                "username": username,
                "nick_name": nick_name,
                "remark": remark,
                "display_name": display_name,
                "type": contact_type,
            })
            contacts_map[username] = display_name

        # Also load strangers. They may have chat history even if they are not
        # in the main contact table, so keep them in both the display map and
        # the exported contact manifest.
        try:
            for row in conn.execute("SELECT username, remark, nick_name FROM stranger"):
                username, remark, nick_name = row
                if username not in contacts_map:
                    name = remark or nick_name or username
                    contacts_map[username] = name
                    contacts_list.append({
                        "username": username,
                        "nick_name": nick_name,
                        "remark": remark,
                        "display_name": name,
                        "type": "stranger",
                    })
        except:
            pass

    finally:
        conn.close()

    return contacts_list, contacts_map


def get_owner_nickname(decrypted_dir, account):
    """Get the WeChat account owner's nickname for output folder naming."""
    OWNER_WXIDS = {
        "1": "wxid_9gngtzl6t2h922",
        "2": "wangchzng_bc19",
    }
    owner_wxid = OWNER_WXIDS.get(account)
    if not owner_wxid:
        return None

    contact_db = os.path.join(decrypted_dir, "contact", "contact.db")
    if not os.path.isfile(contact_db):
        return None

    conn = sqlite3.connect(contact_db)
    try:
        row = conn.execute(
            "SELECT nick_name FROM contact WHERE username=?", (owner_wxid,)
        ).fetchone()
        if row and row[0]:
            return row[0]
    except:
        pass
    finally:
        conn.close()

    # Try to find from session.db self info
    session_db = os.path.join(decrypted_dir, "session", "session.db")
    if os.path.isfile(session_db):
        conn = sqlite3.connect(session_db)
        try:
            row = conn.execute(
                "SELECT session_title FROM SessionAbstract WHERE username=?", (owner_wxid,)
            ).fetchone()
            if row and row[0]:
                return row[0]
        except:
            pass
        finally:
            conn.close()

    return owner_wxid


def get_all_msg_dbs(decrypted_dir):
    """Find all chat message DB files.

    WeChat stores normal chats in message_N.db and some service/official
    account conversations in biz_message_N.db. Both use Msg_<md5(username)>
    tables and must be included for a complete export.
    """
    msg_dir = os.path.join(decrypted_dir, "message")
    if not os.path.isdir(msg_dir):
        return []
    dbs = []
    for f in sorted(os.listdir(msg_dir)):
        if re.match(r"^(message|biz_message)_\d+\.db$", f):
            dbs.append(os.path.join(msg_dir, f))
    return dbs


def analyze_decrypted_sources(decrypted_dir):
    """Build a small completeness report for decrypted message sources."""
    msg_dir = os.path.join(decrypted_dir, "message")
    report = {
        "message_dir": msg_dir,
        "message_dbs": [],
        "biz_message_dbs": [],
        "media_dbs": [],
        "missing_message_indexes": [],
        "missing_biz_message_indexes": [],
        "decrypt_manifest": None,
        "warnings": [],
    }
    if not os.path.isdir(msg_dir):
        report["warnings"].append(f"message directory not found: {msg_dir}")
        return report

    files = sorted(os.listdir(msg_dir))
    for filename in files:
        if re.match(r"^message_\d+\.db$", filename):
            report["message_dbs"].append(filename)
        elif re.match(r"^biz_message_\d+\.db$", filename):
            report["biz_message_dbs"].append(filename)
        elif re.match(r"^media_\d+\.db$", filename):
            report["media_dbs"].append(filename)

    def missing_indexes(names, prefix):
        indexes = sorted(int(re.search(r"_(\d+)\.db$", name).group(1)) for name in names)
        if not indexes:
            return []
        return [idx for idx in range(indexes[0], indexes[-1] + 1) if idx not in indexes]

    report["missing_message_indexes"] = missing_indexes(report["message_dbs"], "message")
    report["missing_biz_message_indexes"] = missing_indexes(report["biz_message_dbs"], "biz_message")

    manifest_path = Path(decrypted_dir) / "decrypt_manifest.json"
    if manifest_path.is_file():
        try:
            with manifest_path.open(encoding="utf-8") as f:
                report["decrypt_manifest"] = json.load(f)
        except Exception as exc:
            report["warnings"].append(f"cannot read decrypt_manifest.json: {exc}")
    else:
        report["warnings"].append("decrypt_manifest.json not found; cannot verify key/decrypt completeness")

    return report


def validate_source_completeness(source_report, allow_incomplete=False):
    """Raise when decrypted message sources look incomplete."""
    errors = []
    if not source_report["message_dbs"]:
        errors.append("no message_N.db files found")
    if source_report["missing_message_indexes"]:
        errors.append(f"missing message indexes: {source_report['missing_message_indexes']}")
    if source_report["missing_biz_message_indexes"]:
        errors.append(f"missing biz_message indexes: {source_report['missing_biz_message_indexes']}")

    manifest = source_report.get("decrypt_manifest")
    if manifest:
        failed = manifest.get("failed", [])
        missing_keys = manifest.get("missing_key_sources", [])
        missing_sources = manifest.get("missing_source_files", [])
        if failed:
            errors.append(f"decrypt failures: {len(failed)}")
        if missing_keys:
            errors.append(f"source DBs missing keys: {len(missing_keys)}")
        if missing_sources:
            errors.append(f"key entries missing source files: {len(missing_sources)}")

    if errors and not allow_incomplete:
        raise RuntimeError("decrypted source completeness check failed: " + "; ".join(errors))
    return errors


def username_to_table(username):
    h = hashlib.md5(username.encode()).hexdigest()
    return f"Msg_{h}"


def collect_all_usernames(msg_dbs):
    """Collect all usernames from all message DBs.

    Returns dict mapping username -> list of db_paths that contain it.
    A conversation's messages may be spread across multiple databases.
    """
    username_to_dbs = {}
    for db_path in msg_dbs:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT user_name FROM Name2Id WHERE user_name != ''"
            ).fetchall()
            for (username,) in rows:
                if username not in username_to_dbs:
                    username_to_dbs[username] = []
                if db_path not in username_to_dbs[username]:
                    username_to_dbs[username].append(db_path)
        finally:
            conn.close()
    return username_to_dbs


def build_sender_id_map(db_path):
    """Build mapping from Name2Id rowid -> username for a message DB."""
    id_map = {}
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT rowid, user_name FROM Name2Id").fetchall()
        for rowid, username in rows:
            id_map[rowid] = username
    finally:
        conn.close()
    return id_map


def format_message(row, is_group, contacts_map, sender_id_map=None, owner_wxid=None, voice_text=None):
    """Format a single message row."""
    local_id, local_type, create_time, sender_id, content, source, ct_flag = row

    ts = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S") if create_time else "?"
    type_name = MSG_TYPE_MAP.get(local_type, f"type:{local_type}")

    sender = ""
    body = content or ""

    # Decompress zstd content if needed
    if ct_flag == 4 and isinstance(body, bytes) and body[:4] == b'\x28\xb5\x2f\xfd':
        try:
            import zstandard
            dctx = zstandard.ZstdDecompressor()
            body = dctx.decompress(body).decode("utf-8", errors="replace")
        except:
            body = "(压缩内容解析失败)"

    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="replace")
        except:
            body = "(binary content)"

    # Resolve sender
    if is_group and body and ":\n" in body:
        parts = body.split(":\n", 1)
        raw_sender = parts[0]
        body = parts[1]
        sender = contacts_map.get(raw_sender, raw_sender)
    elif sender_id_map and sender_id:
        sender_username = sender_id_map.get(sender_id, "")
        if sender_username:
            if owner_wxid and sender_username == owner_wxid:
                sender = "我"
            else:
                sender = contacts_map.get(sender_username, sender_username)

    if not sender:
        sender = "未知"

    # Format body based on message type
    if local_type == 3:
        body = "[图片]- (图片消息)"
    elif local_type == 34:
        if voice_text:
            body = f"[语音]- {voice_text}"
        else:
            body = "[语音]- (语音消息)"
    elif local_type == 43:
        body = "[视频]- (视频消息)"
    elif local_type == 47:
        body = "[表情]- (表情消息)"
    elif local_type == 48:
        body = "[位置]- (位置消息)"
    elif local_type == 49:
        if body and "<title>" in body:
            import html as _html
            m = re.search(r"<title>(.*?)</title>", body)
            body = f"[链接]- {_html.unescape(m.group(1))}" if m else "[链接/文件]- (链接消息)"
        else:
            body = "[链接/文件]- (链接消息)"
    elif local_type == 42:
        body = "[名片]- (名片消息)"
    elif local_type == 10000:
        body = f"[系统]- {body[:100]}" if body else "[系统消息]"
    elif local_type == 10002:
        body = "[撤回]- (消息已撤回)"
    elif local_type != 1:
        if body and "<title>" in body:
            import html as _html
            m = re.search(r"<title>(.*?)</title>", body)
            if m:
                title = _html.unescape(m.group(1))
                if "<type>57</type>" in body:
                    body = f"[引用]- {title}"
                else:
                    body = f"[链接]- {title}"
            else:
                body = f"[{type_name}]- {body[:100]}"
        else:
            body = f"[{type_name}]- {body[:100]}" if body else f"[{type_name}]"

    return f"{sender}：{body} - {ts}"


def safe_filename(display_name, username):
    name = display_name or username
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = name.strip('. ')
    if not name:
        name = username.replace('@', '_at_')
    if len(name) > 80:
        name = name[:80]
    return name


def unique_output_path(directory, filename, username, used_paths):
    """Return a unique output path without overwriting same-name conversations."""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    if candidate not in used_paths and not os.path.exists(candidate):
        used_paths.add(candidate)
        return candidate

    suffix = hashlib.sha1(username.encode("utf-8")).hexdigest()[:8]
    candidate = os.path.join(directory, f"{base}_{suffix}{ext}")
    counter = 2
    while candidate in used_paths or os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base}_{suffix}_{counter}{ext}")
        counter += 1
    used_paths.add(candidate)
    return candidate


def match_contacts_filter(username, display_name, contacts_map, filter_list):
    """Check if a username matches any of the contact filters.

    Supports:
      - Exact wxid match: wxid_xxx
      - Exact display name match
      - Fuzzy substring match on display_name, nick_name, remark
      - Chatroom ID match: 12345@chatroom
    """
    if not filter_list:
        return True  # No filter = all pass

    display = display_name or contacts_map.get(username, username)

    for pattern in filter_list:
        pattern = pattern.strip()
        if not pattern:
            continue
        # Exact wxid/username match
        if pattern == username:
            return True
        # Exact display name match
        if pattern == display:
            return True
        # Fuzzy substring match (case-insensitive for English)
        if pattern.lower() in display.lower():
            return True
        if pattern.lower() in username.lower():
            return True

    return False


def parse_date(date_str):
    """Parse a date string, supports multiple formats."""
    if not date_str:
        return None
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析日期: '{date_str}'，支持格式: YYYY-MM-DD, YYYY-MM-DD HH:MM:SS")


def export_account(account, decrypted_dir, output_dir, config):
    """Export contacts and all chats for one account with filtering support.

    config: dict with keys:
      - transcribe: bool
      - fresh: bool
      - start_date: datetime or None
      - end_date: datetime or None
      - contacts_filter: list of strings or None
      - chat_type: 'private' | 'group' | 'all'
      - dry_run: bool
      - allow_incomplete: bool
    """
    transcribe = config.get("transcribe", False)
    fresh = config.get("fresh", False)
    start_date = config.get("start_date")
    end_date = config.get("end_date")
    contacts_filter = config.get("contacts_filter")
    chat_type = config.get("chat_type", "all")
    dry_run = config.get("dry_run", False)
    allow_incomplete = config.get("allow_incomplete", False)

    # Convert dates to timestamps
    start_ts = int(start_date.timestamp()) if start_date else None
    end_ts = int(end_date.timestamp()) if end_date else None

    print(f"\n{'='*60}")
    print(f"  Exporting Account {account}")
    print(f"  Source: {decrypted_dir}")
    print(f"  Output: {output_dir}")
    if start_date or end_date:
        range_str = f"  Time range: {start_date.strftime('%Y-%m-%d') if start_date else '开始'} ~ {end_date.strftime('%Y-%m-%d') if end_date else '至今'}"
        print(range_str)
    if contacts_filter:
        print(f"  Contacts filter: {', '.join(contacts_filter)}")
    if chat_type != "all":
        print(f"  Chat type: {chat_type}")
    if dry_run:
        print(f"  Mode: DRY-RUN (预览模式，不写文件)")
    print(f"{'='*60}\n")

    # Create output dirs (skip in dry-run)
    if not dry_run:
        os.makedirs(output_dir, exist_ok=True)

    # 1. Export contacts
    print("[1/2] Loading contacts...")
    contacts_list, contacts_map = load_contacts(decrypted_dir)

    friends = [c for c in contacts_list if c["type"] == "friend"]
    groups = [c for c in contacts_list if c["type"] == "group"]
    officials = [c for c in contacts_list if c["type"] == "official_account"]
    strangers = [c for c in contacts_list if c["type"] == "stranger"]
    print(f"  ✅ {len(contacts_list)} contacts loaded")
    print(
        f"     Friends: {len(friends)}, Groups: {len(groups)}, "
        f"Official Accounts: {len(officials)}, Strangers: {len(strangers)}"
    )

    if not dry_run:
        # Save contacts JSON
        contacts_file = os.path.join(output_dir, "contacts.json")
        with open(contacts_file, "w", encoding="utf-8") as f:
            json.dump(contacts_list, f, ensure_ascii=False, indent=2)

        # Save readable list
        contacts_txt = os.path.join(output_dir, "contacts_list.txt")
        with open(contacts_txt, "w", encoding="utf-8") as f:
            f.write(f"# WeChat Contacts - Account {account}\n")
            f.write(f"# Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Total: {len(contacts_list)}\n\n")
            f.write(f"## Friends ({len(friends)})\n")
            for c in sorted(friends, key=lambda x: x["display_name"]):
                remark_info = f" (备注: {c['remark']})" if c['remark'] and c['remark'] != c['nick_name'] else ""
                f.write(f"  {c['display_name']}{remark_info} | {c['username']}\n")
            f.write(f"\n## Groups ({len(groups)})\n")
            for c in sorted(groups, key=lambda x: x["display_name"]):
                f.write(f"  {c['display_name']} | {c['username']}\n")
            f.write(f"\n## Official Accounts ({len(officials)})\n")
            for c in sorted(officials, key=lambda x: x["display_name"]):
                f.write(f"  {c['display_name']} | {c['username']}\n")
            f.write(f"\n## Strangers ({len(strangers)})\n")
            for c in sorted(strangers, key=lambda x: x["display_name"]):
                f.write(f"  {c['display_name']} | {c['username']}\n")

    # 2. Export chat records
    print(f"\n[2/2] Exporting chat records...")

    # Progress tracking for resume support
    progress_file = os.path.join(output_dir, "export_progress.json") if not dry_run else None
    completed_usernames = set()

    progress_fingerprint = {
        "account": account,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "contacts_filter": contacts_filter,
        "chat_type": chat_type,
        "transcribe": transcribe,
    }

    if not dry_run and not fresh and progress_file and os.path.isfile(progress_file):
        try:
            with open(progress_file, "r") as f:
                progress_data = json.load(f)
            if progress_data.get("fingerprint") == progress_fingerprint:
                completed_usernames = set(progress_data.get("completed", []))
                print(f"  📋 Resuming: {len(completed_usernames)} conversations already completed")
            else:
                print("  📋 Existing progress file belongs to different filters; starting fresh")
        except:
            completed_usernames = set()

    # Only clean old files on fresh start
    chats_dir = os.path.join(output_dir, "chats")
    if not dry_run:
        if not completed_usernames:
            import shutil
            if os.path.isdir(chats_dir):
                shutil.rmtree(chats_dir)
            os.makedirs(chats_dir, exist_ok=True)
        else:
            os.makedirs(os.path.join(chats_dir, "private"), exist_ok=True)
            os.makedirs(os.path.join(chats_dir, "groups"), exist_ok=True)

    source_report = analyze_decrypted_sources(decrypted_dir)
    completeness_errors = validate_source_completeness(source_report, allow_incomplete=allow_incomplete)
    for warning in source_report.get("warnings", []):
        print(f"  ⚠️  {warning}")
    for error in completeness_errors:
        print(f"  ⚠️  {error}")

    msg_dbs = get_all_msg_dbs(decrypted_dir)
    if not msg_dbs:
        print("  ⚠️  No message databases found!")
        return

    print(
        f"  Found {len(msg_dbs)} chat databases "
        f"({len(source_report['message_dbs'])} message, {len(source_report['biz_message_dbs'])} biz_message)"
    )

    # Detect account owner wxid
    OWNER_WXIDS = {
        "1": "wxid_9gngtzl6t2h922",
        "2": "wangchzng_bc19",
    }
    owner_wxid = OWNER_WXIDS.get(account)
    if owner_wxid:
        print(f"  Account owner: {owner_wxid}")

    # Build sender_id_map per DB
    sender_id_maps = {}
    for db_path in msg_dbs:
        sender_id_maps[db_path] = build_sender_id_map(db_path)

    # Collect all usernames (each username -> list of DBs containing it)
    username_to_dbs = collect_all_usernames(msg_dbs)
    print(f"  Found {len(username_to_dbs)} conversations total")

    # Build table index
    all_tables = {}
    for db_path in msg_dbs:
        conn = sqlite3.connect(db_path)
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
                ).fetchall()
            }
            all_tables[db_path] = tables
        finally:
            conn.close()

    # Pre-filter conversations by type and contact filter
    filtered_list = []
    for username, db_paths in sorted(username_to_dbs.items()):
        is_group = "@chatroom" in username

        # Type filter
        if chat_type == "private" and is_group:
            continue
        if chat_type == "group" and not is_group:
            continue

        # Contact filter
        display_name = contacts_map.get(username, "")
        if contacts_filter and not match_contacts_filter(username, display_name, contacts_map, contacts_filter):
            continue

        filtered_list.append((username, db_paths))

    print(f"  After filtering: {len(filtered_list)} conversations match criteria")

    if not filtered_list:
        print("  ⚠️  No conversations match the given filters!")
        return

    # DRY-RUN: show preview and exit
    if dry_run:
        print(f"\n  {'─'*50}")
        print(f"  📋 DRY-RUN 预览 — 将导出以下对话：")
        print(f"  {'─'*50}")

        private_count = 0
        group_count = 0
        total_msg_estimate = 0

        for username, db_paths in filtered_list:
            is_group = "@chatroom" in username
            display = contacts_map.get(username, username)
            table = username_to_table(username)

            # Count messages across ALL databases for this conversation
            total_count = 0
            min_time = None
            max_time = None
            for dbp in db_paths:
                if table not in all_tables.get(dbp, set()):
                    continue
                conn = sqlite3.connect(dbp)
                try:
                    where_clauses = []
                    if start_ts:
                        where_clauses.append(f"create_time >= {start_ts}")
                    if end_ts:
                        where_clauses.append(f"create_time <= {end_ts}")
                    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
                    count = conn.execute(f"SELECT count(*) FROM [{table}]{where_sql}").fetchone()[0]
                    total_count += count
                    if count > 0:
                        time_row = conn.execute(
                            f"SELECT min(create_time), max(create_time) FROM [{table}]{where_sql}"
                        ).fetchone()
                        if time_row[0]:
                            if min_time is None or time_row[0] < min_time:
                                min_time = time_row[0]
                        if time_row[1]:
                            if max_time is None or time_row[1] > max_time:
                                max_time = time_row[1]
                except:
                    pass
                finally:
                    conn.close()

            if total_count == 0:
                continue

            min_ts_str = datetime.fromtimestamp(min_time).strftime("%Y-%m-%d") if min_time else "?"
            max_ts_str = datetime.fromtimestamp(max_time).strftime("%Y-%m-%d") if max_time else "?"

            type_label = "群聊" if is_group else "私聊"
            print(f"    {'🏘️' if is_group else '👤'} [{type_label}] {display} — {total_count} 条消息 ({min_ts_str} ~ {max_ts_str})")

            total_msg_estimate += total_count
            if is_group:
                group_count += 1
            else:
                private_count += 1

        print(f"\n  {'─'*50}")
        print(f"  📊 预览统计：")
        print(f"     私聊: {private_count} 个")
        print(f"     群聊: {group_count} 个")
        print(f"     预估消息总量: {total_msg_estimate:,} 条")
        if start_date or end_date:
            print(f"     时间范围: {start_date.strftime('%Y-%m-%d') if start_date else '最早'} ~ {end_date.strftime('%Y-%m-%d') if end_date else '最新'}")
        print(f"  {'─'*50}")
        print(f"\n  💡 去掉 --dry-run 参数即可执行实际导出")
        return

    # --- Actual export ---
    exported = 0
    skipped = 0
    resumed = 0
    total_messages = 0
    export_details = []  # For export log

    def save_progress():
        if progress_file:
            with open(progress_file, "w") as f:
                json.dump({
                    "fingerprint": progress_fingerprint,
                    "completed": sorted(completed_usernames),
                    "exported": exported,
                    "total_messages": total_messages,
                    "last_update": datetime.now().isoformat(),
                }, f, ensure_ascii=False, indent=2)

    total_conversations = len(filtered_list)
    used_output_paths = set()

    for idx, (username, db_paths) in enumerate(filtered_list, 1):
        table = username_to_table(username)

        # Find which DBs actually have this table
        valid_db_paths = [dbp for dbp in db_paths if table in all_tables.get(dbp, set())]
        if not valid_db_paths:
            skipped += 1
            continue

        # Skip already completed conversations (resume support)
        if username in completed_usernames:
            resumed += 1
            continue

        is_group = "@chatroom" in username
        try:
            # Build time filter SQL
            where_clauses = []
            if start_ts:
                where_clauses.append(f"create_time >= {start_ts}")
            if end_ts:
                where_clauses.append(f"create_time <= {end_ts}")
            where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            # Query ALL databases and merge rows by create_time
            all_rows = []
            for dbp in valid_db_paths:
                conn = sqlite3.connect(dbp)
                try:
                    db_rows = conn.execute(
                        f"SELECT local_id, local_type, create_time, real_sender_id, "
                        f"message_content, source, WCDB_CT_message_content FROM [{table}]"
                        f"{where_sql} ORDER BY create_time ASC"
                    ).fetchall()
                    # Tag each row with db_path for sender resolution
                    for r in db_rows:
                        all_rows.append((r, dbp))
                finally:
                    conn.close()

            if not all_rows:
                skipped += 1
                continue

            # Sort all rows by create_time (index 2 in the row tuple)
            all_rows.sort(key=lambda x: x[0][2])

            # Build voice transcription map if enabled
            voice_text_map = {}
            if transcribe and not is_group:
                has_voice = any(r[0][1] == 34 for r in all_rows)
                if has_voice:
                    voice_data_map = build_voice_data_map(decrypted_dir, username)
                    if voice_data_map:
                        voice_count = len(voice_data_map)
                        display = contacts_map.get(username, username)
                        print(f"    🎤 [{idx}/{total_conversations}] Transcribing {voice_count} voices for {display}...")
                        done = 0
                        for lid, vdata in voice_data_map.items():
                            text = transcribe_silk(vdata)
                            if text:
                                voice_text_map[lid] = text
                            done += 1
                            if done % 50 == 0:
                                print(f"       {done}/{voice_count}...")
                        print(f"       ✓ Done ({len(voice_text_map)} transcribed)")

            lines = []
            for r, dbp in all_rows:
                local_id = r[0]
                sid_map = sender_id_maps.get(dbp, {})
                vt = voice_text_map.get(local_id) if voice_text_map else None
                lines.append(format_message(r, is_group, contacts_map, sid_map, owner_wxid, voice_text=vt))
            total_messages += len(lines)

            display_name = contacts_map.get(username, "")
            fname = safe_filename(display_name, username)

            sub_dir = os.path.join(chats_dir, "groups" if is_group else "private")
            os.makedirs(sub_dir, exist_ok=True)

            output_path = unique_output_path(sub_dir, f"{fname}.txt", username, used_output_paths)

            # Get actual time range of exported messages
            first_ts = all_rows[0][0][2] if all_rows else None
            last_ts = all_rows[-1][0][2] if all_rows else None
            count = len(lines)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"# Chat: {display_name or username} ({username})\n")
                f.write(f"# Messages: {count}\n")
                f.write(f"# Type: {'Group' if is_group else 'Private'}\n")
                f.write(f"# Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                if first_ts and last_ts:
                    f.write(f"# Time range: {datetime.fromtimestamp(first_ts).strftime('%Y-%m-%d')} ~ {datetime.fromtimestamp(last_ts).strftime('%Y-%m-%d')}\n")
                f.write("\n")
                f.write("\n".join(lines))
                f.write("\n")

            # Record export details for log
            export_details.append({
                "username": username,
                "display_name": display_name or username,
                "type": "group" if is_group else "private",
                "messages": count,
                "first_message": datetime.fromtimestamp(first_ts).isoformat() if first_ts else None,
                "last_message": datetime.fromtimestamp(last_ts).isoformat() if last_ts else None,
                "file": os.path.basename(output_path),
                "source_databases": sorted(os.path.basename(p) for p in valid_db_paths),
            })

            exported += 1
            completed_usernames.add(username)

            save_progress()

            if exported % 100 == 0:
                print(f"    📊 Progress: {exported} exported, {idx}/{total_conversations} processed")

        except Exception as e:
            print(f"  ❌ [{idx}/{total_conversations}] {username}: {e}")

    print(f"  ✅ {exported} conversations exported ({total_messages:,} messages total)")
    if resumed:
        print(f"     Resumed: {resumed} (already completed)")
    print(f"     Skipped: {skipped} (empty or no table)")
    print(f"     -> {chats_dir}/private/")
    print(f"     -> {chats_dir}/groups/")

    # Write summary.json
    summary = {
        "account": account,
        "export_time": datetime.now().isoformat(),
        "contacts_total": len(contacts_list),
        "contacts_friends": len(friends),
        "contacts_groups": len(groups),
        "contacts_official_accounts": len(officials),
        "contacts_strangers": len(strangers),
        "message_databases": len(source_report["message_dbs"]),
        "biz_message_databases": len(source_report["biz_message_dbs"]),
        "media_databases": len(source_report["media_dbs"]),
        "conversations_exported": exported,
        "messages_total": total_messages,
    }
    with open(os.path.join(output_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # Write export_log.json (detailed export manifest)
    export_log = {
        "export_time": datetime.now().isoformat(),
        "account": account,
        "owner_wxid": owner_wxid,
        "output_dir": output_dir,
        "source_report": source_report,
        "filters": {
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "contacts_filter": contacts_filter,
            "chat_type": chat_type,
            "transcribe": transcribe,
        },
        "statistics": {
            "total_conversations_in_db": len(username_to_dbs),
            "conversations_matched_filter": total_conversations,
            "conversations_exported": exported,
            "conversations_skipped_empty": skipped,
            "total_messages_exported": total_messages,
            "private_chats": len([d for d in export_details if d["type"] == "private"]),
            "group_chats": len([d for d in export_details if d["type"] == "group"]),
        },
        "exported_conversations": export_details,
    }
    log_path = os.path.join(output_dir, "export_log.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(export_log, f, ensure_ascii=False, indent=2)

    print(f"\n  📊 Summary: {output_dir}/summary.json")
    print(f"  📝 Export log: {output_dir}/export_log.json")


def main():
    parser = argparse.ArgumentParser(
        description="WeChat 聊天记录导出工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 全量导出账号1
  python3 export_all.py --account 1

  # 导出2025年的记录
  python3 export_all.py -a 1 --start-date 2025-01-01 --end-date 2025-12-31

  # 只导出某几个人的私聊
  python3 export_all.py -a 1 --contacts "张三,李四,王五" --type private

  # 只导出群聊
  python3 export_all.py -a 1 --type group

  # 模糊匹配导出（名称中包含"工作"的群）
  python3 export_all.py -a 1 --contacts "工作" --type group

  # 预览将导出什么（不实际写文件）
  python3 export_all.py -a 1 --contacts "张三" --dry-run

  # 联合条件：导出张三2025年3月的私聊并转写语音
  python3 export_all.py -a 1 --contacts "张三" --start-date 2025-03-01 --end-date 2025-03-31 --transcribe
        """
    )
    parser.add_argument(
        "--account", "-a", choices=["1", "2", "all"], default="all",
        help="导出哪个账号 (default: all)"
    )
    parser.add_argument(
        "--start-date", "-s", type=str, default=None,
        help="消息起始时间 (格式: YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"
    )
    parser.add_argument(
        "--end-date", "-e", type=str, default=None,
        help="消息截止时间 (格式: YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"
    )
    parser.add_argument(
        "--contacts", "-c", type=str, default=None,
        help="导出指定联系人/群聊 (逗号分隔, 支持模糊匹配). 例: '张三,李四' 或 '工作群'"
    )
    parser.add_argument(
        "--type", choices=["private", "group", "all"], default="all",
        help="聊天类型: private(私聊), group(群聊), all(全部)"
    )
    parser.add_argument(
        "--transcribe", "-t", action="store_true",
        help="启用语音转写 (使用 Whisper, 较慢约 0.4s/条)"
    )
    parser.add_argument(
        "--fresh", action="store_true",
        help="忽略已有进度，从头开始导出"
    )
    parser.add_argument(
        "--dry-run", "-d", action="store_true",
        help="预览模式: 显示将导出的内容但不实际写文件"
    )
    parser.add_argument(
        "--allow-incomplete", action="store_true",
        help="允许在解密清单或分片库不完整时继续导出（默认会阻止明显不完整的导出）"
    )
    parser.add_argument(
        "--output", "-o", type=str, default=None,
        help="输出基础目录 (默认: ./output 或 WECHAT_EXPORT_OUTPUT 环境变量)"
    )
    parser.add_argument(
        "--decrypted-dir", type=str, default=None,
        help="解密数据库目录路径 (默认: ./decrypted_{account})"
    )
    args = parser.parse_args()

    # Parse dates
    start_date = parse_date(args.start_date) if args.start_date else None
    end_date = parse_date(args.end_date) if args.end_date else None

    # If end_date is just a date (no time), set to end of day
    if end_date and end_date.hour == 0 and end_date.minute == 0 and end_date.second == 0:
        end_date = end_date.replace(hour=23, minute=59, second=59)

    # Parse contacts filter
    contacts_filter = None
    if args.contacts:
        contacts_filter = [c.strip() for c in args.contacts.split(",") if c.strip()]

    # Build config
    config = {
        "transcribe": args.transcribe,
        "fresh": args.fresh,
        "start_date": start_date,
        "end_date": end_date,
        "contacts_filter": contacts_filter,
        "chat_type": args.type,
        "dry_run": args.dry_run,
        "allow_incomplete": args.allow_incomplete,
    }

    output_base = args.output if args.output else BASE_OUTPUT
    accounts = ["1", "2"] if args.account == "all" else [args.account]

    for acct in accounts:
        decrypted_dir = args.decrypted_dir if args.decrypted_dir else os.path.join(TOOL_DIR, f"decrypted_{acct}")

        if not os.path.isdir(decrypted_dir):
            print(f"[-] Decrypted directory not found: {decrypted_dir}")
            print(f"    Run: python3 decrypt_db.py --account {acct} -o decrypted_{acct}")
            continue

        # Auto-generate output folder name: {nickname}-{timestamp}
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        owner_nick = get_owner_nickname(decrypted_dir, acct)
        if owner_nick:
            # Sanitize nickname for folder name
            folder_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', owner_nick).strip()
            if not folder_name:
                folder_name = f"account_{acct}"
        else:
            folder_name = f"account_{acct}"

        output_dir = os.path.join(output_base, f"{folder_name}-{timestamp}")

        # In dry-run, skip folder creation
        if not args.dry_run:
            os.makedirs(output_dir, exist_ok=True)

        export_account(acct, decrypted_dir, output_dir, config)

    print(f"\n{'='*60}")
    print(f"  All done! Output location: {output_base}/")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
