import base64
import io
import json
import os
import queue
import subprocess
import threading
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

TRAY_TYPELIB_DIR = (
    Path.home()
    / ".local/share/automove-tray-runtime/usr/lib/x86_64-linux-gnu/girepository-1.0"
)
if TRAY_TYPELIB_DIR.is_dir():
    existing_typelib_path = os.environ.get("GI_TYPELIB_PATH")
    typelib_paths = [str(TRAY_TYPELIB_DIR)]
    if existing_typelib_path:
        typelib_paths.append(existing_typelib_path)
    os.environ["GI_TYPELIB_PATH"] = os.pathsep.join(typelib_paths)

import pystray
from PIL import Image, ImageDraw

APP_DIR = Path(__file__).resolve().parent
PID_FILE = APP_DIR / "auto_mouse_move.pid"
LOG_FILE = APP_DIR / "auto_mouse_move.log"
START_SCRIPT = APP_DIR / "start_auto_mouse.sh"
STOP_SCRIPT = APP_DIR / "stop_auto_mouse.sh"
CONFIG_FILE = APP_DIR / "auto_mouse_move_config.json"
NOTES_FILE = APP_DIR / "automove_notes.txt"
NOTES_DATA_FILE = APP_DIR / "automove_notes.json"
NOTES_DIR = APP_DIR / "notes"
YDOTOOL_SOCKET = "/tmp/automove-ydotool.sock"

KEY_LEFTCTRL = 29
KEY_LEFTALT = 56
DEFAULT_PAUSE_KEY_CODES = [99, 119, 411]
DEFAULT_STOP_KEY_CODE = 107
KEY_PRESETS = {
    "Pause": 119,
    "Print/SysRq": 99,
    "Break": 411,
    "End": 107,
    "Keypad End": 79,
    "F12": 88,
}

# Modern Sticky Notes Palette — flat, neutral-dark shell with an indigo accent
THEME = {
    "bg_app": "#18181B",            # Zinc-900 app background
    "bg_header": "#111113",         # Near-black header
    "bg_tabs": "#111113",           # Tab strip background
    "bg_panel": "#1F1F23",          # Automove drawer background
    "bg_editor": "#FDFDFC",         # Clean near-white paper background
    "bg_toolbar": "#F5F5F6",        # Note toolbar strip
    "bg_tab_active": "#FDFDFC",     # Active tab matches note paper
    "bg_surface": "#232326",        # Muted button background
    "bg_surface_hover": "#2E2E32",  # Hover state
    "border": "#E6E6E9",            # Paper / light borders
    "accent_primary": "#6366F1",    # Indigo-500 accent
    "accent_hover": "#818CF8",      # Indigo-400 hover
    "fg_main": "#1C1C1F",           # Near-black note text
    "fg_header": "#F4F4F5",         # Soft light text for top header
    "fg_secondary": "#9A9AA2",      # Subtitle & secondary text
    "fg_muted": "#6C6C74",          # Low prominence labels
    "status_running": "#22C55E",    # Green-500
    "status_paused": "#F59E0B",     # Amber-500
    "status_stopped": "#71717A",    # Neutral gray-500
    "btn_muted_bg": "#2A2A2E",      # Low visibility automove controls
    "btn_muted_hover": "#36363B",
    "btn_danger": "#DC2626",        # Red-600 danger button
    "btn_danger_hover": "#EF4444",
    "btn_action_bg": "#F0F0F2",     # Editor action button background
    "btn_action_hover": "#E4E4E8",  # Editor action button hover
    "btn_action_active": "#D8D8DE", # Editor action button active
    "btn_border": "#E0E0E5",        # Editor action button border
}


def load_app_config():
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return config if isinstance(config, dict) else {}


def save_app_config(updates):
    config = load_app_config()
    config.update(updates)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def load_shortcut_config():
    config = {
        "pause_key_codes": DEFAULT_PAUSE_KEY_CODES,
        "stop_key_code": DEFAULT_STOP_KEY_CODE,
    }

    saved_config = load_app_config()

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


def save_shortcut_config(pause_key_codes, stop_key_code):
    save_app_config(
        {
            "pause_key_codes": sorted(set(pause_key_codes)),
            "stop_key_code": stop_key_code,
        }
    )


