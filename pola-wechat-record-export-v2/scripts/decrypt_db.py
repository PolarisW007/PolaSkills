#!/usr/bin/env python3
"""
Decrypt WeChat SQLCipher databases to plaintext SQLite files.

Requirements:
    brew install sqlcipher

Usage:
    python3 decrypt_db.py                  # decrypt all databases
    python3 decrypt_db.py -o ./decrypted   # specify output directory
"""

import json
import os
import subprocess
import sys
import glob
import argparse
from datetime import datetime

DB_DIRS = {
    "1": os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    ),
    "2": os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat2/Data/Documents/xwechat_files"
    ),
}
PAGE_SZ = 4096
SALT_SZ = 16


def find_db_dir(account="1"):
    DB_DIR = DB_DIRS.get(account, DB_DIRS["1"])
    pattern = os.path.join(DB_DIR, "*", "db_storage")
    candidates = glob.glob(pattern)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        return candidates[0]
    if os.path.isdir(DB_DIR) and os.path.basename(DB_DIR) == "db_storage":
        return DB_DIR
    return None


def find_sqlcipher():
    brew_path = "/opt/homebrew/opt/sqlcipher/bin/sqlcipher"
    if os.path.isfile(brew_path):
        return brew_path
    for p in os.environ.get("PATH", "").split(":"):
        candidate = os.path.join(p, "sqlcipher")
        if os.path.isfile(candidate):
            return candidate
    return None


def collect_source_databases(db_dir):
    """Return all encrypted WeChat DB relative paths under db_storage."""
    rel_paths = []
    for root, _, files in os.walk(db_dir):
        for filename in files:
            if not filename.endswith(".db"):
                continue
            if filename.endswith("-wal") or filename.endswith("-shm"):
                continue
            abs_path = os.path.join(root, filename)
            rel_paths.append(os.path.relpath(abs_path, db_dir))
    return sorted(rel_paths)


def decrypt_database(sqlcipher_bin, src_path, dst_path, key_hex):
    """Decrypt a SQLCipher database to a plaintext SQLite file."""
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)

    # Remove existing decrypted file if present
    if os.path.exists(dst_path):
        os.remove(dst_path)

    sql_commands = f"""PRAGMA key = "x'{key_hex}'";
PRAGMA cipher_page_size = 4096;
ATTACH DATABASE '{dst_path}' AS plaintext KEY '';
SELECT sqlcipher_export('plaintext');
DETACH DATABASE plaintext;
"""

    try:
        result = subprocess.run(
            [sqlcipher_bin, src_path],
            input=sql_commands,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0 or "Error" in result.stderr:
            return False, result.stderr.strip()

        # Verify the decrypted file
        if not os.path.isfile(dst_path) or os.path.getsize(dst_path) == 0:
            return False, "output file is empty"

        return True, "OK"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(description="Decrypt WeChat databases")
    parser.add_argument(
        "--account", "-a", choices=["1", "2"], default="1",
        help="Which WeChat account: 1=xinWeChat, 2=xinWeChat2 (default: 1)"
    )
    parser.add_argument(
        "--keys",
        default=None,
        help="Path to wechat_keys.json (default: wechat_keys_<account>.json)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="decrypted",
        help="Output directory for decrypted databases (default: decrypted)",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Allow decrypting only DBs present in the key file; default fails if source DBs are missing keys",
    )
    args = parser.parse_args()

    keys_file = args.keys or f"wechat_keys_{args.account}.json"
    if not os.path.isfile(keys_file):
        print(f"[-] Key file not found: {keys_file}")
        sys.exit(1)

    with open(keys_file, "r") as f:
        data = json.load(f)

    sqlcipher_bin = find_sqlcipher()
    if not sqlcipher_bin:
        print("[-] sqlcipher not found. Install it with: brew install sqlcipher")
        sys.exit(1)
    print(f"[*] Using sqlcipher: {sqlcipher_bin}")

    db_dir = find_db_dir(args.account)
    if not db_dir:
        DB_DIR = DB_DIRS.get(args.account, DB_DIRS["1"])
        print(f"[-] Could not find db_storage directory under {DB_DIR}")
        sys.exit(1)
    print(f"[*] DB storage: {db_dir}")

    source_dbs = collect_source_databases(db_dir)
    entries = {k: v for k, v in data.items() if not k.startswith("__")}
    missing_key_sources = sorted(set(source_dbs) - set(entries))
    missing_source_files = sorted(set(entries) - set(source_dbs))
    print(f"[*] Decrypting {len(entries)} databases to {args.output}/\n")
    if missing_key_sources:
        print(f"[!] {len(missing_key_sources)} source databases have no key entry.")
        for rel in missing_key_sources[:20]:
            print(f"    missing key: {rel}")
        if len(missing_key_sources) > 20:
            print(f"    ... and {len(missing_key_sources) - 20} more")
        if not args.allow_missing:
            print("[!] Refusing partial decrypt. Re-run with --allow-missing only if partial export is intentional.")
            manifest = {
                "account": args.account,
                "created_at": datetime.now().isoformat(),
                "source_db_count": len(source_dbs),
                "key_entry_count": len(entries),
                "passed": [],
                "failed": [],
                "missing_key_sources": missing_key_sources,
                "missing_source_files": missing_source_files,
            }
            os.makedirs(args.output, exist_ok=True)
            with open(os.path.join(args.output, "decrypt_manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            sys.exit(1)
    if missing_source_files:
        print(f"[!] {len(missing_source_files)} key entries do not have source files.")

    passed = 0
    failed = 0
    passed_entries = []
    failed_entries = []

    for db_rel_path, key_hex in sorted(entries.items()):
        src = os.path.join(db_dir, db_rel_path)
        dst = os.path.join(args.output, db_rel_path)

        if not os.path.isfile(src):
            print(f"  ⏭️  {db_rel_path}: source file not found, skipping")
            missing_source_files.append(db_rel_path)
            continue

        success, detail = decrypt_database(sqlcipher_bin, src, dst, key_hex)
        if success:
            dst_size = os.path.getsize(dst)
            print(f"  ✅ {db_rel_path} -> {dst} ({dst_size / 1024:.0f} KB)")
            passed += 1
            passed_entries.append({"path": db_rel_path, "size": dst_size})
        else:
            print(f"  ❌ {db_rel_path}: {detail}")
            failed += 1
            failed_entries.append({"path": db_rel_path, "error": detail})

    print(f"\n[*] Done: {passed} decrypted, {failed} failed")
    manifest = {
        "account": args.account,
        "created_at": datetime.now().isoformat(),
        "source_db_count": len(source_dbs),
        "key_entry_count": len(entries),
        "passed_count": passed,
        "failed_count": failed,
        "passed": passed_entries,
        "failed": failed_entries,
        "missing_key_sources": missing_key_sources,
        "missing_source_files": sorted(set(missing_source_files)),
    }
    os.makedirs(args.output, exist_ok=True)
    manifest_path = os.path.join(args.output, "decrypt_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"[*] Manifest saved to: {os.path.abspath(manifest_path)}")
    if passed > 0:
        print(f"[*] Decrypted files saved to: {os.path.abspath(args.output)}/")
        print(f"[*] You can now run: python3 export_all.py --account {args.account} --decrypted-dir {args.output}")
    if failed > 0 or missing_source_files:
        sys.exit(1)


if __name__ == "__main__":
    main()
