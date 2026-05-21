#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/arvin/Infosoft/automove"
PID_FILE="$APP_DIR/auto_mouse_move.pid"

if [[ ! -f "$PID_FILE" ]]; then
  exit 0
fi

PID="$(cat "$PID_FILE")"

if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  rm -f "$PID_FILE"
else
  rm -f "$PID_FILE"
fi
