#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT="${1:-/tmp/sticky-panote_1.0.4_all.deb}"
STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGING_DIR"' EXIT
mkdir -p "$STAGING_DIR/DEBIAN" "$STAGING_DIR/usr/lib/sticky-panote" \
    "$STAGING_DIR/usr/bin" "$STAGING_DIR/usr/share/applications"
cat > "$STAGING_DIR/DEBIAN/control" <<'EOF'
Package: sticky-panote
Version: 1.0.4
Section: utils
Priority: optional
Architecture: all
Maintainer: Sticky Panote <arvin@localhost>
Depends: python3, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, python3-pystray, python3-pil, gir1.2-ayatanaappindicator3-0.1, ydotool, libnotify-bin
Description: Native GNOME sticky notes with AutoMove and Hubstaff controls
EOF
for file in sticky_panote.py sticky_panote_tray.py auto_mouse_move.py \
    start_sticky_panote.sh start_auto_mouse.sh stop_auto_mouse.sh start_hubstaff_work.sh; do
    install -m755 "$APP_DIR/$file" "$STAGING_DIR/usr/lib/sticky-panote/$file"
done
cat > "$STAGING_DIR/usr/bin/sticky-panote" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export STICKY_PANOTE_DATA_DIR="${STICKY_PANOTE_DATA_DIR:-$HOME/.local/share/sticky-panote}"
mkdir -p "$STICKY_PANOTE_DATA_DIR"
export PATH="/usr/bin:/bin:$PATH"
exec /usr/lib/sticky-panote/start_sticky_panote.sh "$@"
EOF
chmod 755 "$STAGING_DIR/usr/bin/sticky-panote"
sed 's|^Exec=.*|Exec=/usr/bin/sticky-panote|' "$APP_DIR/com.infosoft.StickyPanote.desktop" \
    > "$STAGING_DIR/usr/share/applications/com.infosoft.StickyPanote.desktop"
dpkg-deb --root-owner-group --build "$STAGING_DIR" "$OUTPUT"
