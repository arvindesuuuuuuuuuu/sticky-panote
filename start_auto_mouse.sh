#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/arvin/Infosoft/automove"
PID_FILE="$APP_DIR/auto_mouse_move.pid"
YDOTOOLD_PID_FILE="$APP_DIR/ydotoold.pid"
LOG_FILE="$APP_DIR/auto_mouse_move.log"
YDOTOOL_SOCKET="/tmp/automove-ydotool.sock"
SILENT=false
WORKER_ENV=""

if [[ "${1:-}" == "--silent" ]]; then
  SILENT=true
  WORKER_ENV="AUTOMOVE_SILENT=1 "
fi

notify() {
  if [[ "$SILENT" == false ]]; then
    notify-send --app-name= "" "$1"
  fi
}

if [[ -f "$PID_FILE" ]]; then
  if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    notify "Already running."
    exit 0
  fi

  rm -f "$PID_FILE"
fi

if ! command -v ydotool >/dev/null 2>&1; then
  echo "ydotool is not installed. Run: sudo apt install ydotool" > "$LOG_FILE"
  notify "Cannot start: ydotool is not installed."
  exit 1
fi

cd "$APP_DIR"

if [[ ! -S "$YDOTOOL_SOCKET" ]]; then
  rm -f "$YDOTOOL_SOCKET"
  sg input -c "setsid ydotoold --socket-path='$YDOTOOL_SOCKET' --socket-perm=0660 > '$APP_DIR/ydotoold.log' 2>&1 < /dev/null & echo \$! > '$YDOTOOLD_PID_FILE'"
  sleep 1
fi

sg input -c "setsid env ${WORKER_ENV}YDOTOOL_SOCKET='$YDOTOOL_SOCKET' python3 -u '$APP_DIR/auto_mouse_move.py' > '$LOG_FILE' 2>&1 < /dev/null & echo \$! > '$PID_FILE'"
notify "Started."
