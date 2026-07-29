import ctypes
import os
import random
import subprocess
import sys
import time
from ctypes import wintypes


MOVE_INTERVAL_SECONDS = 30
MOUSE_CHECK_INTERVAL_SECONDS = 0.5
MIN_WIGGLE_PIXELS = 1
MAX_WIGGLE_PIXELS = 3
WIGGLE_STEPS = 1
WIGGLE_STEP_DELAY_SECONDS = 0.03
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(APP_DIR, "auto_mouse_move_windows.pid")
NOTIFY_SCRIPT = os.path.join(APP_DIR, "notify_auto_mouse_windows.vbs")

VK_CONTROL = 0x11
VK_MENU = 0x12
VK_PAUSE = 0x13
VK_END = 0x23
CTRL_BREAK_EVENT = 1
CONSOLE_CTRL_HANDLER = None


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


def require_windows():
    if os.name != "nt":
        raise SystemExit("This script is for Windows. Use auto_mouse_move.py on Linux.")


def get_user32():
    user32 = ctypes.windll.user32
    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    user32.GetLastInputInfo.restype = wintypes.BOOL
    user32.GetTickCount.restype = wintypes.DWORD
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    return user32


def ignore_console_break_shortcut():
    global CONSOLE_CTRL_HANDLER

    handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def handler(control_type):
        return control_type == CTRL_BREAK_EVENT

    CONSOLE_CTRL_HANDLER = handler_type(handler)
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleCtrlHandler.argtypes = [handler_type, wintypes.BOOL]
    kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL

    if not kernel32.SetConsoleCtrlHandler(CONSOLE_CTRL_HANDLER, True):
        raise ctypes.WinError()


def write_pid_file():
    with open(PID_FILE, "w", encoding="utf-8") as pid_file:
        pid_file.write(str(os.getpid()))


def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass


def notify_status(message):
    try:
        subprocess.Popen(
            ["wscript.exe", NOTIFY_SCRIPT, message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def notify_stopped():
    notify_status("Auto Mouse Move stopped.")


def get_idle_seconds(user32):
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)

    if not user32.GetLastInputInfo(ctypes.byref(info)):
        raise ctypes.WinError()

    elapsed_ms = user32.GetTickCount() - info.dwTime
    return elapsed_ms / 1000


def is_key_down(user32, virtual_key):
    return bool(user32.GetAsyncKeyState(virtual_key) & 0x8000)


def ctrl_alt_pressed(user32):
    return (
        is_key_down(user32, VK_CONTROL)
        and is_key_down(user32, VK_MENU)
    )


def pause_shortcut_pressed(user32):
    return ctrl_alt_pressed(user32) and is_key_down(user32, VK_PAUSE)


def stop_shortcut_pressed(user32):
    return ctrl_alt_pressed(user32) and is_key_down(user32, VK_END)


def random_wiggle_delta():
    distance_x = random.randint(MIN_WIGGLE_PIXELS, MAX_WIGGLE_PIXELS)
    distance_y = random.randint(MIN_WIGGLE_PIXELS, MAX_WIGGLE_PIXELS)
    direction_x = random.choice([-1, 1])
    direction_y = random.choice([-1, 1])

    return distance_x * direction_x, distance_y * direction_y


def get_cursor_position(user32):
    point = POINT()

    if not user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError()

    return point.x, point.y


def smooth_relative_move(user32, dx, dy):
    start_x, start_y = get_cursor_position(user32)
    previous_x = 0
    previous_y = 0

    for step in range(1, WIGGLE_STEPS + 1):
        progress = step / WIGGLE_STEPS
        eased_progress = progress * progress * (3 - 2 * progress)
        target_x = round(dx * eased_progress)
        target_y = round(dy * eased_progress)

        next_x = start_x + target_x
        next_y = start_y + target_y

        if target_x != previous_x or target_y != previous_y:
            if not user32.SetCursorPos(next_x, next_y):
                raise ctypes.WinError()

        previous_x = target_x
        previous_y = target_y
        time.sleep(WIGGLE_STEP_DELAY_SECONDS)


def wiggle_mouse(user32):
    dx, dy = random_wiggle_delta()
    smooth_relative_move(user32, dx, dy)
    print(f"Moved mouse by ({dx}, {dy}).", flush=True)


def main():
    require_windows()
    ignore_console_break_shortcut()
    user32 = get_user32()
    write_pid_file()

    print("Moving mouse after 30 seconds of no keyboard or mouse activity.", flush=True)
    print("Pause/resume uses the configured shortcut.", flush=True)
    print("Stop uses the configured shortcut.", flush=True)
    print("Press Ctrl+C to stop.", flush=True)

    next_move_time = time.monotonic() + MOVE_INTERVAL_SECONDS
    paused = False
    pause_shortcut_was_pressed = False

    try:
        while True:
            if stop_shortcut_pressed(user32):
                print("\nStopped by shortcut.", flush=True)
                break

            pause_shortcut_is_pressed = pause_shortcut_pressed(user32)
            if pause_shortcut_is_pressed and not pause_shortcut_was_pressed:
                paused = not paused
                if paused:
                    print("\nPaused by shortcut.", flush=True)
                    notify_status("Auto Mouse Move paused.")
                else:
                    next_move_time = time.monotonic() + MOVE_INTERVAL_SECONDS
                    print("\nResumed by shortcut.", flush=True)
                    notify_status("Auto Mouse Move resumed.")

            pause_shortcut_was_pressed = pause_shortcut_is_pressed

            if paused:
                time.sleep(MOUSE_CHECK_INTERVAL_SECONDS)
                continue

            idle_seconds = get_idle_seconds(user32)

            if idle_seconds < MOVE_INTERVAL_SECONDS:
                next_move_time = time.monotonic() + (MOVE_INTERVAL_SECONDS - idle_seconds)

            if time.monotonic() >= next_move_time:
                wiggle_mouse(user32)
                next_move_time = time.monotonic() + MOVE_INTERVAL_SECONDS

            time.sleep(MOUSE_CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        remove_pid_file()
        notify_stopped()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Auto Mouse Move failed: {exc}", file=sys.stderr, flush=True)
        raise
