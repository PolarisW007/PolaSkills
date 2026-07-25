#!/usr/bin/env python3
"""
Enhanced WeChat database key extractor — multi-strategy memory scanner.

Strategies:
  A) Hex string pattern: x'<64hex_key><32hex_salt>'
  B) Salt-proximity search: find raw salt bytes in memory, check nearby for key
  C) Brute-force: try every 32-byte aligned block against unresolved DBs

Usage:
    # With LLDB Python (requires SIP disabled):
    PYTHONPATH=$(lldb -P) sudo python3 find_key_enhanced.py --account 2

    # Specify PID explicitly:
    PYTHONPATH=$(lldb -P) sudo python3 find_key_enhanced.py --account 2 --pid 5766
"""

import sys
import os
import re
import glob
import json
import struct
import hashlib
import hmac as hmac_mod
import argparse
import time

import lldb

CONTAINERS = {
    "1": "com.tencent.xinWeChat",
    "2": "com.tencent.xinWeChat2",
}

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16

HEX_PATTERN = re.compile(rb"x'([0-9a-fA-F]{64,192})'")


def find_db_dir(container):
    db_dir = os.path.expanduser(
        f"~/Library/Containers/{container}/Data/Documents/xwechat_files"
    )
    pattern = os.path.join(db_dir, "*", "db_storage")
    candidates = glob.glob(pattern)
    if candidates:
        return candidates[0], db_dir
    return None, db_dir


def collect_db_files(db_dir):
    """Collect all .db files with their first page and salt."""
    db_files = []
    salt_to_dbs = {}

    for root, dirs, files in os.walk(db_dir):
        for f in files:
            if not f.endswith(".db"):
                continue
            if f.endswith("-wal") or f.endswith("-shm"):
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, db_dir)
            sz = os.path.getsize(path)
            if sz < PAGE_SZ:
                continue
            with open(path, "rb") as fh:
                page1 = fh.read(PAGE_SZ)
            salt = page1[:SALT_SZ]
            salt_hex = salt.hex()
            db_files.append({
                "rel": rel,
                "path": path,
                "size": sz,
                "salt_hex": salt_hex,
                "salt_bytes": salt,
                "page1": page1,
            })
            salt_to_dbs.setdefault(salt_hex, []).append(rel)

    return db_files, salt_to_dbs


def verify_key_for_db(enc_key_bytes, page1):
    """Verify enc_key can decrypt this DB's page 1 using HMAC-SHA512."""
    salt = page1[:SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key_bytes, mac_salt, 2, dklen=KEY_SZ)

    hmac_data = page1[SALT_SZ: PAGE_SZ - 80 + 16]
    stored_hmac = page1[PAGE_SZ - 64: PAGE_SZ]

    h = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    h.update(struct.pack("<I", 1))
    return h.digest() == stored_hmac


def try_key_for_remaining(enc_key_bytes, db_files, remaining_salts, key_map, salt_to_dbs, source=""):
    """Try a key candidate against all unresolved databases."""
    found = []
    for db in db_files:
        if db["salt_hex"] not in remaining_salts:
            continue
        if verify_key_for_db(enc_key_bytes, db["page1"]):
            salt_hex = db["salt_hex"]
            key_map[salt_hex] = enc_key_bytes.hex()
            remaining_salts.discard(salt_hex)
            dbs = salt_to_dbs[salt_hex]
            found.append(salt_hex)
            print(f"\n  [FOUND via {source}] salt={salt_hex}")
            print(f"    key={enc_key_bytes.hex()}")
            print(f"    databases: {', '.join(dbs)}")
    return found


def strategy_a_hex_patterns(data, db_files, remaining_salts, key_map, salt_to_dbs):
    """Strategy A: Search for x'<hex>' patterns."""
    count = 0
    for m in HEX_PATTERN.finditer(data):
        hex_str = m.group(1).decode()
        hex_len = len(hex_str)

        if hex_len == 96:
            enc_key_hex = hex_str[:64]
            salt_hex = hex_str[64:]
        elif hex_len == 64:
            enc_key_hex = hex_str
            salt_hex = None
        elif hex_len > 96 and hex_len % 2 == 0:
            enc_key_hex = hex_str[:64]
            salt_hex = hex_str[-32:]
        else:
            continue

        if salt_hex and salt_hex in remaining_salts:
            enc_key = bytes.fromhex(enc_key_hex)
            try_key_for_remaining(enc_key, db_files, remaining_salts, key_map, salt_to_dbs, "hex-pattern")
            count += 1
        elif salt_hex is None and remaining_salts:
            enc_key = bytes.fromhex(enc_key_hex)
            try_key_for_remaining(enc_key, db_files, remaining_salts, key_map, salt_to_dbs, "hex-key-only")
            count += 1
    return count


