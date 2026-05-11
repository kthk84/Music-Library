#!/usr/bin/env bash
#
# Launch SoundBridge v2 with shared state from v1.
#
# v2's app.py reads SOUNDBRIDGE_DATA_DIR (set below) so all paths — config.json,
# shazam_status_cache.json, cover_cache/, .soundeo_browser_profile/ — resolve
# to v1's folder. v2 writes back to the same files so cover hashes, starred
# state, etc. are shared. NEVER run v1 and v2 simultaneously.
#
# Port defaults to 5003 (v1 uses 5002) so an accidentally-running v1 doesn't
# block v2 from starting.
#
# Usage:
#   ./run_v2.sh             # start Flask in foreground
#   ./run_v2.sh desktop     # start the pywebview desktop wrapper
#
# Stop: Ctrl+C in the terminal.

set -e

V1_DIR="/Users/keith/Code/Antigravity Projects/KeithKornson BV/SoundBridge/v1"

if [ ! -d "$V1_DIR" ]; then
    echo "ERROR: v1 data dir not found at: $V1_DIR" >&2
    echo "Edit run_v2.sh to point SOUNDBRIDGE_DATA_DIR at your SoundBridge folder." >&2
    exit 1
fi

# v2 always reads/writes v1's state. To use a separate state, change this path.
export SOUNDBRIDGE_DATA_DIR="$V1_DIR"
# v1 = 5002, v2 = 5003 by default. Override at the call site if needed.
export SOUNDBRIDGE_PORT="${SOUNDBRIDGE_PORT:-5003}"

cd "$(dirname "$0")"

echo "----------------------------------------------------------------------"
echo "SoundBridge v2"
echo "  Code:    $(pwd)"
echo "  State:   $SOUNDBRIDGE_DATA_DIR"
echo "  Port:    $SOUNDBRIDGE_PORT  (v1 is on 5002 — DO NOT run both at once)"
echo "  URL:     http://127.0.0.1:$SOUNDBRIDGE_PORT"
echo "----------------------------------------------------------------------"

if [ "$1" = "desktop" ]; then
    exec python3 launch_desktop.py
else
    exec python3 app.py
fi
