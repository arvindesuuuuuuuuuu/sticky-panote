import os
import select
import struct
import time


INPUT_EVENT_DIR = "/dev/input"
INPUT_EVENT_SIZE = struct.calcsize("llHHI")
EV_KEY = 1


def open_inputs():
    fds = []

    for name in os.listdir(INPUT_EVENT_DIR):
        if not name.startswith("event"):
            continue

        path = os.path.join(INPUT_EVENT_DIR, name)

        try:
            fds.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
        except OSError:
            continue

    return fds


def main():
    fds = open_inputs()

    print("Press the key or shortcut to detect. Press Ctrl+C to stop.")

    try:
        while True:
            readable, _, _ = select.select(fds, [], [], 0.5)

            for fd in readable:
                data = os.read(fd, INPUT_EVENT_SIZE * 32)

                for offset in range(0, len(data) - INPUT_EVENT_SIZE + 1, INPUT_EVENT_SIZE):
                    _, _, event_type, code, value = struct.unpack(
                        "llHHI", data[offset : offset + INPUT_EVENT_SIZE]
                    )

                    if event_type == EV_KEY and value == 1:
                        print(f"key code: {code}", flush=True)

            time.sleep(0.01)
    finally:
        for fd in fds:
            os.close(fd)


if __name__ == "__main__":
    main()