def strategy_b_salt_proximity(data, data_offset, db_files, remaining_salts, key_map, salt_to_dbs):
    """Strategy B: Find raw salt bytes in memory, check nearby for key."""
    count = 0
    for db in db_files:
        if db["salt_hex"] not in remaining_salts:
            continue
        salt_bytes = db["salt_bytes"]
        # Search for salt occurrences in data
        start = 0
        while True:
            pos = data.find(salt_bytes, start)
            if pos == -1:
                break
            start = pos + 1

            # Check various offsets around the salt for a potential 32-byte key
            # Common layouts:
            #   [key(32)][salt(16)]  -> key at pos-32
            #   [salt(16)][key(32)]  -> key at pos+16
            #   [salt(16)][...padding...][key(32)]  -> key at pos+16..pos+64
            #   [key(32)][...padding...][salt(16)]  -> key at pos-64..pos-32
            offsets_to_try = [
                pos - 32,       # key immediately before salt
                pos + 16,       # key immediately after salt
                pos - 40,       # key 8 bytes before salt
                pos + 24,       # key 8 bytes after salt
                pos - 48,       # key 16 bytes before
                pos + 32,       # key 16 bytes after
                pos - 64,       # key 32 bytes before
                pos + 48,       # key 32 bytes after
                pos + 64,       # further away
                pos - 96,       # further away
            ]

            for key_offset in offsets_to_try:
                if key_offset < 0 or key_offset + KEY_SZ > len(data):
                    continue
                candidate = data[key_offset:key_offset + KEY_SZ]
                # Skip null or obviously invalid keys
                if candidate == b'\x00' * KEY_SZ:
                    continue
                if verify_key_for_db(candidate, db["page1"]):
                    key_map[db["salt_hex"]] = candidate.hex()
                    remaining_salts.discard(db["salt_hex"])
                    dbs = salt_to_dbs[db["salt_hex"]]
                    print(f"\n  [FOUND via salt-proximity] salt={db['salt_hex']}")
                    print(f"    key={candidate.hex()}")
                    print(f"    databases: {', '.join(dbs)}")
                    print(f"    location: offset {key_offset} near salt at {pos}")
                    count += 1
                    break
            if db["salt_hex"] not in remaining_salts:
                break
    return count


def strategy_c_bruteforce(data, db_files, remaining_salts, key_map, salt_to_dbs, step=8):
    """Strategy C: Try every aligned 32-byte block as a key (expensive!)."""
    count = 0
    data_len = len(data)
    tried = 0

    for offset in range(0, data_len - KEY_SZ, step):
        candidate = data[offset:offset + KEY_SZ]
        # Quick filter: skip all-zero or low-entropy blocks
        if candidate[:4] == b'\x00\x00\x00\x00':
            continue
        # Check if it looks like a potential key (has high entropy)
        unique_bytes = len(set(candidate))
        if unique_bytes < 8:
            continue

        tried += 1
        found = try_key_for_remaining(candidate, db_files, remaining_salts, key_map, salt_to_dbs, "brute-force")
        if found:
            count += len(found)
        if not remaining_salts:
            break
    return count, tried


