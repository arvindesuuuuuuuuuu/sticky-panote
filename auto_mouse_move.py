import json
import os
import random
import select
import shutil
import struct
import subprocess
import time
from pathlib import Path


MOVE_INTERVAL_SECONDS = 30
MOUSE_CHECK_INTERVAL_SECONDS = 0.5
MIN_WIGGLE_PIXELS = 1
MAX_WIGGLE_PIXELS = 3
WIGGLE_STEPS = 1
WIGGLE_STEP_DELAY_SECONDS = 0.03
MOUSE_INPUT_DEVICE = "/dev/input/mice"
INPUT_EVENT_DIR = "/dev/input"
INPUT_EVENT_SIZE = struct.calcsize("llHHI")
EV_KEY = 1
KEY_LEFTCTRL = 29
KEY_LEFTALT = 56
KEY_PAUSE_CODES = {
    99,  # SysRq, used by some keyboards for the shared Print/Pause area.
    119,  # Pause.
    411,  # Break.
}
KEY_END = 107
KEY_RIGHTCTRL = 97
KEY_RIGHTALT = 100
YDOTOOL_SOCKET = os.environ.get("YDOTOOL_SOCKET", "/tmp/automove-ydotool.sock")
CONFIG_FILE = Path(__file__).resolve().parent / "auto_mouse_move_config.json"


DEFAULT_PAUSE_KEY_CODES = sorted(KEY_PAUSE_CODES)
DEFAULT_STOP_KEY_CODE = KEY_END


def load_shortcut_config():
    config = {
        "pause_key_codes": DEFAULT_PAUSE_KEY_CODES,
        "stop_key_code": DEFAULT_STOP_KEY_CODE,
    }

    try:
        saved_config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return config

    pause_key_codes = saved_config.get("pause_key_codes")
    stop_key_code = saved_config.get("stop_key_code")

    if isinstance(pause_key_codes, list):
        valid_pause_codes = []
        for key_code in pause_key_codes:
            try:
                valid_pause_codes.append(int(key_code))
            except (TypeError, ValueError):
                continue

        if valid_pause_codes:
            config["pause_key_codes"] = sorted(set(valid_pause_codes))

    try:
        config["stop_key_code"] = int(stop_key_code)
    except (TypeError, ValueError):
        pass

    return config


def shortcut_label(key_codes):
    if isinstance(key_codes, int):
        return f"configured shortcut key {key_codes}"

    return "configured shortcut keys " + ",".join(str(code) for code in sorted(key_codes))


