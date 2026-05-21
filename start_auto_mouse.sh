#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/arvin/Infosoft/automove"
PID_FILE="$APP_DIR/auto_mouse_move.pid"
LOG_FILE="$APP_DIR/auto_mouse_move.log"
LOCAL_XAUTHORITY="$APP_DIR/.Xauthority.local"
DISPLAY_NAME="${DISPLAY:-:0}"
MUTTER_XAUTHORITY="$(find /run/user/$(id -u) -maxdepth 1 -name '.mutter-Xwaylandauth.*' -print -quit)"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  exit 0
fi

if [[ -n "$MUTTER_XAUTHORITY" ]]; then
  COOKIE="$(xauth -f "$MUTTER_XAUTHORITY" list | awk 'NR==1 {print $3}')"
  if [[ -n "$COOKIE" ]]; then
    rm -f "$LOCAL_XAUTHORITY"
    xauth -f "$LOCAL_XAUTHORITY" add "$DISPLAY_NAME" MIT-MAGIC-COOKIE-1 "$COOKIE" >/dev/null 2>&1 || true
    xauth -f "$LOCAL_XAUTHORITY" add "$(hostname)/unix${DISPLAY_NAME}" MIT-MAGIC-COOKIE-1 "$COOKIE" >/dev/null 2>&1 || true
  fi
fi

cd "$APP_DIR"
setsid env DISPLAY="$DISPLAY_NAME" XAUTHORITY="$LOCAL_XAUTHORITY" \
  python3 -u "$APP_DIR/auto_mouse_move.py" > "$LOG_FILE" 2>&1 < /dev/null &

echo "$!" > "$PID_FILE"
