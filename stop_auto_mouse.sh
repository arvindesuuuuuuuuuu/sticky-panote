#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/arvin/Infosoft/automove"
PID_FILE="$APP_DIR/auto_mouse_move.pid"
YDOTOOLD_PID_FILE="$APP_DIR/ydotoold.pid"
YDOTOOL_SOCKET="/tmp/automove-ydotool.sock"
STOPPED=false

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"

  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    STOPPED=true
  fi
  rm -f "$PID_FILE"
fi

if [[ -f "$YDOTOOLD_PID_FILE" ]]; then
  YDOTOOLD_PID="$(cat "$YDOTOOLD_PID_FILE")"
  if kill -0 "$YDOTOOLD_PID" 2>/dev/null; then
    kill "$YDOTOOLD_PID"
  fi
  rm -f "$YDOTOOLD_PID_FILE"
fi

rm -f "$YDOTOOL_SOCKET"

if [[ "$STOPPED" == true ]]; then
  notify-send "Auto Mouse Move" "Stopped."
else
  notify-send "Auto Mouse Move" "Not running."
fi
