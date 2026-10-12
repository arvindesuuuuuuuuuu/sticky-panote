#!/usr/bin/env python3
"""Sticky Panote — native GNOME notes with AutoMove controls."""

import configparser
import json
import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GLibUnix", "2.0")
from gi.repository import Adw, Gdk, Gio, GLib, GLibUnix, GObject, Gtk

APP_ID = "com.infosoft.StickyPanote"
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("STICKY_PANOTE_DATA_DIR", APP_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)
PID_FILE = DATA_DIR / "auto_mouse_move.pid"
LOG_FILE = DATA_DIR / "auto_mouse_move.log"
START_SCRIPT = APP_DIR / "start_auto_mouse.sh"
STOP_SCRIPT = APP_DIR / "stop_auto_mouse.sh"
TRAY_SCRIPT = APP_DIR / "sticky_panote_tray.py"
HUBSTAFF_SCRIPT = APP_DIR / "start_hubstaff_work.sh"
HUBSTAFF_CLI = Path("/home/arvin/Hubstaff/HubstaffCLI.bin.x86_64")
CONFIG_FILE = DATA_DIR / "sticky_panote_config.json"
NOTES_FILE = DATA_DIR / "sticky_panote_notes.txt"
NOTES_DATA_FILE = DATA_DIR / "sticky_panote_notes.json"
NOTES_DIR = DATA_DIR / "notes"
YDOTOOL_SOCKET = "/tmp/automove-ydotool.sock"

KEY_LEFTCTRL = 29
KEY_LEFTALT = 56
DEFAULT_PAUSE_KEY_CODES = [99, 119, 411]
DEFAULT_STOP_KEY_CODE = 107
HUBSTAFF_TIMEZONE = ZoneInfo("Asia/Manila")


def grab_focus_once(widget):
    widget.grab_focus()
    return GLib.SOURCE_REMOVE


def autorun_file():
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_dir / "autostart" / f"{APP_ID}.desktop"


def autorun_is_enabled():
    entry = configparser.ConfigParser(interpolation=None)
    try:
        entry.read_string(autorun_file().read_text(encoding="utf-8"))
        return (
            entry.get("Desktop Entry", "Type") == "Application"
            and bool(entry.get("Desktop Entry", "Exec"))
            and not entry.getboolean("Desktop Entry", "Hidden", fallback=False)
            and entry.getboolean("Desktop Entry", "X-GNOME-Autostart-enabled", fallback=True)
        )
    except (OSError, configparser.Error, ValueError):
        return False


def set_autorun(enabled):
    path = autorun_file()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    launcher = (
        Path("/usr/bin/sticky-panote")
        if APP_DIR == Path("/usr/lib/sticky-panote")
        else APP_DIR / "start_sticky_panote.sh"
    )
    # Desktop Exec uses double quotes and escapes rather than shell quoting.
    command = str(launcher).replace("%", "%%")
    for character in ("\\", '"', "`", "$"):
        command = command.replace(character, "\\" + character)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        "[Desktop Entry]\nType=Application\nName=Sticky Panote\n"
        'Comment=Start Sticky Panote after login\nExec="' + command + '"\n'
        "Terminal=false\nIcon=accessories-text-editor\n"
        "StartupWMClass=com.infosoft.StickyPanote\n"
        "X-GNOME-Autostart-enabled=true\n",
    )


def hubstaff_lunch_break(now=None):
    now = now if now is not None else datetime.now(HUBSTAFF_TIMEZONE)
    return now.hour == 12


def load_app_config():
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return config if isinstance(config, dict) else {}


def atomic_write_text(path, content):
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary_path.write_text(content, encoding="utf-8")
        temporary_path.replace(path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def save_app_config(updates):
    config = load_app_config()
    config.update(updates)
    atomic_write_text(CONFIG_FILE, json.dumps(config, indent=2) + "\n")


def load_shortcut_config():
    config = {
        "pause_key_codes": DEFAULT_PAUSE_KEY_CODES,
        "stop_key_code": DEFAULT_STOP_KEY_CODE,
    }
    saved_config = load_app_config()
    pause_key_codes = saved_config.get("pause_key_codes")
    if isinstance(pause_key_codes, list):
        valid_codes = []
        for key_code in pause_key_codes:
            try:
                valid_codes.append(int(key_code))
            except (TypeError, ValueError):
                continue
        if valid_codes:
            config["pause_key_codes"] = sorted(set(valid_codes))
    try:
        config["stop_key_code"] = int(saved_config.get("stop_key_code"))
    except (TypeError, ValueError):
        pass
    return config


def worker_is_running(pid):
    try:
        os.kill(pid, 0)
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except (OSError, PermissionError):
        return False
    return os.fsencode(str(APP_DIR / "auto_mouse_move.py")) in arguments


def read_pid():
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def app_is_running():
    pid = read_pid()
    return pid is not None and worker_is_running(pid)


def read_log_tail(max_lines=80):
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, OSError):
        return ""
    return "\n".join(lines[-max_lines:])


def last_pause_state(log_text):
    paused_at = log_text.rfind("Paused by shortcut.")
    resumed_at = log_text.rfind("Resumed by shortcut.")
    return paused_at != -1 and paused_at > resumed_at


def clean_hubstaff_environment():
    environment = os.environ.copy()
    for variable in (
        "XDG_DATA_HOME",
        "SNAP",
        "SNAP_USER_DATA",
        "SNAP_USER_COMMON",
    ):
        environment.pop(variable, None)
    return environment


