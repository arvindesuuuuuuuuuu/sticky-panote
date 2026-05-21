import random
import time

import pyautogui


MOVE_INTERVAL_SECONDS = 30
MOVE_DURATION_SECONDS = 0.5
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

    print("Moving mouse to a random position every 30 seconds.")
    print("Press Ctrl+C to stop. Move the mouse to the top-left corner to trigger failsafe.")

    try:
        while True:
            x, y = random_mouse_position()
            pyautogui.moveTo(x, y, duration=MOVE_DURATION_SECONDS)
            print(f"Moved mouse to ({x}, {y})")
            time.sleep(MOVE_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
