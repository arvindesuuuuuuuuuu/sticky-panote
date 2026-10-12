# Sticky Panote

Native GNOME sticky notes with a tray menu, AutoMove controls, and Hubstaff
tracking controls. Supports Ubuntu/GNOME, including Wayland.

## Install

From this project folder, build and install the Debian package:

```sh
bash build_deb.sh
sudo apt install /tmp/sticky-panote_1.0.4_all.deb
```

APT installs the required Python, GTK, tray, and AutoMove dependencies.
Open **Sticky Panote** from the applications menu, or run:

```sh
sticky-panote
```

Repeat the build and install commands after updating the source to upgrade the
installed app. Quit and reopen Sticky Panote to load the update.

## Autorun at login

Open the gear button for the **Work Services** popover, then enable **Autorun at Login**.
Sticky Panote will start automatically at your next desktop login. Turn the
switch off to disable startup. This setting applies to your user account and
does not require administrator access. Existing Sticky Panote startup entries
are detected automatically.

**Auto Start Hubstaff** is a separate switch: it resumes the last active project
when Sticky Panote starts. Hubstaff must already be installed and signed in at
`/home/arvin/Hubstaff/HubstaffCLI.bin.x86_64` (the current configured path).

## Notes and settings

The installed app keeps notes and settings in `~/.local/share/sticky-panote/`.
Updates do not replace these files.

If you previously ran the app from this project folder, quit it and copy your
data before the first installed launch:

```sh
mkdir -p ~/.local/share/sticky-panote
cp -an notes sticky_panote_notes.json sticky_panote_notes.txt \
  sticky_panote_config.json ~/.local/share/sticky-panote/
```

Existing destination files are preserved.

## Work services

The notification-area menu provides an AutoMove control that switches between
Start and Stop according to the worker state. On Wayland, AutoMove needs access
to input devices. If your account is not already in the `input` group, run:

```sh
sudo usermod -aG input "$USER"
```

Log out and back in after changing group membership.

While Sticky Panote is running, including hidden in the tray, Hubstaff tracking
automatically pauses daily at **12:00 PM** and resumes at **1:00 PM**, Asia/Manila
time. The schedule checks every five seconds and saves a pending resume during
lunch, including when Hubstaff is already stopped. Restarting Sticky Panote
during the break still allows it to resume afterward. Starting Hubstaff during
lunch is disabled.

## Run from source

After installing the package dependencies, run `./start_sticky_panote.sh`.
Source launches keep data in this project folder and support the same autorun
switch. Keep the folder in place while using source autorun.

## Uninstall

Turn off **Autorun at Login**, quit the app, then run:

```sh
sudo apt remove sticky-panote
```

Your notes and settings remain in `~/.local/share/sticky-panote/`.