def main():
    parser = argparse.ArgumentParser(description="Enhanced WeChat key extractor - multi-strategy")
    parser.add_argument("--account", "-a", choices=["1", "2"], default="2",
                        help="Which WeChat account (default: 2)")
    parser.add_argument("--pid", "-p", type=int, default=None,
                        help="Specific WeChat PID to attach to")
    parser.add_argument("--output", "-o", default=None,
                        help="Output JSON file (default: wechat_keys_<account>.json)")
    parser.add_argument("--strategy", "-s", default="abc",
                        help="Strategies to use: a=hex, b=salt-proximity, c=brute-force (default: abc)")
    parser.add_argument("--brute-step", type=int, default=8,
                        help="Byte step for brute-force scan (default: 8, use 1 for exhaustive)")
    parser.add_argument("--max-region-mb", type=int, default=800,
                        help="Max region size in MB to scan (default: 800)")
    args = parser.parse_args()

    container = CONTAINERS[args.account]
    output_dir = os.path.dirname(os.path.abspath(__file__))
    OUTPUT_FILE = args.output or os.path.join(output_dir, f"wechat_keys_{args.account}.json")

    print("=" * 70)
    print(f"  WeChat Enhanced Key Extractor - Account {args.account} ({container})")
    print(f"  Strategies: {args.strategy.upper()}")
    print("=" * 70)

    # 1. Collect DB files
    db_dir, db_base = find_db_dir(container)
    if not db_dir:
        print(f"[-] Could not find db_storage directory under {db_base}")
        sys.exit(1)

    db_files, salt_to_dbs = collect_db_files(db_dir)
    print(f"\n[*] Found {len(db_files)} databases, {len(salt_to_dbs)} unique salts")

    # Show which DBs we need keys for
    existing_keys = {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r") as f:
                existing_keys = json.load(f)
        except Exception:
            pass

    # Pre-verify existing keys
    key_map = {}
    for db in db_files:
        if db["rel"] in existing_keys and existing_keys[db["rel"]] != "__salts__":
            key_hex = existing_keys[db["rel"]]
            if isinstance(key_hex, str) and len(key_hex) == 64:
                try:
                    enc_key = bytes.fromhex(key_hex)
                    if verify_key_for_db(enc_key, db["page1"]):
                        key_map[db["salt_hex"]] = key_hex
                except Exception:
                    pass

    remaining_salts = set(salt_to_dbs.keys()) - set(key_map.keys())
    print(f"[*] Already have {len(key_map)} valid keys, need {len(remaining_salts)} more")

    if not remaining_salts:
        print("[+] All keys already found! Nothing to do.")
        sys.exit(0)

    # Show what's missing
    for db in db_files:
        if db["salt_hex"] in remaining_salts:
            print(f"  NEED: {db['rel']} ({db['size'] / 1024 / 1024:.1f} MB) salt={db['salt_hex']}")

    # 2. Attach to WeChat
    print("\n[*] Attaching to WeChat...")
    debugger = lldb.SBDebugger.Create()
    debugger.SetAsync(False)
    target = debugger.CreateTarget("")
    error = lldb.SBError()

    if args.pid:
        process = target.AttachToProcessWithID(debugger.GetListener(), args.pid, error)
    else:
        process = target.AttachToProcessWithName(debugger.GetListener(), "WeChat", False, error)

    if not error.Success():
        print(f"[-] Error attaching: {error.GetCString()}")
        print("[!] Tips:")
        print("[!]   - Make sure WeChat is running")
        print("[!]   - Run with sudo")
        print("[!]   - SIP must be disabled (csrutil disable)")
        print("[!]   - Use --pid <PID> for specific process (pgrep -l WeChat)")
        sys.exit(1)

    pid = process.GetProcessID()
    print(f"[+] Attached to WeChat (PID: {pid})")

    # 3. Enumerate memory regions
    region_info = lldb.SBMemoryRegionInfo()
    regions = []
    addr = 0
    max_region_bytes = args.max_region_mb * 1024 * 1024

    while True:
        err = process.GetMemoryRegionInfo(addr, region_info)
        if err.Fail():
            break
        base = region_info.GetRegionBase()
        end = region_info.GetRegionEnd()
        if end <= base:
            break
        if region_info.IsReadable():
            size = end - base
            if 0 < size <= max_region_bytes:
                regions.append((base, size))
        addr = end
        if addr == 0:
            break

    total_bytes = sum(s for _, s in regions)
    print(f"[*] Found {len(regions)} readable regions ({total_bytes / 1024 / 1024:.0f} MB)")

    # 4. Scan with multiple strategies
    t_start = time.time()
    total_scanned = 0
    chunk_size = 16 * 1024 * 1024  # 16MB chunks

    # Strategy A + B run together per chunk
    print(f"\n[*] Phase 1: Strategy A (hex patterns) + Strategy B (salt proximity)...")
    for reg_idx, (base, size) in enumerate(regions):
        offset = 0
        while offset < size:
            read_size = min(chunk_size, size - offset)
            read_addr = base + offset
            data = process.ReadMemory(read_addr, read_size, error)
            offset += read_size
            total_scanned += read_size

            if not error.Success() or not data:
                continue

            if 'a' in args.strategy and remaining_salts:
                strategy_a_hex_patterns(data, db_files, remaining_salts, key_map, salt_to_dbs)

            if 'b' in args.strategy and remaining_salts:
                strategy_b_salt_proximity(data, read_addr, db_files, remaining_salts, key_map, salt_to_dbs)

        # Progress
        if (reg_idx + 1) % 100 == 0 or reg_idx == len(regions) - 1:
            progress = total_scanned / total_bytes * 100 if total_bytes else 100
            elapsed = time.time() - t_start
            print(
                f"  [{progress:.1f}%] {len(key_map)}/{len(salt_to_dbs)} keys, "
                f"{total_scanned / 1024 / 1024:.0f}/{total_bytes / 1024 / 1024:.0f} MB, "
                f"{elapsed:.1f}s"
            )

        if not remaining_salts:
            break

    # Strategy C: Brute-force (only if still missing keys)
    if 'c' in args.strategy and remaining_salts:
        print(f"\n[*] Phase 2: Strategy C (brute-force) for {len(remaining_salts)} remaining DBs...")
        print(f"    Step size: {args.brute_step} bytes")
        total_scanned = 0
        bf_total = 0
        bf_tried = 0

        for reg_idx, (base, size) in enumerate(regions):
            if not remaining_salts:
                break
            offset = 0
            while offset < size:
                if not remaining_salts:
                    break
                read_size = min(chunk_size, size - offset)
                read_addr = base + offset
                data = process.ReadMemory(read_addr, read_size, error)
                offset += read_size
                total_scanned += read_size

                if not error.Success() or not data:
                    continue

                count, tried = strategy_c_bruteforce(
                    data, db_files, remaining_salts, key_map, salt_to_dbs,
                    step=args.brute_step
                )
                bf_total += count
                bf_tried += tried

            if (reg_idx + 1) % 50 == 0 or reg_idx == len(regions) - 1:
                progress = total_scanned / total_bytes * 100 if total_bytes else 100
                elapsed = time.time() - t_start
                print(
                    f"  [{progress:.1f}%] {len(key_map)}/{len(salt_to_dbs)} keys, "
                    f"tried {bf_tried} candidates, {elapsed:.1f}s"
                )

    # 5. Cross-verify known keys
    missing_salts = set(salt_to_dbs.keys()) - set(key_map.keys())
    if missing_salts and key_map:
        print(f"\n[*] Cross-verifying {len(missing_salts)} remaining with {len(key_map)} known keys...")
        for salt_hex in list(missing_salts):
            for db in db_files:
                if db["salt_hex"] == salt_hex:
                    for known_salt, known_key_hex in key_map.items():
                        enc_key = bytes.fromhex(known_key_hex)
                        if verify_key_for_db(enc_key, db["page1"]):
                            key_map[salt_hex] = known_key_hex
                            dbs = salt_to_dbs[salt_hex]
                            print(f"  [CROSS] {', '.join(dbs)} uses same key as salt {known_salt}")
                            missing_salts.discard(salt_hex)
                    break

    # 6. Detach
    process.Detach()
    elapsed = time.time() - t_start
    print(f"\n[*] Detached. Total time: {elapsed:.1f}s")

    # 7. Save results
    print(f"\n{'=' * 70}")
    print(f"Results: {len(key_map)}/{len(salt_to_dbs)} keys found")

    result = {}
    for db in db_files:
        if db["salt_hex"] in key_map:
            result[db["rel"]] = key_map[db["salt_hex"]]
            print(f"  ✅ {db['rel']} ({db['size'] / 1024 / 1024:.1f} MB)")
        else:
            print(f"  ❌ {db['rel']} (salt={db['salt_hex']})")

    result["__salts__"] = sorted(set(key_map.keys()))

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n[*] Keys saved to {OUTPUT_FILE}")

    missing = [db["rel"] for db in db_files if db["salt_hex"] not in key_map]
    if missing:
        print(f"\n[!] Still missing keys for {len(missing)} databases:")
        for m in missing:
            print(f"    - {m}")
        print(f"\n[!] Suggestions:")
        print(f"    1. Try with --brute-step 1 (exhaustive but slow)")
        print(f"    2. Open WeChat search and type something, then re-run")
        print(f"    3. Open specific chats in WeChat to trigger lazy loading")

    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
