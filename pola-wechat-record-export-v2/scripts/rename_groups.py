#!/usr/bin/env python3
"""Rename group chat files from chatroom ID to group name.

Usage:
    python3 rename_groups.py [--account N] [--output DIR]
"""
import sqlite3
import os
import re
import json
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_config(args=None):
    """Get config from args or defaults."""
    acct = args.account if args else "1"
    decrypted = args.decrypted_dir if args and args.decrypted_dir else os.path.join(BASE_DIR, f"decrypted_{acct}")
    output_base = args.output if args and args.output else os.environ.get("WECHAT_EXPORT_OUTPUT", os.path.join(BASE_DIR, "output"))
    groups_dir = os.path.join(output_base, f"account_{acct}", "chats", "groups")
    return decrypted, groups_dir

def safe_filename(display_name, username):
    """Generate a safe filename."""
    name = display_name if display_name and display_name != username else username
    # Remove illegal filesystem chars
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '', name)
    name = name.strip('. ')
    if not name:
        name = username
    return name[:80]

def load_group_names(decrypted_dir, groups_dir):
    """Load group names from all available sources."""
    group_names = {}

    # Source 1: contact.db - contact table
    contact_db = os.path.join(decrypted_dir, "contact", "contact.db")
    if os.path.isfile(contact_db):
        conn = sqlite3.connect(contact_db)
        try:
            rows = conn.execute(
                "SELECT username, nick_name, remark FROM contact WHERE username LIKE '%@chatroom'"
            ).fetchall()
            for username, nick, remark in rows:
                name = remark or nick
                if name:
                    group_names[username] = name
        except:
            pass
        conn.close()

    # Source 2: session.db - SessionNoContactInfoTable
    session_db = os.path.join(decrypted_dir, "session", "session.db")
    if os.path.isfile(session_db):
        conn = sqlite3.connect(session_db)
        try:
            rows = conn.execute(
                "SELECT username, session_title FROM SessionNoContactInfoTable WHERE username LIKE '%@chatroom'"
            ).fetchall()
            for username, title in rows:
                if title and username not in group_names:
                    group_names[username] = title
        except:
            pass
        conn.close()

    # Source 3: Try to extract from system messages in chat history
    # System messages like "XX修改群名为YY" contain group names
    msg_dir = os.path.join(decrypted_dir, "message")
    if os.path.isdir(msg_dir):
        for db_file in sorted(os.listdir(msg_dir)):
            if not db_file.endswith('.db'):
                continue
            db_path = os.path.join(msg_dir, db_file)
            try:
                conn = sqlite3.connect(db_path)
                tables = [t[0] for t in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%@chatroom'"
                ).fetchall()]

                for table in tables:
                    if table in group_names:
                        continue
                    # Look for group name change system messages
                    try:
                        sys_msgs = conn.execute(
                            f"SELECT message_content FROM [{table}] WHERE local_type=10000 "
                            f"AND (message_content LIKE '%修改群名为%' OR message_content LIKE '%群名为%') "
                            f"ORDER BY create_time DESC LIMIT 1"
                        ).fetchall()
                        for msg in sys_msgs:
                            content = str(msg[0]) if msg[0] else ''
                            # Pattern: "XX修改群名为"YY""
                            m = re.search(r'修改群名为"(.+?)"', content)
                            if m:
                                group_names[table] = m.group(1)
                                break
                    except:
                        pass
                conn.close()
            except:
                pass

    # Source 4: Try to extract from exported txt files (system messages with group name)
    if os.path.isdir(groups_dir):
        for filename in os.listdir(groups_dir):
            if not filename.endswith('.txt') or '@chatroom' not in filename:
                continue
            username = filename[:-4]
            if username in group_names:
                continue
            filepath = os.path.join(groups_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                # Find the LAST group name change (most current name)
                matches = re.findall(r'修改群名为"(.+?)"', content)
                if matches:
                    group_names[username] = matches[-1]  # Use the latest name
            except:
                pass

    # Source 5: Construct name from first few active speakers in the chat file
    if os.path.isdir(groups_dir):
        for filename in os.listdir(groups_dir):
            if not filename.endswith('.txt'):
                continue
            filepath = os.path.join(groups_dir, filename)
            # Read chatroom ID from file header
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    first_line = f.readline()
                m = re.search(r'\((\d+@chatroom)\)', first_line)
                if not m:
                    continue
                username = m.group(1)
            except:
                continue

            if username in group_names:
                continue

            try:
                speakers = []
                with open(filepath, 'r', encoding='utf-8') as f:
                    for i, line in enumerate(f):
                        if i < 5:  # skip header
                            continue
                        if i > 300:
                            break
                        # Extract speaker name (format: "SpeakerName：content - timestamp")
                        m = re.match(r'^(.+?)：', line)
                        if m:
                            name = m.group(1)
                            # Skip wxid_, chatroom IDs, and generic names
                            if (name not in ('未知', '我')
                                and not name.startswith('wxid_')
                                and '@chatroom' not in name
                                and '@openim' not in name
                                and not re.match(r'^[a-z]{2,}\d{5,}$', name)
                                and name not in speakers):
                                speakers.append(name)
                            if len(speakers) >= 3:
                                break
                if speakers:
                    group_names[username] = "、".join(speakers[:3]) + "..."
            except:
                pass

    return group_names

def rename_group_files(decrypted_dir, groups_dir):
    """Rename group files from chatroom ID to group name."""
    if not os.path.isdir(groups_dir):
        print(f"Groups directory not found: {groups_dir}")
        return

    group_names = load_group_names(decrypted_dir, groups_dir)
    print(f"Loaded {len(group_names)} group names from database")

    # Find files that still use chatroom ID as name (or have ugly wxid-based names)
    renamed = 0
    skipped = 0
    not_found = []

    for filename in sorted(os.listdir(groups_dir)):
        if not filename.endswith('.txt'):
            continue

        # Get the chatroom ID from the file header
        filepath = os.path.join(groups_dir, filename)
        username = None
        with open(filepath, 'r', encoding='utf-8') as f:
            first_line = f.readline()
            # Header format: # Chat: DisplayName (chatroom_id@chatroom)
            m = re.search(r'\((\d+@chatroom)\)', first_line)
            if m:
                username = m.group(1)

        if not username:
            continue

        # Check if current filename already matches the best name
        if username not in group_names:
            if '@chatroom' in filename or 'wxid_' in filename:
                not_found.append(username)
                skipped += 1
            continue

        new_name = safe_filename(group_names[username], username)
        new_filename = f"{new_name}.txt"

        if new_filename == filename:
            continue  # Already correct

        new_path = os.path.join(groups_dir, new_filename)
        old_path = filepath

        # Handle name collision
        if os.path.exists(new_path) and new_path != old_path:
            new_path = os.path.join(groups_dir, f"{new_name}_{username.split('@')[0]}.txt")

        os.rename(old_path, new_path)
        print(f"  ✓ {filename} -> {os.path.basename(new_path)}")
        renamed += 1

    print(f"\nResult: {renamed} renamed, {skipped} could not resolve (group name not in DB)")
    if not_found:
        print(f"Unresolved groups (likely left/deleted):")
        for g in not_found[:10]:
            print(f"  - {g}")
        if len(not_found) > 10:
            print(f"  ... and {len(not_found) - 10} more")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rename group chat files from chatroom ID to group name")
    parser.add_argument("--account", type=str, default="1", help="Account number (default: 1)")
    parser.add_argument("--output", type=str, default=None, help="Output base directory")
    parser.add_argument("--decrypted-dir", type=str, default=None, help="Path to decrypted database directory")
    args = parser.parse_args()

    decrypted_dir, groups_dir = get_config(args)
    rename_group_files(decrypted_dir, groups_dir)
