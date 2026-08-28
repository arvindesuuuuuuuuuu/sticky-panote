#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

cd "$APP_DIR"
unset GTK_PATH GTK_EXE_PREFIX GTK_DATA_PREFIX GDK_PIXBUF_MODULE_FILE GIO_MODULE_DIR SNAP

# Resume the last active Hubstaff project without delaying Sticky Panote startup.
if [[ -x "$APP_DIR/start_hubstaff_work.sh" ]]; then
    "$APP_DIR/start_hubstaff_work.sh" --if-enabled >> /tmp/hubstaff-auto-start.log 2>&1 &
fi

exec python3 "$APP_DIR/sticky_panote.py"
