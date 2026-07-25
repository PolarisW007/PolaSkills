#!/bin/bash
# ============================================================
# WeChat Full Key Extraction - One-click script
# 强制加载所有数据库 + 多策略内存扫描
# ============================================================
# Usage:
#   chmod +x extract_all_keys.sh
#   sudo ./extract_all_keys.sh [--account 1|2] [--pid PID]
#
# Requirements:
#   - WeChat running (logged in)
#   - SIP disabled (csrutil disable, reboot to recovery mode)
#   - Xcode command line tools (for lldb)
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ACCOUNT="${1:-2}"
PID_ARG=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --account|-a) ACCOUNT="$2"; shift 2;;
        --pid|-p) PID_ARG="--pid $2"; shift 2;;
        --help|-h)
            echo "Usage: sudo $0 [--account 1|2] [--pid PID]"
            echo "  --account: 1=xinWeChat, 2=xinWeChat2 (default: 2)"
            echo "  --pid:     Specific WeChat PID (auto-detect if not set)"
            exit 0;;
        *) shift;;
    esac
done

echo "============================================================"
echo "  WeChat Full Key Extraction"
echo "  Account: $ACCOUNT"
echo "============================================================"

# Check if running as root (needed for lldb attach)
if [ "$EUID" -ne 0 ]; then
    echo "[!] This script needs sudo to attach to WeChat process."
    echo "    Re-run: sudo $0 --account $ACCOUNT $PID_ARG"
    exit 1
fi

# Check WeChat is running
if ! pgrep -x WeChat > /dev/null; then
    echo "[-] WeChat is not running! Please open WeChat first."
    exit 1
fi

echo "[*] WeChat is running."

SYSTEM_PYTHON="/usr/bin/python3"
if [ ! -x "$SYSTEM_PYTHON" ]; then
    echo "[-] $SYSTEM_PYTHON not found. LLDB bindings require macOS system Python."
    exit 1
fi
echo "[*] Python: $SYSTEM_PYTHON"

# Step 1: Force WeChat to load all message databases via global search
echo ""
echo "[*] Step 1: Forcing WeChat to load all databases..."
echo "    (Triggering global search via AppleScript)"

# Use AppleScript to trigger WeChat's search feature
# This forces WeChat to access all message_N.db files
osascript <<'APPLESCRIPT' 2>/dev/null || true
tell application "WeChat" to activate
delay 1

tell application "System Events"
    tell process "WeChat"
        -- Trigger global search with Cmd+F
        keystroke "f" using {command down}
        delay 0.5

        -- Type a common character to trigger search across all DBs
        keystroke "a"
        delay 3

        -- Type another search to ensure more DBs are touched
        keystroke "a" using {command down}
        keystroke "1"
        delay 2

        -- Close search
        key code 53 -- Escape
        delay 0.5
    end tell
end tell
APPLESCRIPT

echo "[+] Global search triggered. Waiting 5s for databases to load..."
sleep 5

# Step 2: Run enhanced memory scanner
echo ""
echo "[*] Step 2: Running enhanced memory scanner..."
echo ""

set +e
"$SYSTEM_PYTHON" "$SCRIPT_DIR/run_key_extract.py" \
    --account "$ACCOUNT" $PID_ARG --strategy abc --brute-step 8
EXIT_CODE=$?
set -e

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "[!] Some keys still missing. Trying exhaustive brute-force (step=1)..."
    echo ""

    "$SYSTEM_PYTHON" "$SCRIPT_DIR/run_key_extract.py" \
        --account "$ACCOUNT" $PID_ARG --strategy c --brute-step 1
fi

echo ""
echo "============================================================"
echo "  Done! Check the keys file for results."
echo "============================================================"
