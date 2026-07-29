#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/arvin/Infosoft/automove"

cd "$APP_DIR"
unset GTK_PATH GTK_EXE_PREFIX GTK_DATA_PREFIX GDK_PIXBUF_MODULE_FILE GIO_MODULE_DIR SNAP
exec python3 "$APP_DIR/auto_mouse_move_gui.py"
