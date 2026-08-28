#!/usr/bin/env python3
"""Notification-area companion for Sticky Panote.

The tray backend uses GTK 3 while the main application uses GTK 4, so they
must live in separate processes. The tray sends simple Unix signals to its
parent application to show or quit it.
"""

import os
import signal
import sys
from pathlib import Path

TRAY_TYPELIB_DIR = (
    Path.home()
    / ".local/share/automove-tray-runtime/usr/lib/x86_64-linux-gnu/girepository-1.0"
)
if TRAY_TYPELIB_DIR.is_dir():
    existing_path = os.environ.get("GI_TYPELIB_PATH")
    paths = [str(TRAY_TYPELIB_DIR)]
    if existing_path:
        paths.append(existing_path)
    os.environ["GI_TYPELIB_PATH"] = os.pathsep.join(paths)

import pystray
from PIL import Image, ImageDraw


def create_tray_image():
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 6, 56, 58), radius=14, fill="#3584E4")
    draw.line((19, 22, 45, 22), fill="#FFFFFF", width=4)
    draw.line((19, 32, 45, 32), fill="#FFFFFF", width=4)
    draw.line((19, 42, 37, 42), fill="#FFFFFF", width=4)
    return image


def main():
    if len(sys.argv) != 2:
        return 2

    try:
        parent_pid = int(sys.argv[1])
    except ValueError:
        return 2

    def signal_parent(selected_signal):
        try:
            os.kill(parent_pid, selected_signal)
        except ProcessLookupError:
            icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(
            "Open Sticky Panote",
            lambda _icon, _item: signal_parent(signal.SIGUSR1),
            default=True,
        ),
        pystray.MenuItem(
            "Quit Application",
            lambda _icon, _item: signal_parent(signal.SIGTERM),
        ),
    )
    icon = pystray.Icon(
        "sticky-panote",
        create_tray_image(),
        "Sticky Panote",
        menu,
    )
    icon.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