def read_hubstaff_status():
    if not HUBSTAFF_CLI.is_file():
        return "Hubstaff is not installed", False
    try:
        result = subprocess.run(
            [str(HUBSTAFF_CLI), "status"],
            check=False,
            env=clean_hubstaff_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        status = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        status = {}
    if not isinstance(status, dict):
        status = {}
    project = status.get("active_project")
    project_name = project.get("name") if isinstance(project, dict) else None
    if status.get("tracking") is True:
        label = "Tracking"
        if project_name:
            label += f" • {project_name}"
        return label, True
    if project_name:
        return f"Stopped • {project_name}", False
    return "Hubstaff is not ready", False


def valid_note_filename(filename):
    return (
        isinstance(filename, str)
        and Path(filename).name == filename
        and filename.lower().endswith(".txt")
    )


class StickyPanoteWindow(Adw.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application)
        self.set_title("Sticky Panote")
        self.set_default_size(1040, 720)
        self.add_css_class("sticky-panote")
        self.set_size_request(600, 440)

        self.notes = []
        self.active_note_index = 0
        self.rendering_note_list = False
        self.loading_note = False
        self.save_source = None
        self.status_source = None
        self.hubstaff_status_source = None
        self.hubstaff_lunch_resume_date = load_app_config().get(
            "hubstaff_lunch_resume_date"
        )
        self.editor_font_size = 11
        self.background_mode = load_app_config().get("background_mode", "light")
        if self.background_mode not in {"light", "dark"}:
            self.background_mode = "light"

        self._build_interface()
        self._install_shortcuts()
        self.load_notes()
        self.render_note_list()
        self.apply_background_mode()
        self.refresh_status()
        self.refresh_hubstaff_status()
        self.save_note()

        self.connect("close-request", self.on_close_request)
        self.connect("realize", self.on_window_realized)

    def _build_interface(self):
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_min_sidebar_width(240)
        self.split_view.set_max_sidebar_width(280)
        self.split_view.set_sidebar_width_fraction(0.25)
        self.set_content(self.split_view)

        self.sidebar = self._build_sidebar()
        self.content_view = self._build_content()
        self.split_view.set_sidebar(self.sidebar)
        self.split_view.set_content(self.content_view)
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 800sp"))
        breakpoint.add_setter(self.split_view, "collapsed", True)
        self.add_breakpoint(breakpoint)

    def _build_sidebar(self):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Notes"))

        toolbar_view.add_top_bar(header)

        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.add_css_class("sidebar")

        self.note_filter = Gtk.SearchEntry(placeholder_text="Search notes")
        self.note_filter.set_margin_top(12)
        self.note_filter.set_margin_bottom(12)
        self.note_filter.set_margin_start(12)
        self.note_filter.set_margin_end(12)
        self.note_filter.connect("search-changed", lambda _entry: self.render_note_list())
        sidebar_box.append(self.note_filter)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)

        self.note_list = Gtk.ListBox()
        self.note_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.note_list.set_activate_on_single_click(True)
        self.note_list.add_css_class("navigation-sidebar")
        self.note_list.add_css_class("notes-list")
        self.note_list.connect("row-selected", self.on_note_row_selected)
        scroller.set_child(self.note_list)
        sidebar_box.append(scroller)

        toolbar_view.set_content(sidebar_box)
        return toolbar_view

    def _build_content(self):
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        sidebar_button = Gtk.ToggleButton(
            icon_name="sidebar-show-symbolic",
            tooltip_text="Show Notes",
        )
        sidebar_button.set_active(True)
        self.split_view.bind_property(
            "show-sidebar",
            sidebar_button,
            "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        header.pack_start(sidebar_button)

        self.window_title = Adw.WindowTitle(title="Sticky Panote")
        header.set_title_widget(self.window_title)

        self.search_button = Gtk.ToggleButton(
            icon_name="system-search-symbolic",
            tooltip_text="Find in Note (Ctrl+F)",
        )
        self.search_button.add_css_class("flat")
        self.search_button.connect("toggled", self.on_search_toggled)
        header.pack_end(self.search_button)

        new_button = Gtk.Button(icon_name="tab-new-symbolic", tooltip_text="New Note")
        new_button.add_css_class("flat")
        new_button.connect("clicked", lambda _button: self.new_note())
        header.pack_end(new_button)

        self.controls_button = Gtk.MenuButton(
            icon_name="emblem-system-symbolic",
            tooltip_text="Work Services and Startup",
        )
        self.controls_button.add_css_class("flat")
        self.controls_popover = Gtk.Popover()
        self.controls_popover.set_child(self._build_controls_panel())
        self.controls_button.set_popover(self.controls_popover)
        header.pack_end(self.controls_button)
        toolbar_view.add_top_bar(header)

        toolbar_view.set_content(self._build_editor())
        return toolbar_view

    def _build_controls_panel(self):
        clamp = Adw.Clamp(maximum_size=440)
        clamp.set_size_request(440, -1)
        clamp.set_margin_top(12)
        clamp.set_margin_bottom(12)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)

        group = Adw.PreferencesGroup(
            title="Work Services",
        )
        row = Adw.ActionRow(title="AutoMove", subtitle="Ready")
        self.activity_row = row

        self.start_pause_button = Gtk.Button(label="Start")
        self.start_pause_button.add_css_class("suggested-action")
        self.start_pause_button.set_valign(Gtk.Align.CENTER)
        self.start_pause_button.connect("clicked", lambda _button: self.start_or_pause())
        row.add_suffix(self.start_pause_button)

        self.stop_button = Gtk.Button(label="Stop")
        self.stop_button.add_css_class("flat")
        self.stop_button.set_valign(Gtk.Align.CENTER)
        self.stop_button.connect("clicked", lambda _button: self.stop_worker())
        row.add_suffix(self.stop_button)
        group.add(row)

        self.hubstaff_row = Adw.ActionRow(
            title="Hubstaff",
            subtitle="Checking status…",
        )
        self.hubstaff_start_button = Gtk.Button(label="Start Work")
        self.hubstaff_start_button.add_css_class("flat")
        self.hubstaff_start_button.set_valign(Gtk.Align.CENTER)
        self.hubstaff_start_button.connect(
            "clicked", lambda _button: self.start_hubstaff_work()
        )
        self.hubstaff_row.add_suffix(self.hubstaff_start_button)
        group.add(self.hubstaff_row)

        auto_start_row = Adw.ActionRow(
            title="Auto Start Hubstaff",
            subtitle="Resume your project when the app opens.",
        )
        self.hubstaff_auto_start_switch = Gtk.Switch()
        self.hubstaff_auto_start_switch.set_valign(Gtk.Align.CENTER)
        self.hubstaff_auto_start_switch.set_active(
            load_app_config().get("hubstaff_auto_start", True) is not False
        )
        self.hubstaff_auto_start_switch.connect(
            "notify::active", self.on_hubstaff_auto_start_changed
        )
        auto_start_row.add_suffix(self.hubstaff_auto_start_switch)
        auto_start_row.set_activatable_widget(self.hubstaff_auto_start_switch)
        group.add(auto_start_row)

        autorun_row = Adw.ActionRow(
            title="Autorun at Login",
            subtitle="Open the app when you sign in.",
        )
        self.autorun_switch = Gtk.Switch()
        self.autorun_switch.set_valign(Gtk.Align.CENTER)
        self.autorun_switch.set_active(autorun_is_enabled())
        self.autorun_handler = self.autorun_switch.connect(
            "notify::active", self.on_autorun_changed
        )
        autorun_row.add_suffix(self.autorun_switch)
        autorun_row.set_activatable_widget(self.autorun_switch)
        group.add(autorun_row)

        clamp.set_child(group)
        return clamp

    def _build_editor(self):
        editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        editor_box.add_css_class("note-surface")
        editor_box.set_vexpand(True)

        action_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        action_bar.add_css_class("note-heading")
        action_bar.set_margin_top(22)
        action_bar.set_margin_bottom(10)
        action_bar.set_margin_start(32)
        action_bar.set_margin_end(24)

        self.title_entry = Gtk.Entry(placeholder_text="Untitled note")
        self.title_entry.set_hexpand(True)
        self.title_entry.add_css_class("note-title")
        self.title_entry.connect("activate", lambda _entry: self.rename_note())
        self.title_entry.connect("notify::has-focus", self.on_title_focus_changed)
        action_bar.append(self.title_entry)

        self.pin_button = Gtk.ToggleButton(
            icon_name="non-starred-symbolic", tooltip_text="Pin Note",
        )
        self.pin_button.add_css_class("flat")
        self.pin_button.connect("toggled", self.on_pin_toggled)
        action_bar.append(self.pin_button)

        checklist_button = Gtk.Button(
            icon_name="checkbox-checked-symbolic", tooltip_text="Insert Checklist",
        )
        checklist_button.add_css_class("flat")
        checklist_button.connect("clicked", lambda _button: self.insert_checklist())
        action_bar.append(checklist_button)

        more_button = Gtk.MenuButton(
            icon_name="view-more-symbolic", tooltip_text="Note Options",
        )
        more_button.add_css_class("flat")
        options = Gtk.Popover()
        option_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        option_box.set_margin_top(6)
        option_box.set_margin_bottom(6)
        option_box.set_margin_start(6)
        option_box.set_margin_end(6)
        for label, icon, callback in (
            ("Insert timestamp", "document-open-recent-symbolic", self.insert_timestamp),
            ("Duplicate note", "edit-copy-symbolic", self.duplicate_note),
        ):
            button = self._menu_button(label, icon, callback, options)
            option_box.append(button)
        option_box.append(Gtk.Separator())
        self.theme_button = self._menu_button(
            "Switch appearance", "weather-clear-night-symbolic", self.toggle_background, options,
        )
        option_box.append(self.theme_button)
        option_box.append(Gtk.Separator())
        delete_button = self._menu_button(
            "Delete note", "user-trash-symbolic", self.close_active_note, options,
        )
        delete_button.add_css_class("error")
        option_box.append(delete_button)
        options.set_child(option_box)
        more_button.set_popover(options)
        action_bar.append(more_button)
        editor_box.append(action_bar)

        self.search_revealer = Gtk.Revealer()
        self.search_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_box.set_halign(Gtk.Align.CENTER)
        search_box.set_margin_bottom(8)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Find in note")
        self.search_entry.set_size_request(300, -1)
        self.search_entry.connect("search-changed", lambda _entry: self.find_in_note())
        self.search_entry.connect("stop-search", lambda _entry: self.clear_search())
        self.search_count = Gtk.Label()
        self.search_count.add_css_class("dim-label")
        search_box.append(self.search_entry)
        search_box.append(self.search_count)
        close_search_button = Gtk.Button(
            icon_name="window-close-symbolic", tooltip_text="Close Search (Esc)",
        )
        close_search_button.add_css_class("flat")
        close_search_button.connect("clicked", lambda _button: self.clear_search())
        search_box.append(close_search_button)
        self.search_revealer.set_child(search_box)
        editor_box.append(self.search_revealer)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)

        self.note_text = Gtk.TextView()
        self.note_text.add_css_class("note-editor")
        self.note_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.note_text.set_left_margin(36)
        self.note_text.set_right_margin(36)
        self.note_text.set_top_margin(12)
        self.note_text.set_bottom_margin(32)
        self.note_text.set_monospace(False)
        self.note_text.get_buffer().connect("changed", self.on_note_changed)

        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_controller.connect("scroll", self.on_editor_scroll)
        self.note_text.add_controller(scroll_controller)
        scroller.set_child(self.note_text)
        editor_box.append(scroller)

        status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        status_bar.add_css_class("statusbar")
        status_bar.set_margin_top(10)
        status_bar.set_margin_bottom(10)
        status_bar.set_margin_start(36)
        status_bar.set_margin_end(28)
        self.save_status = Gtk.Label(label="Saved", xalign=0)
        self.save_status.add_css_class("dim-label")
        self.save_status.set_hexpand(True)
        self.zoom_status = Gtk.Label(label="100%")
        self.zoom_status.add_css_class("dim-label")
        self.note_count = Gtk.Label(label="0 words")
        self.note_count.add_css_class("dim-label")
        status_bar.append(self.save_status)
        status_bar.append(self.zoom_status)
        status_bar.append(self.note_count)
        editor_box.append(status_bar)

        self.search_match_tag = self.note_text.get_buffer().create_tag(
            "search-match",
            background="#f9f06b",
        )
        self.search_current_tag = self.note_text.get_buffer().create_tag(
            "search-current",
            background="#3584e4",
            foreground="#ffffff",
        )
        return editor_box

    def _menu_button(self, label, icon, callback, popover):
        button = Gtk.Button()
        button.add_css_class("flat")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        image = Gtk.Image.new_from_icon_name(icon)
        text = Gtk.Label(label=label, xalign=0)
        text.set_hexpand(True)
        box.append(image)
        box.append(text)
        button.set_child(box)
        button.connect("clicked", lambda _button: (popover.popdown(), callback()))
        if label == "Switch appearance":
            self.theme_icon = image
            self.theme_label = text
        return button

    def _install_shortcuts(self):
        shortcuts = {
            "new-note": (self.new_note, ["<Control>t"]),
            "save-note": (self.save_note, ["<Control>s"]),
            "close-note": (self.close_active_note, ["<Control>w"]),
            "next-note": (self.select_next_note, ["<Control>Tab"]),
            "find": (self.focus_search, ["<Control>f"]),
            "duplicate": (self.duplicate_note, ["<Control>d"]),
            "checklist": (self.insert_checklist, ["<Control><Shift>l"]),
            "reset-zoom": (self.reset_zoom, ["<Control>0"]),
        }
        for action_name, (callback, accelerators) in shortcuts.items():
            action = Gio.SimpleAction.new(action_name, None)
            action.connect("activate", lambda _action, _param, cb=callback: cb())
            self.add_action(action)
            self.get_application().set_accels_for_action(
                f"win.{action_name}", accelerators
            )

    def load_notes(self):
        try:
            saved_notes = json.loads(NOTES_DATA_FILE.read_text(encoding="utf-8"))
            notes = saved_notes.get("notes", [])
            active_index = int(saved_notes.get("active_note_index", 0))
            if not isinstance(notes, list) or not notes:
                raise ValueError
            loaded_notes = []
            for index, note in enumerate(notes):
                if not isinstance(note, dict):
                    continue
                filename = note.get("file")
                if not valid_note_filename(filename):
                    filename = self.next_note_filename(loaded_notes)
                content = str(note.get("content", ""))
                try:
                    content = (NOTES_DIR / filename).read_text(encoding="utf-8")
                except (FileNotFoundError, OSError):
                    pass
                loaded_notes.append(
                    {
                        "title": str(note.get("title", f"Note {index + 1}")),
                        "content": content,
                        "file": filename,
                        "custom_title": bool(note.get("custom_title", False)),
                        "pinned": bool(note.get("pinned", False)),
                    }
                )
            if not loaded_notes:
                raise ValueError
            self.notes = loaded_notes
            self.active_note_index = min(max(active_index, 0), len(self.notes) - 1)
        except (
            FileNotFoundError,
            json.JSONDecodeError,
            OSError,
            TypeError,
            ValueError,
        ):
            try:
                existing_note = NOTES_FILE.read_text(encoding="utf-8")
            except (FileNotFoundError, OSError):
                existing_note = ""
            self.notes = [self.make_note("Note 1", existing_note)]
            self.active_note_index = 0
        self.show_active_note()

    def make_note(self, title, content="", custom_title=False):
        return {
            "title": title,
            "content": content,
            "file": self.next_note_filename(),
            "custom_title": custom_title,
            "pinned": False,
        }

    def next_note_filename(self, notes=None):
        source_notes = self.notes if notes is None else notes
        used_filenames = {
            note.get("file")
            for note in source_notes
            if valid_note_filename(note.get("file"))
        }
        next_number = 1
        while (
            f"note-{next_number}.txt" in used_filenames
            or (NOTES_DIR / f"note-{next_number}.txt").exists()
        ):
            next_number += 1
        return f"note-{next_number}.txt"

    def active_note(self):
        return self.notes[self.active_note_index]

    def show_active_note(self):
        if not self.notes:
            return
        self.loading_note = True
        note = self.active_note()
        self.note_text.get_buffer().set_text(note["content"])
        self.title_entry.set_text(note["title"])
        self.pin_button.set_active(note.get("pinned", False))
        self.pin_button.set_icon_name(
            "starred-symbolic" if note.get("pinned") else "non-starred-symbolic"
        )
        self.set_title(f"{note['title']} — Sticky Panote")
        self.update_note_count()
        self.find_in_note()
        GLib.idle_add(self.finish_loading_note, self.active_note_index)
        GLib.idle_add(grab_focus_once, self.note_text)

    def finish_loading_note(self, expected_index):
        if expected_index == self.active_note_index:
            self.loading_note = False
        return GLib.SOURCE_REMOVE

    def render_note_list(self):
        self.rendering_note_list = True
        try:
            while child := self.note_list.get_first_child():
                self.note_list.remove(child)

            query = self.note_filter.get_text().casefold().strip()
            selected_row = None
            ordered = sorted(
                enumerate(self.notes),
                key=lambda item: (not item[1].get("pinned", False), item[0]),
            )
            for index, note in ordered:
                title_match = query in note["title"].casefold()
                content_match = query in note["content"].casefold()
                if query and not title_match and not content_match:
                    continue
                preview = " ".join(note["content"].split())
                if preview.startswith(note["title"]):
                    preview = preview[len(note["title"]):].strip()
                row = Adw.ActionRow(
                    title=GLib.markup_escape_text(note["title"]),
                    subtitle=GLib.markup_escape_text(preview[:72] or "Empty note"),
                )
                row.set_title_lines(1)
                row.set_subtitle_lines(1)
                row.set_tooltip_text(note["title"])
                row.note_index = index
                if note.get("pinned"):
                    row.add_prefix(Gtk.Image.new_from_icon_name("starred-symbolic"))
                self.note_list.append(row)
                if index == self.active_note_index:
                    selected_row = row
            if selected_row is not None:
                self.note_list.select_row(selected_row)
        finally:
            self.rendering_note_list = False

    def on_note_row_selected(self, _list_box, row):
        if (
            self.rendering_note_list
            or row is None
            or row.note_index == self.active_note_index
        ):
            return
        self.switch_note(row.note_index)

    def sync_active_note(self):
        if self.loading_note or not self.notes:
            return
        buffer = self.note_text.get_buffer()
        content = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        note = self.active_note()
        note["content"] = content
        first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
        if first_line and not note.get("custom_title"):
            title = first_line[:20] + ("…" if len(first_line) > 20 else "")
            note["title"] = title
            self.title_entry.set_text(title)
            self.set_title(f"{title} — Sticky Panote")

    def new_note(self):
        self.sync_active_note()
        next_number = 1
        titles = {note["title"] for note in self.notes}
        while f"Note {next_number}" in titles:
            next_number += 1
        self.notes.append(self.make_note(f"Note {next_number}"))
        self.active_note_index = len(self.notes) - 1
        self.show_active_note()
        self.render_note_list()
        self.save_note()

    def switch_note(self, selected_index):
        if selected_index == self.active_note_index:
            return
        self.sync_active_note()
        self.active_note_index = selected_index
        self.show_active_note()
        self.render_note_list()
        self.save_note()

    def select_next_note(self):
        if len(self.notes) > 1:
            self.switch_note((self.active_note_index + 1) % len(self.notes))

    def close_active_note(self):
        self.sync_active_note()
        note = self.active_note()
        if note["content"].strip():
            dialog = Adw.AlertDialog(
                heading="Delete this note?",
                body=f'“{note["title"]}” will be removed from Sticky Panote.',
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("delete", "Delete")
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")
            dialog.choose(self, None, self.on_delete_note_response)
            return
        self.delete_active_note()

    def on_delete_note_response(self, dialog, result):
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response == "delete":
            self.delete_active_note()

    def delete_active_note(self):
        del self.notes[self.active_note_index]
        if not self.notes:
            self.notes = [self.make_note("Note 1")]
        self.active_note_index = min(self.active_note_index, len(self.notes) - 1)
        self.show_active_note()
        self.render_note_list()
        self.save_note()

    def on_title_focus_changed(self, entry, _param):
        if not entry.get_has_focus():
            self.rename_note()

    def rename_note(self):
        if self.loading_note or not self.notes:
            return
        note = self.active_note()
        title = self.title_entry.get_text().strip()
        if title.lower().endswith(".txt"):
            title = title[:-4].rstrip()
        title = title[:60]
        if not title:
            self.title_entry.set_text(note["title"])
            return
        if title != note["title"]:
            note["title"] = title
            note["custom_title"] = True
            self.set_title(f"{title} — Sticky Panote")
            self.render_note_list()
            self.save_note()

    def on_pin_toggled(self, button):
        if self.loading_note or not self.notes:
            return
        pinned = button.get_active()
        self.active_note()["pinned"] = pinned
        button.set_icon_name("starred-symbolic" if pinned else "non-starred-symbolic")
        self.render_note_list()
        self.save_note()

    def duplicate_note(self):
        self.sync_active_note()
        source = self.active_note()
        duplicate = self.make_note(
            f"{source['title']} copy"[:60],
            source["content"],
            custom_title=True,
        )
        self.notes.append(duplicate)
        self.active_note_index = len(self.notes) - 1
        self.show_active_note()
        self.render_note_list()
        self.save_note()

    def insert_checklist(self):
        buffer = self.note_text.get_buffer()
        iterator = buffer.get_iter_at_mark(buffer.get_insert())
        iterator.set_line_offset(0)
        buffer.insert(iterator, "- [ ] ")
        self.note_text.grab_focus()

    def insert_timestamp(self):
        buffer = self.note_text.get_buffer()
        iterator = buffer.get_iter_at_mark(buffer.get_insert())
        buffer.insert(iterator, datetime.now().strftime("%b %d, %Y  %I:%M %p"))
        self.note_text.grab_focus()

    def focus_search(self):
        if not self.search_button.get_active():
            self.search_button.set_active(True)
        else:
            GLib.idle_add(grab_focus_once, self.search_entry)

    def on_search_toggled(self, button):
        if button.get_active():
            self.search_revealer.set_reveal_child(True)
            GLib.idle_add(grab_focus_once, self.search_entry)
        else:
            self.clear_search()

    def clear_search(self):
        if self.search_button.get_active():
            self.search_button.set_active(False)
            return
        self.search_entry.set_text("")
        self.find_in_note()
        self.search_revealer.set_reveal_child(False)
        self.note_text.grab_focus()

    def find_in_note(self):
        buffer = self.note_text.get_buffer()
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        buffer.remove_tag(self.search_match_tag, start, end)
        buffer.remove_tag(self.search_current_tag, start, end)
        query = self.search_entry.get_text()
        if not query:
            self.search_count.set_text("")
            return
        content = buffer.get_text(start, end, True)
        content_folded = content.casefold()
        query_folded = query.casefold()
        offsets = []
        offset = 0
        while (match := content_folded.find(query_folded, offset)) != -1:
            offsets.append(match)
            offset = match + max(len(query_folded), 1)
        for match in offsets:
            match_start = buffer.get_iter_at_offset(match)
            match_end = buffer.get_iter_at_offset(match + len(query))
            buffer.apply_tag(self.search_match_tag, match_start, match_end)
        if offsets:
            match_start = buffer.get_iter_at_offset(offsets[0])
            match_end = buffer.get_iter_at_offset(offsets[0] + len(query))
            buffer.apply_tag(self.search_current_tag, match_start, match_end)
            buffer.place_cursor(match_start)
            self.note_text.scroll_to_iter(match_start, 0.1, False, 0, 0)
        self.search_count.set_text(str(len(offsets)))

    def on_note_changed(self, _buffer):
        if self.loading_note:
            return
        self.sync_active_note()
        self.save_status.set_text("Saving…")
        self.update_note_count()
        if self.save_source is not None:
            GLib.source_remove(self.save_source)
        self.save_source = GLib.timeout_add(600, self.save_note)

    def update_note_count(self):
        buffer = self.note_text.get_buffer()
        content = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        words = len(content.split())
        word_label = "word" if words == 1 else "words"
        self.note_count.set_text(
            f"{words} {word_label}"
        )

    def save_note(self):
        if self.save_source is not None:
            GLib.source_remove(self.save_source)
            self.save_source = None
        try:
            self.sync_active_note()
            NOTES_DIR.mkdir(exist_ok=True)
            for note in self.notes:
                filename = note.get("file")
                if not valid_note_filename(filename):
                    filename = self.next_note_filename()
                    note["file"] = filename
                atomic_write_text(NOTES_DIR / filename, note["content"])
            atomic_write_text(
                NOTES_DATA_FILE,
                json.dumps(
                    {"active_note_index": self.active_note_index, "notes": self.notes},
                    indent=2,
                )
                + "\n",
            )
        except OSError as exc:
            self.save_status.set_text("Save failed")
            self.show_error("Cannot Save Note", str(exc))
            return GLib.SOURCE_REMOVE
        self.save_status.set_text("Saved")
        self.render_note_list()
        return GLib.SOURCE_REMOVE

    def on_editor_scroll(self, controller, _dx, dy):
        state = controller.get_current_event_state()
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        self.set_editor_zoom(self.editor_font_size + (-1 if dy > 0 else 1))
        return True

    def reset_zoom(self):
        self.set_editor_zoom(11)

    def set_editor_zoom(self, size):
        self.editor_font_size = min(28, max(7, size))
        self.zoom_status.set_text(f"{round(self.editor_font_size / 11 * 100)}%")
        self.get_application().apply_editor_css(self.editor_font_size)

    def toggle_background(self):
        self.background_mode = "dark" if self.background_mode == "light" else "light"
        self.apply_background_mode()
        try:
            save_app_config({"background_mode": self.background_mode})
        except OSError as exc:
            self.show_error("Cannot Save Appearance", str(exc))

    def apply_background_mode(self):
        manager = Adw.StyleManager.get_default()
        if self.background_mode == "dark":
            manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            self.theme_icon.set_from_icon_name("weather-clear-symbolic")
            self.theme_label.set_text("Use light appearance")
            self.theme_button.set_tooltip_text("Use Light Appearance")
        else:
            manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
            self.theme_icon.set_from_icon_name("weather-clear-night-symbolic")
            self.theme_label.set_text("Use dark appearance")
            self.theme_button.set_tooltip_text("Use Dark Appearance")

    def run_command(self, command, error_title, environment=None):
        try:
            result = subprocess.run(
                command,
                cwd=APP_DIR,
                check=False,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            self.show_error(error_title, str(exc))
            return False
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "Command failed.").strip()
            self.show_error(error_title, output)
            return False
        return True

    def start_worker(self):
        self.run_command([str(START_SCRIPT)], "Start Failed")
        self.refresh_status()

    def start_or_pause(self):
        if app_is_running():
            self.toggle_pause()
        else:
            self.start_worker()

    def stop_worker(self):
        self.run_command([str(STOP_SCRIPT)], "Stop Failed")
        self.refresh_status()

    def start_hubstaff_work(self):
        if hubstaff_lunch_break():
            return
        if not HUBSTAFF_SCRIPT.is_file():
            self.show_error("Hubstaff Start Failed", "The Hubstaff launcher is missing.")
            return
        self.hubstaff_row.set_subtitle("Starting Hubstaff…")
        self.hubstaff_start_button.set_sensitive(False)
        try:
            subprocess.Popen(
                [str(HUBSTAFF_SCRIPT)],
                cwd=APP_DIR,
                env=clean_hubstaff_environment(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self.show_error("Hubstaff Start Failed", str(exc))
            self.hubstaff_start_button.set_sensitive(True)
            return
        GLib.timeout_add(800, self.refresh_hubstaff_status)

    def on_hubstaff_auto_start_changed(self, switch, _param):
        try:
            save_app_config({"hubstaff_auto_start": switch.get_active()})
        except OSError as exc:
            self.show_error("Cannot Save Hubstaff Setting", str(exc))

    def on_autorun_changed(self, switch, _param):
        try:
            set_autorun(switch.get_active())
        except OSError as exc:
            switch.handler_block(self.autorun_handler)
            switch.set_active(autorun_is_enabled())
            switch.handler_unblock(self.autorun_handler)
            self.show_error("Cannot Save Autorun Setting", str(exc))

    def toggle_pause(self):
        if not app_is_running():
            self.show_error("AutoMove", "AutoMove is not running.")
            self.refresh_status()
            return
        pause_key_code = load_shortcut_config()["pause_key_codes"][0]
        environment = os.environ.copy()
        environment["YDOTOOL_SOCKET"] = YDOTOOL_SOCKET
        if self.run_command(
            [
                "ydotool",
                "key",
                f"{KEY_LEFTCTRL}:1",
                f"{KEY_LEFTALT}:1",
                f"{pause_key_code}:1",
                f"{pause_key_code}:0",
                f"{KEY_LEFTALT}:0",
                f"{KEY_LEFTCTRL}:0",
            ],
            "Pause or Resume Failed",
            environment,
        ):
            GLib.timeout_add(800, self.refresh_status)

    @staticmethod
    def latest_activity(log_text):
        lines = [line.strip() for line in log_text.splitlines() if line.strip()]
        if not lines:
            return "Ready"
        latest = lines[-1]
        return latest[:90] + ("…" if len(latest) > 90 else "")

    def refresh_status(self):
        if self.status_source is not None:
            GLib.source_remove(self.status_source)
            self.status_source = None
        running = app_is_running()
        log_text = read_log_tail()
        paused = running and last_pause_state(log_text)
        activity = self.latest_activity(log_text)
        if paused:
            status = "Paused"
            action = "Resume"
        elif running:
            status = "Running"
            action = "Pause"
        else:
            status = "Stopped"
            action = "Start"
        self.activity_row.set_subtitle(f"{status} • {activity}")
        self.start_pause_button.set_label(action)
        self.stop_button.set_sensitive(running)
        application = self.get_application()
        application.lookup_action("automove-start").set_enabled(not running or paused)
        application.lookup_action("automove-pause").set_enabled(running and not paused)
        application.lookup_action("automove-stop").set_enabled(running)
        self.status_source = GLib.timeout_add(1500, self.refresh_status)
        return GLib.SOURCE_REMOVE

    def apply_hubstaff_lunch_schedule(self, tracking, now=None):
        now = now if now is not None else datetime.now(HUBSTAFF_TIMEZONE)
        today = now.date().isoformat()
        command = None
        if hubstaff_lunch_break(now) and tracking:
            command = "stop"
        elif hubstaff_lunch_break(now):
            self.hubstaff_lunch_resume_date = today
        elif now.hour >= 13 and self.hubstaff_lunch_resume_date == today:
            if tracking:
                self.hubstaff_lunch_resume_date = None
            else:
                command = "resume"
        elif self.hubstaff_lunch_resume_date not in (None, today):
            self.hubstaff_lunch_resume_date = None

        if command is not None:
            try:
                result = subprocess.run(
                    [str(HUBSTAFF_CLI), command],
                    check=False,
                    env=clean_hubstaff_environment(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"Hubstaff lunch {command} failed: {exc}", file=sys.stderr)
                return False
            if result.returncode != 0:
                return False
            self.hubstaff_lunch_resume_date = today if command == "stop" else None

        saved_date = load_app_config().get("hubstaff_lunch_resume_date")
        if saved_date != self.hubstaff_lunch_resume_date:
            try:
                save_app_config(
                    {"hubstaff_lunch_resume_date": self.hubstaff_lunch_resume_date}
                )
            except OSError as exc:
                print(f"Cannot save Hubstaff lunch state: {exc}", file=sys.stderr)
        return command is not None

    def refresh_hubstaff_status(self):
        if self.hubstaff_status_source is not None:
            GLib.source_remove(self.hubstaff_status_source)
            self.hubstaff_status_source = None
        status, tracking = read_hubstaff_status()
        if self.apply_hubstaff_lunch_schedule(tracking):
            status, tracking = read_hubstaff_status()
        lunch_break = hubstaff_lunch_break()
        if lunch_break:
            status = (
                "Lunch break • Resumes at 1:00 PM"
                if self.hubstaff_lunch_resume_date
                else "Lunch break • 12:00–1:00 PM"
            )
        self.hubstaff_row.set_subtitle(status)
        self.hubstaff_start_button.set_label(
            "Lunch Break" if lunch_break else "Tracking" if tracking else "Start Work"
        )
        self.hubstaff_start_button.set_sensitive(not tracking and not lunch_break)
        self.hubstaff_status_source = GLib.timeout_add(
            5000, self.refresh_hubstaff_status
        )
        return GLib.SOURCE_REMOVE

    def show_error(self, heading, body):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", "Close")
        dialog.present(self)

    def on_close_request(self, _window):
        self.save_note()
        self.set_visible(False)
        return True

    def on_window_realized(self, _window):
        surface = self.get_surface()
        if isinstance(surface, Gdk.Toplevel):
            surface.connect("notify::state", self.on_toplevel_state_changed)

    def on_toplevel_state_changed(self, surface, _param):
        if surface.get_state() & Gdk.ToplevelState.MINIMIZED:
            GLib.idle_add(self.hide_after_minimize)

    def hide_after_minimize(self):
        self.save_note()
        self.set_visible(False)
        return GLib.SOURCE_REMOVE

    def show_from_tray(self):
        self.set_visible(True)
        self.present()
        self.note_text.grab_focus()

    def quit_application(self):
        self.save_note()
        application = self.get_application()
        application.quit_requested = True
        application.quit()


class StickyPanoteApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None
        self.tray_process = None
        self.css_provider = Gtk.CssProvider()
        self.quit_requested = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        for name in ("automove-start", "automove-pause", "automove-stop"):
            action = Gio.SimpleAction.new(name, None)
            action.set_enabled(False)
            action.connect("activate", self.on_automove_action)
            self.add_action(action)
        Gtk.Window.set_default_icon_name("accessories-text-editor")
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(
            display,
            self.css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self.apply_editor_css(11)
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self.on_show_signal)
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.on_quit_signal)

    def do_activate(self):
        if self.window is None:
            self.window = StickyPanoteWindow(self)
            self.window.connect("destroy", self.on_window_destroyed)
            self.start_tray_process()
        self.window.show_from_tray()

    def apply_editor_css(self, font_size):
        css = f"""
            .sticky-panote .note-editor {{
                font-family: Cantarell, Sans;
                font-size: {font_size}pt;
                line-height: 1.55;
            }}
            .sticky-panote .note-surface {{
                background: @view_bg_color;
            }}
            .sticky-panote .note-title {{
                font-size: 24px;
                font-weight: 600;
                background: transparent;
                border: none;
                box-shadow: none;
                padding: 4px;
                min-height: 36px;
            }}
            .sticky-panote .note-title:focus-within {{
                box-shadow: inset 0 -2px alpha(@accent_bg_color, 0.5);
            }}
            .sticky-panote .notes-list row {{
                border-radius: 10px;
                margin: 2px 8px;
            }}
            .sticky-panote .notes-list row:selected {{
                background: alpha(@accent_bg_color, 0.12);
                color: @window_fg_color;
            }}
            .sticky-panote .statusbar {{
                font-size: 11px;
                border: none;
            }}
        """
        self.css_provider.load_from_string(css)

    def start_tray_process(self):
        if not TRAY_SCRIPT.is_file():
            return
        tray_environment = os.environ.copy()
        for variable in (
            "GTK_PATH",
            "GTK_EXE_PREFIX",
            "GTK_DATA_PREFIX",
            "GDK_PIXBUF_MODULE_FILE",
            "GIO_MODULE_DIR",
            "SNAP",
            "SNAP_USER_DATA",
            "SNAP_USER_COMMON",
            "XDG_DATA_HOME",
        ):
            tray_environment.pop(variable, None)
        try:
            self.tray_process = subprocess.Popen(
                [sys.executable, str(TRAY_SCRIPT), str(os.getpid())],
                cwd=APP_DIR,
                env=tray_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self.tray_process = None

    def stop_tray_process(self):
        if self.tray_process is not None and self.tray_process.poll() is None:
            self.tray_process.terminate()
        self.tray_process = None

    def on_show_signal(self):
        if self.window is not None:
            self.window.show_from_tray()
        return GLib.SOURCE_CONTINUE

    def on_automove_action(self, action, _parameter):
        if self.window is None:
            return
        running = app_is_running()
        paused = running and last_pause_state(read_log_tail())
        if action.get_name() == "automove-start":
            if not running:
                self.window.start_worker()
            elif paused:
                self.window.toggle_pause()
        elif action.get_name() == "automove-pause":
            if running and not paused:
                self.window.toggle_pause()
        elif action.get_name() == "automove-stop" and running:
            self.window.stop_worker()

    def on_quit_signal(self):
        if self.window is not None:
            self.window.quit_application()
        else:
            self.quit()
        return GLib.SOURCE_REMOVE

    def on_window_destroyed(self, _window):
        self.window = None
        if self.quit_requested:
            self.stop_tray_process()

    def do_shutdown(self):
        self.stop_tray_process()
        Adw.Application.do_shutdown(self)


def main():
    app = StickyPanoteApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