def pid_is_running(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_pid():
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def app_is_running():
    pid = read_pid()
    return pid is not None and pid_is_running(pid)


def read_log_tail(max_lines=80):
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return ""
    return "\n".join(lines[-max_lines:])


def last_pause_state(log_text):
    paused_at = log_text.rfind("Paused by shortcut.")
    resumed_at = log_text.rfind("Resumed by shortcut.")
    if paused_at == -1:
        return False
    return paused_at > resumed_at


def valid_note_filename(filename):
    return (
        isinstance(filename, str)
        and Path(filename).name == filename
        and filename.lower().endswith(".txt")
    )


class AutoMouseGui(tk.Tk):
    def __init__(self):
        super().__init__(className="MyStickyNotes")

        self.title("Sticky Notes")
        self.geometry("1080x700")
        self.minsize(720, 480)
        self.configure(bg=THEME["bg_app"])
        icon_buffer = io.BytesIO()
        self.create_tray_image().save(icon_buffer, format="PNG")
        self.window_icon = tk.PhotoImage(
            data=base64.b64encode(icon_buffer.getvalue()).decode("ascii")
        )
        self.iconphoto(True, self.window_icon)

        self.status_var = tk.StringVar(value="Stopped")
        self.save_status_var = tk.StringVar(value="Saved")
        self.note_count_var = tk.StringVar(value="0 words  •  0 characters")
        self.zoom_var = tk.StringVar(value="100%")
        self.document_title_var = tk.StringVar(value="note.txt")
        self.document_path_var = tk.StringVar(value="/notes")
        self.activity_var = tk.StringVar(value="Ready")
        self.note_title_var = tk.StringVar(value="Note 1")
        self.search_var = tk.StringVar()
        self.search_status_var = tk.StringVar()
        self.background_mode = load_app_config().get("background_mode", "light")
        if self.background_mode not in {"light", "dark"}:
            self.background_mode = "light"
        self.editor_font_size = 11
        self.editor_font = tkfont.Font(family="Ubuntu", size=self.editor_font_size)
        self.control_panel_visible = False
        self.save_job = None
        self.status_job = None
        self.tray_commands = queue.Queue()
        self.tray_icon = None
        self.quitting = False
        self.notes = []
        self.active_note_index = 0
        self.loading_note = False

        self._build_layout()
        self.load_notes()
        self.render_tabs()

        self.note_text.edit_modified(False)
        self.note_text.bind("<<Modified>>", self.note_changed)
        self.bind("<Control-s>", self.save_note)
        self.bind("<Control-t>", lambda _e: self.new_tab())
        self.bind("<Control-w>", lambda _e: self.close_active_tab())
        self.bind("<Control-Tab>", self.select_next_tab)
        self.bind("<Control-f>", self.focus_search)
        self.bind("<Control-d>", self.duplicate_note)
        self.bind("<Control-Shift-L>", self.insert_checklist)
        self.bind("<Escape>", self.clear_search)
        self.bind("<Unmap>", self.window_unmapped)
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        self.save_note()
        self.start_tray_icon()
        self.after(200, self.process_tray_commands)
        self.refresh_status()

    def _build_layout(self):
        # Header bar
        self.header = tk.Frame(
            self,
            bg=THEME["bg_header"],
            height=52,
            highlightthickness=0,
        )
        self.header.pack(fill=tk.X)
        self.header.pack_propagate(False)

        brand = tk.Frame(self.header, bg=THEME["bg_header"])
        brand.pack(side=tk.LEFT, fill=tk.Y, padx=(14, 0))

        icon_badge = tk.Frame(brand, bg=THEME["accent_primary"], width=30, height=30)
        icon_badge.pack(side=tk.LEFT, pady=11)
        icon_badge.pack_propagate(False)

        tk.Label(
            icon_badge,
            text="✎",
            bg=THEME["accent_primary"],
            fg="#FFFFFF",
            font=("Ubuntu", 13, "bold"),
        ).pack(expand=True)

        brand_text = tk.Frame(brand, bg=THEME["bg_header"])
        brand_text.pack(side=tk.LEFT, padx=(10, 0), pady=8)

        tk.Label(
            brand_text,
            text="Sticky Notes",
            bg=THEME["bg_header"],
            fg=THEME["fg_header"],
            font=("Ubuntu", 11, "bold"),
            anchor="w",
        ).pack(anchor="w")

        tk.Label(
            brand_text,
            textvariable=self.document_title_var,
            bg=THEME["bg_header"],
            fg=THEME["fg_secondary"],
            font=("Ubuntu", 8),
            anchor="w",
        ).pack(anchor="w")

        header_actions = tk.Frame(self.header, bg=THEME["bg_header"])
        header_actions.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10))

        # Discrete status badge — flat pill with a colored dot
        self.status_badge = tk.Frame(
            header_actions,
            bg=THEME["bg_surface"],
            padx=10,
            pady=4,
            highlightthickness=0,
        )
        self.status_badge.pack(side=tk.LEFT, padx=(0, 10), pady=14)

        self.status_label = tk.Label(
            self.status_badge,
            textvariable=self.status_var,
            bg=THEME["bg_surface"],
            fg=THEME["fg_muted"],
            font=("Ubuntu", 8, "bold"),
        )
        self.status_label.pack()

        # Header buttons — flat, borderless, modern
        new_document_button = tk.Button(
            header_actions,
            text="＋",
            command=self.new_tab,
            bg=THEME["bg_surface"],
            fg=THEME["fg_header"],
            activebackground=THEME["bg_surface_hover"],
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            bd=0,
            width=3,
            font=("Ubuntu", 12, "bold"),
            cursor="hand2",
        )
        new_document_button.pack(side=tk.LEFT, pady=10, padx=(0, 6))
        self._add_hover(
            new_document_button,
            THEME["bg_surface"],
            THEME["bg_surface_hover"],
        )

        self.menu_button = tk.Button(
            header_actions,
            text="⚙",
            command=self.toggle_control_panel,
            bg=THEME["bg_surface"],
            fg=THEME["fg_header"],
            activebackground=THEME["bg_surface_hover"],
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            bd=0,
            width=3,
            font=("Ubuntu", 10),
            cursor="hand2",
        )
        self.menu_button.pack(side=tk.LEFT, pady=10)
        self._add_hover(
            self.menu_button,
            THEME["bg_surface"],
            THEME["bg_surface_hover"],
        )

        # Tab Strip
        self.tab_bar = tk.Frame(
            self,
            bg=THEME["bg_tabs"],
            height=38,
            highlightthickness=0,
        )
        self.tab_bar.pack(fill=tk.X)
        self.tab_bar.pack_propagate(False)

        self.tabs_container = tk.Frame(self.tab_bar, bg=THEME["bg_tabs"])
        self.tabs_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 10))

        # Discrete Hidden Automove Drawer
        self.control_panel = tk.Frame(
            self,
            bg=THEME["bg_panel"],
            padx=16,
            pady=12,
            highlightthickness=1,
            highlightbackground="#2C2C31",
        )

        panel_header = tk.Frame(self.control_panel, bg=THEME["bg_panel"])
        panel_header.pack(fill=tk.X)

        tk.Label(
            panel_header,
            text="AUTOMOVE SERVICE",
            bg=THEME["bg_panel"],
            fg=THEME["fg_secondary"],
            font=("Ubuntu", 8, "bold"),
        ).pack(side=tk.LEFT)

        self.panel_status_label = tk.Label(
            panel_header,
            textvariable=self.status_var,
            bg=THEME["bg_panel"],
            fg=THEME["fg_muted"],
            font=("Ubuntu", 8),
        )
        self.panel_status_label.pack(side=tk.RIGHT)

        controls = tk.Frame(self.control_panel, bg=THEME["bg_panel"])
        controls.pack(fill=tk.X, pady=(8, 6))

        # Muted automove control buttons — flat modern style
        self.start_pause_button = tk.Button(
            controls,
            text="▶  Start Automove",
            command=self.start_or_pause,
            bg=THEME["btn_muted_bg"],
            fg=THEME["fg_header"],
            activebackground=THEME["btn_muted_hover"],
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            bd=0,
            pady=7,
            font=("Ubuntu", 8, "bold"),
            cursor="hand2",
        )
        self.start_pause_button.pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True,
            padx=(0, 6),
        )

        self.stop_button = tk.Button(
            controls,
            text="■  Stop Automove",
            command=self.stop_worker,
            bg=THEME["btn_danger"],
            fg="#FFFFFF",
            activebackground=THEME["btn_danger_hover"],
            activeforeground="#FFFFFF",
            disabledforeground="#8A8A90",
            relief=tk.FLAT,
            bd=0,
            pady=7,
            font=("Ubuntu", 8, "bold"),
            cursor="hand2",
        )
        self.stop_button.pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True,
            padx=(6, 0),
        )

        activity_row = tk.Frame(self.control_panel, bg=THEME["bg_surface"], padx=10, pady=6)
        activity_row.pack(fill=tk.X, pady=(2, 0))

        tk.Label(
            activity_row,
            text="Activity",
            bg=THEME["bg_surface"],
            fg=THEME["fg_muted"],
            font=("Ubuntu", 7),
        ).pack(side=tk.LEFT)

        self.activity_label = tk.Label(
            activity_row,
            textvariable=self.activity_var,
            bg=THEME["bg_surface"],
            fg=THEME["fg_secondary"],
            font=("Ubuntu", 7),
            anchor="e",
        )
        self.activity_label.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))

        # Main Sticky Note Editor
        body = tk.Frame(self, bg=THEME["bg_app"])
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 6))

        self.editor_frame = tk.Frame(
            body,
            bg=THEME["bg_editor"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
        )
        self.editor_frame.pack(fill=tk.BOTH, expand=True)

        self.editor_toolbar = tk.Frame(
            self.editor_frame,
            bg=THEME["bg_toolbar"],
            padx=12,
            pady=8,
            highlightthickness=1,
            highlightbackground=THEME["border"],
        )
        self.editor_toolbar.pack(fill=tk.X)

        self.pin_button = tk.Button(
            self.editor_toolbar,
            text="☆",
            command=self.toggle_pin,
            bg=THEME["bg_toolbar"],
            fg=THEME["accent_primary"],
            activebackground=THEME["bg_toolbar"],
            activeforeground=THEME["accent_hover"],
            relief=tk.FLAT,
            bd=0,
            width=2,
            font=("Ubuntu", 12),
            cursor="hand2",
        )
        self.pin_button.pack(side=tk.LEFT)

        self.title_entry = tk.Entry(
            self.editor_toolbar,
            textvariable=self.note_title_var,
            bg=THEME["bg_toolbar"],
            fg=THEME["fg_main"],
            insertbackground=THEME["fg_main"],
            selectbackground="#C7D2FE",
            relief=tk.FLAT,
            bd=0,
            font=("Ubuntu", 10, "bold"),
        )
        self.title_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 12))
        self.title_entry.bind("<Return>", self.rename_note)
        self.title_entry.bind("<FocusOut>", self.rename_note)

        self.background_button = tk.Button(
            self.editor_toolbar,
            command=self.toggle_background,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            width=8,
            pady=4,
            font=("Ubuntu", 8, "bold"),
            cursor="hand2",
        )
        self.background_button.pack(side=tk.LEFT, padx=(0, 5))

        # Enhanced Action Buttons with Borders and Active Feedback
        self.action_buttons = []
        for label, command, width in (
            ("☑ Checklist", self.insert_checklist, 10),
            ("◷ Time", self.insert_timestamp, 7),
            ("⧉ Duplicate", self.duplicate_note, 10),
        ):
            button = tk.Button(
                self.editor_toolbar,
                text=label,
                command=command,
                bg=THEME["btn_action_bg"],
                fg=THEME["fg_main"],
                activebackground=THEME["btn_action_active"],
                activeforeground=THEME["fg_main"],
                relief=tk.FLAT,
                bd=0,
                highlightthickness=1,
                highlightbackground=THEME["btn_border"],
                width=width,
                pady=4,
                font=("Ubuntu", 8, "bold"),
                cursor="hand2",
            )
            button.pack(side=tk.LEFT, padx=(0, 5))
            self._add_hover(
                button,
                THEME["btn_action_bg"],
                THEME["btn_action_hover"],
            )
            self.action_buttons.append(button)

        self.search_shell = tk.Frame(
            self.editor_toolbar,
            bg=THEME["btn_action_bg"],
            highlightthickness=1,
            highlightbackground=THEME["btn_border"],
        )
        self.search_shell.pack(side=tk.RIGHT, ipady=1)

        self.search_icon_label = tk.Label(
            self.search_shell,
            text="⌕",
            bg=THEME["btn_action_bg"],
            fg=THEME["fg_muted"],
            font=("Ubuntu", 10),
        )
        self.search_icon_label.pack(side=tk.LEFT, padx=(6, 2))

        self.search_entry = tk.Entry(
            self.search_shell,
            textvariable=self.search_var,
            bg=THEME["btn_action_bg"],
            fg=THEME["fg_main"],
            insertbackground=THEME["fg_main"],
            selectbackground="#C7D2FE",
            relief=tk.FLAT,
            bd=0,
            width=13,
            font=("Ubuntu", 8),
        )
        self.search_entry.pack(side=tk.LEFT, ipady=3)
        self.search_entry.bind("<KeyRelease>", self.find_in_note)

        self.search_status_label = tk.Label(
            self.search_shell,
            textvariable=self.search_status_var,
            bg=THEME["btn_action_bg"],
            fg=THEME["fg_muted"],
            width=3,
            font=("Ubuntu", 7),
        )
        self.search_status_label.pack(side=tk.LEFT, padx=(2, 4))

        self.text_shell = tk.Frame(self.editor_frame, bg=THEME["bg_editor"])
        self.text_shell.pack(fill=tk.BOTH, expand=True)

        self.note_text = tk.Text(
            self.text_shell,
            bg=THEME["bg_editor"],
            fg=THEME["fg_main"],
            insertbackground=THEME["fg_main"],
            selectbackground="#C7D2FE",
            selectforeground=THEME["fg_main"],
            relief=tk.FLAT,
            bd=0,
            padx=20,
            pady=16,
            wrap=tk.WORD,
            undo=True,
            spacing1=3,
            spacing3=4,
            font=self.editor_font,
        )
        self.note_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.note_text.bind("<Control-MouseWheel>", self.zoom_with_mousewheel)
        self.note_text.bind("<Control-Button-4>", self.zoom_with_mousewheel)
        self.note_text.bind("<Control-Button-5>", self.zoom_with_mousewheel)
        self.bind("<Control-Key-0>", self.reset_zoom)

        self.editor_scrollbar = tk.Scrollbar(
            self.text_shell,
            command=self.note_text.yview,
            bg="#D8D8DE",
            troughcolor=THEME["bg_editor"],
            activebackground="#C4C4CC",
            relief=tk.FLAT,
            bd=0,
            width=8,
        )
        self.editor_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.note_text.configure(yscrollcommand=self.editor_scrollbar.set)
        self.note_text.tag_configure("search_match", background="#FDE68A")
        self.note_text.tag_configure(
            "search_current",
            background=THEME["accent_primary"],
            foreground="#FFFFFF",
        )
        self.apply_background_mode()

        # Footer Status Bar
        footer = tk.Frame(self, bg=THEME["bg_app"], padx=12)
        footer.pack(fill=tk.X, pady=(2, 6))

        save_status = tk.Label(
            footer,
            textvariable=self.save_status_var,
            bg=THEME["bg_app"],
            fg=THEME["fg_muted"],
            font=("Ubuntu", 8),
        )
        save_status.pack(side=tk.LEFT)

        count_label = tk.Label(
            footer,
            textvariable=self.note_count_var,
            bg=THEME["bg_app"],
            fg=THEME["fg_muted"],
            font=("Ubuntu", 8),
        )
        count_label.pack(side=tk.RIGHT)

        zoom_label = tk.Label(
            footer,
            textvariable=self.zoom_var,
            bg=THEME["bg_app"],
            fg=THEME["fg_muted"],
            font=("Ubuntu", 8),
        )
        zoom_label.pack(side=tk.RIGHT, padx=(0, 14))

        tk.Label(
            footer,
            text="UTF-8",
            bg=THEME["bg_app"],
            fg=THEME["fg_muted"],
            font=("Ubuntu", 8),
        ).pack(side=tk.RIGHT, padx=(0, 14))

        self.note_text.focus_set()

    def zoom_with_mousewheel(self, event):
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            direction = 1
        elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            direction = -1
        else:
            return "break"

        self.set_editor_zoom(self.editor_font_size + direction)
        return "break"

    def reset_zoom(self, _event=None):
        self.set_editor_zoom(11)
        return "break"

    def set_editor_zoom(self, font_size):
        self.editor_font_size = min(28, max(7, font_size))
        self.editor_font.configure(size=self.editor_font_size)
        self.zoom_var.set(f"{round(self.editor_font_size / 11 * 100)}%")

    def toggle_background(self):
        self.background_mode = (
            "dark" if self.background_mode == "light" else "light"
        )
        self.apply_background_mode()
        try:
            save_app_config({"background_mode": self.background_mode})
        except OSError as exc:
            messagebox.showerror("Cannot save appearance", str(exc))

    def apply_background_mode(self):
        if self.background_mode == "dark":
            colors = {
                "editor": "#1D1D21",
                "toolbar": "#25252A",
                "text": "#F4F4F5",
                "muted": "#A1A1AA",
                "button": "#323238",
                "button_hover": "#3D3D44",
                "button_active": "#484850",
                "border": "#45454D",
                "selection": "#4A5368",
                "match": "#66551F",
                "scrollbar": "#3A3A40",
                "scrollbar_active": "#4A4A52",
            }
            toggle_text = "☀ Light"
        else:
            colors = {
                "editor": "#FDFDFC",
                "toolbar": "#F5F5F6",
                "text": "#1C1C1F",
                "muted": "#6C6C74",
                "button": "#F0F0F2",
                "button_hover": "#E4E4E8",
                "button_active": "#D8D8DE",
                "border": "#E0E0E5",
                "selection": "#C7D2FE",
                "match": "#FDE68A",
                "scrollbar": "#D8D8DE",
                "scrollbar_active": "#C4C4CC",
            }
            toggle_text = "☾ Dark"

        self.editor_frame.configure(
            bg=colors["editor"],
            highlightbackground=colors["border"],
        )
        self.editor_toolbar.configure(
            bg=colors["toolbar"],
            highlightbackground=colors["border"],
        )
        self.text_shell.configure(bg=colors["editor"])
        self.pin_button.configure(
            bg=colors["toolbar"],
            fg=THEME["accent_primary"],
            activebackground=colors["toolbar"],
        )
        self.title_entry.configure(
            bg=colors["toolbar"],
            fg=colors["text"],
            insertbackground=colors["text"],
            selectbackground=colors["selection"],
        )

        all_action_buttons = [self.background_button, *self.action_buttons]
        for button in all_action_buttons:
            button.configure(
                bg=colors["button"],
                fg=colors["text"],
                activebackground=colors["button_active"],
                activeforeground=colors["text"],
                highlightbackground=colors["border"],
            )
            self._add_hover(
                button,
                colors["button"],
                colors["button_hover"],
            )
        self.background_button.configure(text=toggle_text)

        self.search_shell.configure(
            bg=colors["button"],
            highlightbackground=colors["border"],
        )
        self.search_icon_label.configure(
            bg=colors["button"],
            fg=colors["muted"],
        )
        self.search_entry.configure(
            bg=colors["button"],
            fg=colors["text"],
            insertbackground=colors["text"],
            selectbackground=colors["selection"],
        )
        self.search_status_label.configure(
            bg=colors["button"],
            fg=colors["muted"],
        )
        self.note_text.configure(
            bg=colors["editor"],
            fg=colors["text"],
            insertbackground=colors["text"],
            selectbackground=colors["selection"],
            selectforeground=colors["text"],
        )
        self.note_text.tag_configure(
            "search_match",
            background=colors["match"],
            foreground=colors["text"],
        )
        self.note_text.tag_configure(
            "search_current",
            background=THEME["accent_primary"],
            foreground="#FFFFFF",
        )
        self.editor_scrollbar.configure(
            bg=colors["scrollbar"],
            troughcolor=colors["editor"],
            activebackground=colors["scrollbar_active"],
        )

    @staticmethod
    def _add_hover(button, normal_color, hover_color):
        button.configure(bg=normal_color, activebackground=hover_color)
        button.bind(
            "<Enter>",
            lambda _e: button.configure(bg=hover_color)
            if str(button["state"]) != tk.DISABLED
            else None,
        )
        button.bind(
            "<Leave>",
            lambda _e: button.configure(bg=normal_color)
            if str(button["state"]) != tk.DISABLED
            else None,
        )

    def toggle_control_panel(self):
        if self.control_panel_visible:
            self.control_panel.pack_forget()
            self.menu_button.configure(text="⚙", bg=THEME["bg_surface"])
        else:
            self.control_panel.pack(fill=tk.X, after=self.tab_bar)
            self.menu_button.configure(text="×", bg=THEME["bg_surface_hover"])
        self.control_panel_visible = not self.control_panel_visible

    def load_notes(self):
        try:
            saved_notes = json.loads(NOTES_DATA_FILE.read_text(encoding="utf-8"))
            notes = saved_notes.get("notes", [])
            active_index = int(saved_notes.get("active_note_index", 0))
            if not isinstance(notes, list) or not notes:
                raise ValueError
            self.notes = []
            for index, note in enumerate(notes):
                if not isinstance(note, dict):
                    continue
                filename = note.get("file")
                if not valid_note_filename(filename):
                    filename = self.next_note_filename()
                content = str(note.get("content", ""))
                note_path = NOTES_DIR / filename
                try:
                    content = note_path.read_text(encoding="utf-8")
                except (FileNotFoundError, OSError):
                    pass
                self.notes.append(
                    {
                        "title": str(note.get("title", f"Note {index + 1}")),
                        "content": content,
                        "file": filename,
                        "custom_title": bool(note.get("custom_title", False)),
                        "pinned": bool(note.get("pinned", False)),
                    }
                )
            if not self.notes:
                raise ValueError
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
            self.notes = [
                {
                    "title": "Note 1",
                    "content": existing_note,
                    "file": self.next_note_filename(),
                    "custom_title": False,
                    "pinned": False,
                }
            ]
            self.active_note_index = 0

        self.show_active_note()

    def next_note_filename(self):
        used_filenames = {
            note.get("file")
            for note in getattr(self, "notes", [])
            if valid_note_filename(note.get("file"))
        }
        next_number = 1
        while (
            f"note-{next_number}.txt" in used_filenames
            or (NOTES_DIR / f"note-{next_number}.txt").exists()
        ):
            next_number += 1
        return f"note-{next_number}.txt"

    def show_active_note(self):
        self.loading_note = True
        active_note = self.notes[self.active_note_index]
        self.note_text.delete("1.0", tk.END)
        self.note_text.insert("1.0", active_note["content"])
        self.note_text.edit_modified(False)
        self.note_title_var.set(active_note["title"])
        self.pin_button.configure(text="★" if active_note.get("pinned") else "☆")
        self.loading_note = False
        self.update_note_count()
        self.find_in_note()

    def render_tabs(self):
        for child in self.tabs_container.winfo_children():
            child.destroy()

        for index, note in enumerate(self.notes):
            active = index == self.active_note_index
            bg_col = THEME["bg_tab_active"] if active else THEME["bg_tabs"]
            fg_col = THEME["fg_main"] if active else THEME["fg_secondary"]

            tab_wrap = tk.Frame(self.tabs_container, bg=THEME["bg_tabs"])
            tab_wrap.pack(side=tk.LEFT, padx=(0, 2), pady=(3, 0))

            accent_strip = tk.Frame(
                tab_wrap,
                bg=THEME["accent_primary"] if active else THEME["bg_tabs"],
                height=2,
            )
            accent_strip.pack(fill=tk.X)

            tab_frame = tk.Frame(tab_wrap, bg=bg_col)
            tab_frame.pack(fill=tk.Y)

            tab = tk.Button(
                tab_frame,
                text=("★ " if note.get("pinned") else "") + note["title"],
                command=lambda selected=index: self.switch_tab(selected),
                bg=bg_col,
                fg=fg_col,
                activebackground=THEME["bg_tab_active"],
                activeforeground=THEME["fg_main"],
                relief=tk.FLAT,
                bd=0,
                padx=12,
                pady=4,
                font=("Ubuntu", 9, "bold" if active else "normal"),
                cursor="hand2",
            )
            tab.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            if active:
                close_button = tk.Button(
                    tab_frame,
                    text="×",
                    command=self.close_active_tab,
                    bg=bg_col,
                    fg=THEME["fg_muted"],
                    activebackground=THEME["btn_danger"],
                    activeforeground="#ffffff",
                    relief=tk.FLAT,
                    bd=0,
                    padx=6,
                    font=("Ubuntu", 9, "bold"),
                    cursor="hand2",
                )
                close_button.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 2))
                self._add_hover(close_button, bg_col, THEME["btn_danger"])

        if self.notes:
            active_note = self.notes[self.active_note_index]
            document_title = active_note["title"]
            if not document_title.lower().endswith(".txt"):
                document_title += ".txt"
            self.document_title_var.set(document_title)
            self.document_path_var.set(f"/notes/{active_note['file']}")

    def sync_active_note(self):
        if self.notes:
            content = self.note_text.get("1.0", "end-1c")
            self.notes[self.active_note_index]["content"] = content
            first_line = next(
                (line.strip() for line in content.splitlines() if line.strip()),
                "",
            )
            if first_line and not self.notes[self.active_note_index].get("custom_title"):
                title = first_line[:20] + ("…" if len(first_line) > 20 else "")
                self.notes[self.active_note_index]["title"] = title
                self.note_title_var.set(title)

    def rename_note(self, _event=None):
        title = self.note_title_var.get().strip()
        if not title:
            self.note_title_var.set(self.notes[self.active_note_index]["title"])
            return "break"

        if title.lower().endswith(".txt"):
            title = title[:-4].rstrip()
        title = title[:60] or self.notes[self.active_note_index]["title"]
        active_note = self.notes[self.active_note_index]
        if title != active_note["title"]:
            active_note["title"] = title
            active_note["custom_title"] = True
            self.note_title_var.set(title)
            self.save_note()
        return "break"

    def toggle_pin(self):
        active_note = self.notes[self.active_note_index]
        active_note["pinned"] = not active_note.get("pinned", False)
        self.pin_button.configure(text="★" if active_note["pinned"] else "☆")
        self.save_note()

    def duplicate_note(self, _event=None):
        self.sync_active_note()
        source_note = self.notes[self.active_note_index]
        self.notes.append(
            {
                "title": f"{source_note['title']} copy"[:60],
                "content": source_note["content"],
                "file": self.next_note_filename(),
                "custom_title": True,
                "pinned": False,
            }
        )
        self.active_note_index = len(self.notes) - 1
        self.show_active_note()
        self.render_tabs()
        self.save_note()
        self.note_text.focus_set()
        return "break"

    def insert_checklist(self, _event=None):
        line_start = self.note_text.index("insert linestart")
        self.note_text.insert(line_start, "- [ ] ")
        self.note_text.edit_modified(True)
        self.note_changed()
        self.note_text.focus_set()
        return "break"

    def insert_timestamp(self):
        timestamp = datetime.now().strftime("%b %d, %Y  %I:%M %p")
        self.note_text.insert(tk.INSERT, timestamp)
        self.note_text.edit_modified(True)
        self.note_changed()
        self.note_text.focus_set()

    def focus_search(self, _event=None):
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, tk.END)
        return "break"

    def clear_search(self, _event=None):
        self.search_var.set("")
        self.find_in_note()
        self.note_text.focus_set()
        return "break"

    def find_in_note(self, _event=None):
        self.note_text.tag_remove("search_match", "1.0", tk.END)
        self.note_text.tag_remove("search_current", "1.0", tk.END)
        query = self.search_var.get()
        if not query:
            self.search_status_var.set("")
            return

        matches = []
        start = "1.0"
        while True:
            match = self.note_text.search(
                query,
                start,
                stopindex=tk.END,
                nocase=True,
            )
            if not match:
                break
            end = f"{match}+{len(query)}c"
            matches.append((match, end))
            self.note_text.tag_add("search_match", match, end)
            start = end

        if matches:
            first_match, first_end = matches[0]
            self.note_text.tag_add("search_current", first_match, first_end)
            self.note_text.see(first_match)
        self.search_status_var.set(str(len(matches)))

    def new_tab(self):
        self.sync_active_note()
        next_number = 1
        existing_titles = {note["title"] for note in self.notes}
        while f"Note {next_number}" in existing_titles:
            next_number += 1
        self.notes.append(
            {
                "title": f"Note {next_number}",
                "content": "",
                "file": self.next_note_filename(),
                "custom_title": False,
                "pinned": False,
            }
        )
        self.active_note_index = len(self.notes) - 1
        self.show_active_note()
        self.render_tabs()
        self.save_note()
        self.note_text.focus_set()

    def switch_tab(self, selected_index):
        if selected_index == self.active_note_index:
            return
        self.sync_active_note()
        self.active_note_index = selected_index
        self.show_active_note()
        self.render_tabs()
        self.save_note()
        self.note_text.focus_set()

    def select_next_tab(self, _event=None):
        if len(self.notes) > 1:
            self.switch_tab((self.active_note_index + 1) % len(self.notes))
        return "break"

    def close_active_tab(self):
        active_note = self.notes[self.active_note_index]
        self.sync_active_note()
        if active_note["content"].strip() and not messagebox.askyesno(
            "Close note",
            f'Close "{active_note["title"]}"? This note will be deleted.',
        ):
            return

        del self.notes[self.active_note_index]
        if not self.notes:
            self.notes = [
                {
                    "title": "Note 1",
                    "content": "",
                    "file": self.next_note_filename(),
                    "custom_title": False,
                    "pinned": False,
                }
            ]
        self.active_note_index = min(self.active_note_index, len(self.notes) - 1)
        self.show_active_note()
        self.render_tabs()
        self.save_note()

    def note_changed(self, _event=None):
        if self.loading_note or not self.note_text.edit_modified():
            return

        self.note_text.edit_modified(False)
        self.sync_active_note()
        self.save_status_var.set("Saving…")
        self.update_note_count()
        if self.save_job is not None:
            self.after_cancel(self.save_job)
        self.save_job = self.after(600, self.save_note)

    def update_note_count(self):
        content = self.note_text.get("1.0", "end-1c")
        word_count = len(content.split())
        character_count = len(content)
        word_label = "word" if word_count == 1 else "words"
        character_label = "character" if character_count == 1 else "characters"
        self.note_count_var.set(
            f"{word_count} {word_label}  •  {character_count} {character_label}"
        )

    def save_note(self, _event=None):
        if self.save_job is not None:
            self.after_cancel(self.save_job)
            self.save_job = None

        try:
            self.sync_active_note()
            NOTES_DIR.mkdir(exist_ok=True)
            for note in self.notes:
                filename = note.get("file")
                if not valid_note_filename(filename):
                    filename = self.next_note_filename()
                    note["file"] = filename
                (NOTES_DIR / filename).write_text(
                    note["content"],
                    encoding="utf-8",
                )
            note_data = {
                "active_note_index": self.active_note_index,
                "notes": self.notes,
            }
            NOTES_DATA_FILE.write_text(
                json.dumps(note_data, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            self.save_status_var.set("Save failed")
            messagebox.showerror("Cannot save note", str(exc))
            return "break"

        self.save_status_var.set("Saved")
        self.render_tabs()
        return "break"

    @staticmethod
    def create_tray_image():
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 6, 56, 58), radius=14, fill="#6366F1")
        draw.line((19, 22, 45, 22), fill="#FFFFFF", width=4)
        draw.line((19, 32, 45, 32), fill="#FFFFFF", width=4)
        draw.line((19, 42, 37, 42), fill="#FFFFFF", width=4)
        return image

    def start_tray_icon(self):
        menu = pystray.Menu(
            pystray.MenuItem(
                "Open Sticky Notes",
                lambda _i, _item: self.tray_commands.put("show"),
                default=True,
            ),
            pystray.MenuItem(
                "Quit Application",
                lambda _i, _item: self.tray_commands.put("quit"),
            ),
        )
        self.tray_icon = pystray.Icon(
            "my-sticky-notes",
            self.create_tray_image(),
            "Sticky Notes",
            menu,
        )
        threading.Thread(
            target=self.tray_icon.run,
            name="sticky-notes-tray",
            daemon=True,
        ).start()

    def process_tray_commands(self):
        try:
            while True:
                command = self.tray_commands.get_nowait()
                if command == "show":
                    self.show_window()
                elif command == "quit":
                    self.quit_application()
                    return
        except queue.Empty:
            pass

        if not self.quitting:
            self.after(200, self.process_tray_commands)

    def window_unmapped(self, _event=None):
        if self.state() == "iconic":
            self.after(50, self.hide_to_tray)

    def hide_to_tray(self):
        self.save_note()
        self.withdraw()

    def show_window(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()
        self.note_text.focus_set()

    def quit_application(self):
        self.quitting = True
        self.save_note()
        if self.tray_icon is not None:
            self.tray_icon.stop()
        self.destroy()

    def run_command(self, command, error_title, env=None):
        try:
            result = subprocess.run(
                command,
                cwd=APP_DIR,
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            messagebox.showerror(error_title, str(exc))
            return False

        if result.returncode != 0:
            output = (result.stderr or result.stdout or "Command failed.").strip()
            messagebox.showerror(error_title, output)
            return False

        return True

    def start_worker(self):
        self.run_command([str(START_SCRIPT)], "Start failed")
        self.refresh_status()

    def start_or_pause(self):
        if app_is_running():
            self.toggle_pause()
        else:
            self.start_worker()

    def stop_worker(self):
        self.run_command([str(STOP_SCRIPT)], "Stop failed")
        self.refresh_status()

    def toggle_pause(self):
        if not app_is_running():
            messagebox.showinfo("Auto Mouse Move", "Not running.")
            self.refresh_status()
            return

        pause_key_code = load_shortcut_config()["pause_key_codes"][0]

        success = self.run_command(
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
            "Pause/resume failed",
            env={**os.environ, "YDOTOOL_SOCKET": YDOTOOL_SOCKET},
        )
        if success:
            self.after(800, self.refresh_status)

    @staticmethod
    def latest_activity(log_text):
        lines = [line.strip() for line in log_text.splitlines() if line.strip()]
        if not lines:
            return "Ready"
        latest = lines[-1]
        return latest[:90] + ("…" if len(latest) > 90 else "")

    def refresh_status(self):
        if self.status_job is not None:
            self.after_cancel(self.status_job)

        running = app_is_running()
        log_text = read_log_tail()
        paused = running and last_pause_state(log_text)
        self.activity_var.set(self.latest_activity(log_text))

        # Discrete status indicator
        if paused:
            status = "Paused"
            fg_color = THEME["status_paused"]
        elif running:
            status = "Running"
            fg_color = THEME["status_running"]
        else:
            status = "Stopped"
            fg_color = THEME["fg_muted"]

        self.status_var.set(status)
        self.status_label.configure(
            fg=fg_color,
            text=f"● {status}",
        )
        self.panel_status_label.configure(fg=fg_color)

        if paused:
            self.start_pause_button.configure(
                text="▶ Resume",
                bg=THEME["btn_muted_bg"],
            )
            self._add_hover(
                self.start_pause_button,
                THEME["btn_muted_bg"],
                THEME["btn_muted_hover"],
            )
        elif running:
            self.start_pause_button.configure(
                text="Ⅱ Pause",
                bg=THEME["btn_muted_bg"],
            )
            self._add_hover(
                self.start_pause_button,
                THEME["btn_muted_bg"],
                THEME["btn_muted_hover"],
            )
        else:
            self.start_pause_button.configure(
                text="▶ Start",
                bg=THEME["btn_muted_bg"],
            )
            self._add_hover(
                self.start_pause_button,
                THEME["btn_muted_bg"],
                THEME["btn_muted_hover"],
            )

        self.stop_button.configure(state=tk.NORMAL if running else tk.DISABLED)
        self.status_job = self.after(1500, self.refresh_status)


if __name__ == "__main__":
    AutoMouseGui().mainloop()
