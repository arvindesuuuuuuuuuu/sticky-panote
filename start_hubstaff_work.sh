#!/usr/bin/env bash
set -u

HUBSTAFF_CLI="/home/arvin/Hubstaff/HubstaffCLI.bin.x86_64"
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$APP_DIR/sticky_panote_config.json"

# Launchers started from Snap applications can inherit a private data path.
# Hubstaff must use the normal user data directory to find its IPC socket.
unset XDG_DATA_HOME SNAP SNAP_USER_DATA SNAP_USER_COMMON

if [[ "${1:-}" == "--if-enabled" ]]; then
    if ! python3 - "$CONFIG_FILE" <<'PY'
import json
import sys
from pathlib import Path

try:
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError, OSError):
    config = {}

raise SystemExit(0 if config.get("hubstaff_auto_start", True) is not False else 1)
PY
    then
        echo "$(date --iso-8601=seconds) Hubstaff auto start is disabled"
        exit 0
    fi
fi

if [[ ! -x "$HUBSTAFF_CLI" ]]; then
    echo "$(date --iso-8601=seconds) Hubstaff CLI is not installed at $HUBSTAFF_CLI"
    exit 0
fi

timer_is_running() {
    local status

    status=$("$HUBSTAFF_CLI" status 2>/dev/null) || return 1
    [[ "$status" == *'"tracking":true'* ]]
}

if timer_is_running; then
    echo "$(date --iso-8601=seconds) Hubstaff timer is already running"
    exit 0
fi

# Start Hubstaff when needed, then allow its IPC service time to become ready.
"$HUBSTAFF_CLI" launch >/dev/null 2>&1 || true

for _attempt in {1..15}; do
    if "$HUBSTAFF_CLI" send wait-login 2>/dev/null | grep -q '"ready":true'; then
        break
    fi
    sleep 2
done

if "$HUBSTAFF_CLI" resume; then
    echo "$(date --iso-8601=seconds) Hubstaff timer resumed"
    exit 0
fi

# A concurrent Hubstaff startup can resume the timer before this command does.
if timer_is_running; then
    echo "$(date --iso-8601=seconds) Hubstaff timer is running"
    exit 0
fi

echo "$(date --iso-8601=seconds) Hubstaff timer could not be resumed"
exit 0
