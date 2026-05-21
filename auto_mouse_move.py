import random
import time

import pyautogui


MOVE_INTERVAL_SECONDS = 30
MOVE_DURATION_SECONDS = 0.5
MOUSE_CHECK_INTERVAL_SECONDS = 0.5
SCREEN_EDGE_PADDING = 20


def random_mouse_position():
    width, height = pyautogui.size()

    min_x = min(SCREEN_EDGE_PADDING, width - 1)
    min_y = min(SCREEN_EDGE_PADDING, height - 1)
    max_x = max(min_x, width - SCREEN_EDGE_PADDING - 1)
    max_y = max(min_y, height - SCREEN_EDGE_PADDING - 1)

    return random.randint(min_x, max_x), random.randint(min_y, max_y)


def main():
    pyautogui.FAILSAFE = True

    print("Moving mouse after 30 seconds of no user mouse movement.")
    print("Press Ctrl+C to stop. Move the mouse to the top-left corner to trigger failsafe.")

    last_position = pyautogui.position()
    next_move_time = time.monotonic() + MOVE_INTERVAL_SECONDS

    try:
        while True:
            current_position = pyautogui.position()

            if current_position != last_position:
                last_position = current_position
                next_move_time = time.monotonic() + MOVE_INTERVAL_SECONDS

            if time.monotonic() >= next_move_time:
                x, y = random_mouse_position()
                pyautogui.moveTo(x, y, duration=MOVE_DURATION_SECONDS)
                last_position = pyautogui.position()
                next_move_time = time.monotonic() + MOVE_INTERVAL_SECONDS
                print(f"Moved mouse to ({x}, {y})")

            time.sleep(MOUSE_CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
