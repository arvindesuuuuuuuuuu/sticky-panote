#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$APP_DIR/auto_mouse_move.pid"
YDOTOOLD_PID_FILE="$APP_DIR/ydotoold.pid"
WORKER_SCRIPT="$APP_DIR/auto_mouse_move.py"
YDOTOOL_SOCKET="/tmp/automove-ydotool.sock"
STOPPED=false

worker_is_running() {
  local pid="${1:-}"

  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  grep -Fzxq -- "$WORKER_SCRIPT" "/proc/$pid/cmdline"
}

ydotoold_is_running() {
  local pid="${1:-}"

  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  grep -Fzxq -- "ydotoold" "/proc/$pid/cmdline" &&
    grep -Fzxq -- "--socket-path=$YDOTOOL_SOCKET" "/proc/$pid/cmdline"
}

if [[ -f "$PID_FILE" ]]; then
  PID="$(tr -d '[:space:]' < "$PID_FILE")"

  if worker_is_running "$PID"; then
    kill "$PID"
    STOPPED=true
  fi
  rm -f "$PID_FILE"
fi

if [[ -f "$YDOTOOLD_PID_FILE" ]]; then
  YDOTOOLD_PID="$(tr -d '[:space:]' < "$YDOTOOLD_PID_FILE")"
  if ydotoold_is_running "$YDOTOOLD_PID"; then
    kill "$YDOTOOLD_PID"
  fi
  rm -f "$YDOTOOLD_PID_FILE"
fi

rm -f "$YDOTOOL_SOCKET"

if [[ "$STOPPED" == true ]]; then
  notify-send --app-name= "" "Stopped."
else
  notify-send --app-name= "" "Not running."
fi
