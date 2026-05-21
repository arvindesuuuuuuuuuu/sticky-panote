import os
import random
import select
import shutil
import struct
import subprocess
import time


MOVE_INTERVAL_SECONDS = 30
MOUSE_CHECK_INTERVAL_SECONDS = 0.5
MIN_WIGGLE_PIXELS = 25
MAX_WIGGLE_PIXELS = 90
WIGGLE_STEPS = 8
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
KEY_RIGHTCTRL = 97
KEY_RIGHTALT = 100
YDOTOOL_SOCKET = os.environ.get("YDOTOOL_SOCKET", "/tmp/automove-ydotool.sock")


def send_notification(message):
    if shutil.which("notify-send") is None:
        return

    subprocess.run(
        ["notify-send", "Auto Mouse Move", message],
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


def stop_shortcut_pressed(keyboard_fds, pressed_keys):
    if not keyboard_fds:
        return False

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

            if value:
                pressed_keys.add(code)
            else:
                pressed_keys.discard(code)

            ctrl_pressed = KEY_LEFTCTRL in pressed_keys or KEY_RIGHTCTRL in pressed_keys
            alt_pressed = KEY_LEFTALT in pressed_keys or KEY_RIGHTALT in pressed_keys

            if code in KEY_PAUSE_CODES and value == 1 and ctrl_pressed and alt_pressed:
                return True

    return False


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

    print("Moving mouse after 30 seconds of no user mouse movement.", flush=True)
    print("Press Ctrl+C or Ctrl+Alt+Pause/Break to stop.", flush=True)

    mouse_fd = open_mouse_input()
    keyboard_fds = open_keyboard_inputs()
    pressed_keys = set()
    next_move_time = time.monotonic() + MOVE_INTERVAL_SECONDS

    try:
        while True:
            if stop_shortcut_pressed(keyboard_fds, pressed_keys):
                print("\nStopped by Ctrl+Alt+Pause/Break.", flush=True)
                send_notification("Stopped by shortcut.")
                break

            if mouse_input_changed(mouse_fd):
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
