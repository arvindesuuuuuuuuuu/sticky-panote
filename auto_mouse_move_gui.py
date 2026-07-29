import base64
import io
import json
import os
import queue
import subprocess
import threading
import tkinter as tk
import tkinter.font as tkfont
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

# Design System Palette
THEME = {
    "bg_dark": "#18181a",
    "bg_panel": "#202024",
    "bg_surface": "#28282d",
    "bg_surface_hover": "#323238",
    "border": "#36363d",
    "accent_primary": "#e2b852",
    "accent_hover": "#f3cb65",
    "fg_main": "#e6e6e8",
    "fg_muted": "#96969e",
    "status_running": "#4caf50",
    "status_running_bg": "#1e3322",
    "status_paused": "#ff9800",
    "status_paused_bg": "#382e1b",
    "status_stopped": "#f44336",
    "status_stopped_bg": "#362121",
    "btn_danger": "#b8423c",
    "btn_danger_hover": "#cb4e48",
    "btn_success": "#3a8453",
    "btn_success_hover": "#469b63",
}


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


def save_shortcut_config(pause_key_codes, stop_key_code):
    config = {
        "pause_key_codes": sorted(set(pause_key_codes)),
        "stop_key_code": stop_key_code,
    }
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


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


class AutoMouseGui(tk.Tk):
    def __init__(self):
        super().__init__(className="MyStickyNotes")

        self.title("My Sticky Notes")
        self.geometry("820x580")
        self.minsize(520, 360)
        self.configure(bg=THEME["bg_dark"])
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
        self.editor_font_size = 10
        self.editor_font = tkfont.Font(family="Consolas", size=self.editor_font_size)
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
        self.bind("<Unmap>", self.window_unmapped)
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        self.save_note()
        self.start_tray_icon()
        self.after(200, self.process_tray_commands)
        self.refresh_status()

    def _build_layout(self):
        # --- Top Header ---
        self.header = tk.Frame(self, bg=THEME["bg_panel"], padx=18, pady=12)
        self.header.pack(fill=tk.X)

        heading = tk.Frame(self.header, bg=THEME["bg_panel"])
        heading.pack(side=tk.LEFT, fill=tk.Y)

        title = tk.Label(
            heading,
            text="Sticky Notes",
            bg=THEME["bg_panel"],
            fg=THEME["fg_main"],
            font=("Segoe UI", 13, "bold"),
        )
        title.pack(anchor=tk.W)

        subtitle = tk.Label(
            heading,
            text="Capture ideas  •  Keep moving",
            bg=THEME["bg_panel"],
            fg=THEME["fg_muted"],
            font=("Segoe UI", 8),
        )
        subtitle.pack(anchor=tk.W, pady=(1, 0))

        self.menu_button = tk.Button(
            self.header,
            text="⚙",
            command=self.toggle_control_panel,
            bg=THEME["bg_surface"],
            fg=THEME["fg_main"],
            activebackground=THEME["bg_surface_hover"],
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )
        self.menu_button.pack(side=tk.RIGHT)
        self._add_hover(self.menu_button, THEME["bg_surface"], THEME["bg_surface_hover"])

        self.status_label = tk.Label(
            self.header,
            text="●  Stopped",
            bg=THEME["status_stopped_bg"],
            fg=THEME["status_stopped"],
            padx=12,
            pady=5,
            font=("Segoe UI", 9, "bold"),
        )
        self.status_label.pack(side=tk.RIGHT, padx=(0, 10))

        # --- Tab Navigation Bar ---
        self.tab_bar = tk.Frame(self, bg=THEME["bg_dark"], padx=14, pady=6)
        self.tab_bar.pack(fill=tk.X)

        self.tabs_container = tk.Frame(self.tab_bar, bg=THEME["bg_dark"])
        self.tabs_container.pack(side=tk.LEFT, fill=tk.X, expand=True)

        close_tab_button = tk.Button(
            self.tab_bar,
            text="✕",
            command=self.close_active_tab,
            bg=THEME["bg_surface"],
            fg=THEME["fg_muted"],
            activebackground=THEME["btn_danger"],
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=4,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )
        close_tab_button.pack(side=tk.RIGHT, padx=(6, 0))
        self._add_hover(close_tab_button, THEME["bg_surface"], THEME["btn_danger"])

        new_tab_button = tk.Button(
            self.tab_bar,
            text="+ New",
            command=self.new_tab,
            bg=THEME["bg_surface"],
            fg=THEME["fg_main"],
            activebackground=THEME["bg_surface_hover"],
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=4,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )
        new_tab_button.pack(side=tk.RIGHT)
        self._add_hover(new_tab_button, THEME["bg_surface"], THEME["bg_surface_hover"])

        # --- Collapsible Controls Drawer ---
        self.control_panel = tk.Frame(
            self,
            bg=THEME["bg_surface"],
            padx=16,
            pady=12,
            highlightthickness=1,
            highlightbackground=THEME["border"],
        )

        controls_top = tk.Frame(self.control_panel, bg=THEME["bg_surface"])
        controls_top.pack(fill=tk.X)

        self.start_pause_button = tk.Button(
            controls_top,
            text="▶  Start",
            command=self.start_or_pause,
            bg=THEME["btn_success"],
            fg="#ffffff",
            activebackground=THEME["btn_success_hover"],
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=14,
            pady=7,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )
        self.start_pause_button.pack(side=tk.LEFT, padx=(0, 8))

        self.stop_button = tk.Button(
            controls_top,
            text="■  Stop",
            command=self.stop_worker,
            bg=THEME["btn_danger"],
            fg="#ffffff",
            activebackground=THEME["btn_danger_hover"],
            activeforeground="#ffffff",
            disabledforeground="#6d6d75",
            relief=tk.FLAT,
            bd=0,
            padx=14,
            pady=7,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )
        self.stop_button.pack(side=tk.LEFT)

        panel_hint = tk.Label(
            controls_top,
            text="Logs & Automation Control",
            bg=THEME["bg_surface"],
            fg=THEME["fg_muted"],
            font=("Segoe UI", 8),
        )
        panel_hint.pack(side=tk.RIGHT)

        # Log Monitor Box inside drawer
        self.log_text = tk.Text(
            self.control_panel,
            height=4,
            bg=THEME["bg_dark"],
            fg=THEME["fg_muted"],
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=6,
            font=("Consolas", 8),
            wrap=tk.WORD,
        )
        self.log_text.pack(fill=tk.X, pady=(10, 0))

        # --- Editor Body ---
        body = tk.Frame(self, bg=THEME["bg_dark"], padx=14, pady=8)
        body.pack(fill=tk.BOTH, expand=True)

        editor_frame = tk.Frame(
            body,
            bg=THEME["bg_panel"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
        )
        editor_frame.pack(fill=tk.BOTH, expand=True)

        self.note_text = tk.Text(
            editor_frame,
            bg=THEME["bg_panel"],
            fg=THEME["fg_main"],
            insertbackground=THEME["accent_primary"],
            selectbackground="#3b3b45",
            selectforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=18,
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

        scrollbar = tk.Scrollbar(
            editor_frame,
            command=self.note_text.yview,
            bg=THEME["bg_surface"],
            troughcolor=THEME["bg_panel"],
            activebackground=THEME["bg_surface_hover"],
            relief=tk.FLAT,
            bd=0,
            width=8,
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.note_text.configure(yscrollcommand=scrollbar.set)

        # --- Bottom Status Footer ---
        footer = tk.Frame(self, bg=THEME["bg_panel"], padx=16, pady=6)
        footer.pack(fill=tk.X)

        save_status = tk.Label(
            footer,
            textvariable=self.save_status_var,
            bg=THEME["bg_panel"],
            fg=THEME["fg_muted"],
            font=("Segoe UI", 8),
        )
        save_status.pack(side=tk.LEFT)

        count_label = tk.Label(
            footer,
            textvariable=self.note_count_var,
            bg=THEME["bg_panel"],
            fg=THEME["fg_muted"],
            font=("Segoe UI", 8),
        )
        count_label.pack(side=tk.RIGHT)

        zoom_label = tk.Label(
            footer,
            textvariable=self.zoom_var,
            bg=THEME["bg_panel"],
            fg=THEME["fg_muted"],
            font=("Segoe UI", 8),
        )
        zoom_label.pack(side=tk.RIGHT, padx=(0, 14))
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
        self.set_editor_zoom(10)
        return "break"

    def set_editor_zoom(self, font_size):
        self.editor_font_size = min(28, max(7, font_size))
        self.editor_font.configure(size=self.editor_font_size)
        self.zoom_var.set(f"{round(self.editor_font_size / 10 * 100)}%")

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
            self.menu_button.configure(text="⚙  Controls", bg=THEME["bg_surface"])
        else:
            self.control_panel.pack(fill=tk.X, after=self.tab_bar)
            self.menu_button.configure(
                text="✕  Close controls", bg=THEME["bg_surface_hover"]
            )
        self.control_panel_visible = not self.control_panel_visible

    def load_notes(self):
        try:
            saved_notes = json.loads(NOTES_DATA_FILE.read_text(encoding="utf-8"))
            notes = saved_notes.get("notes", [])
            active_index = int(saved_notes.get("active_note_index", 0))
            if not isinstance(notes, list) or not notes:
                raise ValueError
            self.notes = [
                {
                    "title": str(note.get("title", f"Note {index + 1}")),
                    "content": str(note.get("content", "")),
                }
                for index, note in enumerate(notes)
                if isinstance(note, dict)
            ]
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
            self.notes = [{"title": "Note 1", "content": existing_note}]
            self.active_note_index = 0

        self.show_active_note()

    def show_active_note(self):
        self.loading_note = True
        self.note_text.delete("1.0", tk.END)
        self.note_text.insert("1.0", self.notes[self.active_note_index]["content"])
        self.note_text.edit_modified(False)
        self.loading_note = False
        self.update_note_count()

    def render_tabs(self):
        for child in self.tabs_container.winfo_children():
            child.destroy()

        for index, note in enumerate(self.notes):
            active = index == self.active_note_index
            bg_col = THEME["bg_surface"] if active else THEME["bg_dark"]
            fg_col = THEME["accent_primary"] if active else THEME["fg_muted"]

            tab = tk.Button(
                self.tabs_container,
                text=note["title"],
                command=lambda selected=index: self.switch_tab(selected),
                bg=bg_col,
                fg=fg_col,
                activebackground=THEME["bg_surface_hover"],
                activeforeground="#ffffff",
                relief=tk.FLAT,
                bd=0,
                padx=12,
                pady=5,
                font=("Segoe UI", 8, "bold" if active else "normal"),
                cursor="hand2",
            )
            tab.pack(side=tk.LEFT, padx=(0, 4))

    def sync_active_note(self):
        if self.notes:
            content = self.note_text.get("1.0", "end-1c")
            self.notes[self.active_note_index]["content"] = content
            first_line = next(
                (line.strip() for line in content.splitlines() if line.strip()),
                "",
            )
            if first_line:
                title = first_line[:20] + ("…" if len(first_line) > 20 else "")
                self.notes[self.active_note_index]["title"] = title

    def new_tab(self):
        self.sync_active_note()
        next_number = 1
        existing_titles = {note["title"] for note in self.notes}
        while f"Note {next_number}" in existing_titles:
            next_number += 1
        self.notes.append({"title": f"Note {next_number}", "content": ""})
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
            self.notes = [{"title": "Note 1", "content": ""}]
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
        draw.rounded_rectangle((8, 6, 56, 58), radius=8, fill="#e2b852")
        draw.polygon(((43, 58), (56, 45), (56, 58)), fill="#c29a38")
        draw.line((18, 22, 46, 22), fill="#423412", width=4)
        draw.line((18, 32, 46, 32), fill="#423412", width=4)
        draw.line((18, 42, 38, 42), fill="#423412", width=4)
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

    def refresh_status(self):
        if self.status_job is not None:
            self.after_cancel(self.status_job)

        running = app_is_running()
        log_text = read_log_tail()
        paused = running and last_pause_state(log_text)

        # Update log viewer
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert("1.0", log_text or "No active log outputs.")
        self.log_text.see(tk.END)

        if paused:
            status = "Paused"
            fg_color = THEME["status_paused"]
            bg_color = THEME["status_paused_bg"]
        elif running:
            status = "Running"
            fg_color = THEME["status_running"]
            bg_color = THEME["status_running_bg"]
        else:
            status = "Stopped"
            fg_color = THEME["status_stopped"]
            bg_color = THEME["status_stopped_bg"]

        self.status_var.set(status)
        self.status_label.configure(
            text=f"●  {status}",
            foreground=fg_color,
            background=bg_color,
        )

        if paused:
            self.start_pause_button.configure(
                text="▶  Resume", bg=THEME["accent_primary"]
            )
            self._add_hover(
                self.start_pause_button,
                THEME["accent_primary"],
                THEME["accent_hover"],
            )
        elif running:
            self.start_pause_button.configure(
                text="Ⅱ  Pause", bg=THEME["accent_primary"]
            )
            self._add_hover(
                self.start_pause_button,
                THEME["accent_primary"],
                THEME["accent_hover"],
            )
        else:
            self.start_pause_button.configure(
                text="▶  Start", bg=THEME["btn_success"]
            )
            self._add_hover(
                self.start_pause_button,
                THEME["btn_success"],
                THEME["btn_success_hover"],
            )

        self.stop_button.configure(state=tk.NORMAL if running else tk.DISABLED)
        self.status_job = self.after(1500, self.refresh_status)


if __name__ == "__main__":
    AutoMouseGui().mainloop()