def send_notification(message):
    if os.environ.get("AUTOMOVE_SILENT") == "1":
        return

    if shutil.which("notify-send") is None:
        return

    subprocess.run(
        ["notify-send", "--app-name=", "", message],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def open_mouse_input():
    return os.open(MOUSE_INPUT_DEVICE, os.O_RDONLY | os.O_NONBLOCK)


def mouse_input_changed(mouse_fd):
    readable, _, _ = select.select([mouse_fd], [], [], 0)
    if not readable:
        return False

    try:
        os.read(mouse_fd, 4096)
    except BlockingIOError:
        return False

    return True


def open_keyboard_inputs():
    keyboard_fds = []

    for name in os.listdir(INPUT_EVENT_DIR):
        if not name.startswith("event"):
            continue

        path = os.path.join(INPUT_EVENT_DIR, name)

        try:
            keyboard_fds.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
        except PermissionError:
            continue
        except OSError:
            continue

    return keyboard_fds


def check_keyboard_activity(keyboard_fds, pressed_keys, pause_key_codes, stop_key_code):
    if not keyboard_fds:
        return False, False, False

    keyboard_changed = False
    readable, _, _ = select.select(keyboard_fds, [], [], 0)

    for keyboard_fd in readable:
        try:
            data = os.read(keyboard_fd, INPUT_EVENT_SIZE * 32)
        except BlockingIOError:
            continue
        except OSError:
            continue

        for offset in range(0, len(data) - INPUT_EVENT_SIZE + 1, INPUT_EVENT_SIZE):
            _, _, event_type, code, value = struct.unpack(
                "llHHI", data[offset : offset + INPUT_EVENT_SIZE]
            )

            if event_type != EV_KEY:
                continue

            keyboard_changed = True

            if value:
                pressed_keys.add(code)
            else:
                pressed_keys.discard(code)

            ctrl_pressed = KEY_LEFTCTRL in pressed_keys or KEY_RIGHTCTRL in pressed_keys
            alt_pressed = KEY_LEFTALT in pressed_keys or KEY_RIGHTALT in pressed_keys

            if value == 1 and ctrl_pressed and alt_pressed:
                if code in pause_key_codes:
                    return keyboard_changed, True, False

                if code == stop_key_code:
                    return keyboard_changed, False, True

    return keyboard_changed, False, False


def random_wiggle_delta():
    distance_x = random.randint(MIN_WIGGLE_PIXELS, MAX_WIGGLE_PIXELS)
    distance_y = random.randint(MIN_WIGGLE_PIXELS, MAX_WIGGLE_PIXELS)
    direction_x = random.choice([-1, 1])
    direction_y = random.choice([-1, 1])

    return distance_x * direction_x, distance_y * direction_y


def ydotool_mousemove(dx, dy):
    subprocess.run(
        ["ydotool", "mousemove", "--", str(dx), str(dy)],
        check=True,
        env={**os.environ, "YDOTOOL_SOCKET": YDOTOOL_SOCKET},
    )


def smooth_relative_move(dx, dy):
    previous_x = 0
    previous_y = 0

    for step in range(1, WIGGLE_STEPS + 1):
        progress = step / WIGGLE_STEPS
        eased_progress = progress * progress * (3 - 2 * progress)
        target_x = round(dx * eased_progress)
        target_y = round(dy * eased_progress)

        ydotool_mousemove(target_x - previous_x, target_y - previous_y)
        previous_x = target_x
        previous_y = target_y
        time.sleep(WIGGLE_STEP_DELAY_SECONDS)


def wiggle_mouse():
    dx, dy = random_wiggle_delta()
    smooth_relative_move(dx, dy)
    print(f"Moved mouse by ({dx}, {dy}).", flush=True)


def main():
    if shutil.which("ydotool") is None:
        raise SystemExit("ydotool is not installed. Run: sudo apt install ydotool")

    shortcut_config = load_shortcut_config()
    pause_key_codes = set(shortcut_config["pause_key_codes"])
    stop_key_code = shortcut_config["stop_key_code"]

    print("Moving mouse after 30 seconds of no keyboard or mouse activity.", flush=True)
    print(f"Pause/resume uses {shortcut_label(pause_key_codes)}.", flush=True)
    print(f"Stop uses {shortcut_label(stop_key_code)}.", flush=True)
    print("Press Ctrl+C to stop.", flush=True)

    mouse_fd = open_mouse_input()
    keyboard_fds = open_keyboard_inputs()
    pressed_keys = set()
    next_move_time = time.monotonic() + MOVE_INTERVAL_SECONDS
    paused = False

    try:
        while True:
            keyboard_changed, pause_requested, stop_requested = check_keyboard_activity(
                keyboard_fds,
                pressed_keys,
                pause_key_codes,
                stop_key_code,
            )

            if stop_requested:
                print("\nStopped by shortcut.", flush=True)
                send_notification("Stopped by shortcut.")
                break

            if pause_requested:
                paused = not paused
                if paused:
                    print("\nPaused by shortcut.", flush=True)
                    send_notification("Paused.")
                else:
                    next_move_time = time.monotonic() + MOVE_INTERVAL_SECONDS
                    print("\nResumed by shortcut.", flush=True)
                    send_notification("Resumed.")

            if paused:
                time.sleep(MOUSE_CHECK_INTERVAL_SECONDS)
                continue

            if keyboard_changed or mouse_input_changed(mouse_fd):
                next_move_time = time.monotonic() + MOVE_INTERVAL_SECONDS

            if time.monotonic() >= next_move_time:
                wiggle_mouse()
                next_move_time = time.monotonic() + MOVE_INTERVAL_SECONDS

            time.sleep(MOUSE_CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        os.close(mouse_fd)
        for keyboard_fd in keyboard_fds:
            os.close(keyboard_fd)


if __name__ == "__main__":
    main()
