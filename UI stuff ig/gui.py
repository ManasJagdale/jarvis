"""
gui.py

Desktop chat window for Jarvis, built with CustomTkinter (a themed layer
over Tkinter -- rounded corners, a proper dark palette, smoother widgets
-- while staying pure Python, no browser/webview involved). This is the
entry point you'll actually double-click day to day; main.py (the
terminal version) still works and shares the exact same agent loop via
jarvis_core.py.

What changed from the plain-Tkinter version:
  - CustomTkinter widgets throughout (CTk*) instead of raw tk widgets,
    for the rounded/dark aesthetic.
  - Chat messages render as individual bubble frames (one per message,
    left/right aligned, color-coded by role) instead of one scrolling
    Text widget.
  - An ambient "presence" orb (see orb.py) sits at the top -- a
    Pillow-rendered glowing sphere, gently breathing in size/brightness
    on a slow loop. It's a lightweight visual touch, not a reactive
    status indicator (see orb.py's docstring for why).
  - Confirmation prompts use a CTkToplevel styled to match, but the
    actual safety behaviour is unchanged: Cancel is the keyboard default
    (Enter and Escape both cancel), only a deliberate click on the red
    "Yes, do it" button confirms.

Everything about the threading model is unchanged from the original:
    Every LLM call (and some tool calls, like run_script) can take a
    couple of seconds. Tkinter has ONE event loop on the main thread --
    if we called the LLM there, the whole window would freeze and stop
    redrawing until the call finished. So each turn runs on a worker
    thread, and that thread hands results back to the main thread via
    `root.after(0, ...)`, which is the standard thread-safe way to touch
    Tkinter widgets from another thread (you never touch a widget
    directly from a worker thread -- you schedule the touch on the main
    thread and let it happen there).

    execute_tool() in jarvis_core.py calls confirm_fn(description) and
    expects a plain True/False answer *right then*, synchronously -- it
    doesn't know or care that a UI is involved. So
    self._confirm_action_gui() below schedules the popup on the main
    thread, then blocks the worker thread on a threading.Event until the
    user clicks a button. The main thread is free the whole time (it's
    just running its own modal dialog), only the worker thread waits.
"""

import os
import sys
import threading
import traceback
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
from PIL import ImageTk

from orb import FRAME_DELAY_MS, generate_orb_frames

# gui.py and orb.py live one folder down from the rest of the project
# (in a UI-only subfolder), so Python's default "look next to this
# script" import rule doesn't reach jarvis_core.py / gemini_client.py /
# etc. -- those still live at the project root. Add the parent folder to
# sys.path so `from jarvis_core import run_turn` (below, in main())
# resolves correctly regardless of which folder gui.py is launched from.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

MAX_TOOL_RESULT_CHARS = 400

# Written next to this file if startup fails. pythonw.exe (used by
# run_jarvis.bat so no console window appears) has no console to print
# tracebacks to, so without this, a startup failure looks like "nothing
# happens" with zero clues why. This log is the first place to look.
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_gui_error.log")

# ---------------------------------------------------------------------------
# Palette -- deliberately close to the orb's own colors so the window
# feels like one designed surface rather than a themed sphere dropped
# onto a generic dark-mode app.
# ---------------------------------------------------------------------------
BG = "#0b0d12"
PANEL = "#12151c"
BUBBLE_USER = "#3a5ce0"
BUBBLE_JARVIS = "#1a1d24"
BUBBLE_TOOL = "#161920"
TEXT_PRIMARY = "#e8e9ec"
TEXT_MUTED = "#8a8d97"
ACCENT = "#7f9fff"
DANGER = "#c0392b"
DANGER_HOVER = "#a93226"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


def _show_fatal_error(title: str, message: str):
    """Last-resort error display. Logs to disk AND tries a Tk popup, so it
    works even if the failure happened before the main window could open."""
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n--- {title} ---\n{message}\n")
    except OSError:
        pass
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        pass


class ConfirmDialog:
    """
    Modal Yes/No popup for destructive actions.

    Cancel is the safe default on purpose: it holds keyboard focus, and
    both Enter and Escape trigger it. You must deliberately click
    "Yes, do it" for a destructive action to proceed -- a stray keypress
    can never confirm one. This preserves the intent of the old CLI's
    "type the word yes" gate in button form.
    """

    def __init__(self, parent: ctk.CTk, description: str):
        self.result = False

        self.top = ctk.CTkToplevel(parent)
        self.top.title("Jarvis needs confirmation")
        self.top.configure(fg_color=PANEL)
        self.top.transient(parent)
        self.top.resizable(False, False)
        self.top.grab_set()

        ctk.CTkLabel(
            self.top,
            text="\u26a0  Jarvis wants to do the following:",
            font=("Segoe UI", 13, "bold"),
            text_color="#f0c419",
            anchor="w",
            justify="left",
        ).pack(padx=20, pady=(20, 8), anchor="w")

        ctk.CTkLabel(
            self.top,
            text=description,
            font=("Consolas", 11),
            text_color=TEXT_PRIMARY,
            justify="left",
            anchor="w",
            wraplength=440,
        ).pack(padx=20, pady=(0, 20), anchor="w")

        btn_frame = ctk.CTkFrame(self.top, fg_color="transparent")
        btn_frame.pack(padx=20, pady=(0, 20), fill="x")

        yes_btn = ctk.CTkButton(
            btn_frame,
            text="Yes, do it",
            width=110,
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
            command=self._confirm,
        )
        yes_btn.pack(side="right")

        cancel_btn = ctk.CTkButton(
            btn_frame,
            text="Cancel",
            width=110,
            fg_color="transparent",
            border_width=1,
            border_color=TEXT_MUTED,
            hover_color=PANEL,
            command=self._cancel,
        )
        cancel_btn.pack(side="right", padx=(0, 8))

        cancel_btn.focus_set()
        self.top.bind("<Return>", lambda _e: self._cancel())
        self.top.bind("<Escape>", lambda _e: self._cancel())
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)

        self.top.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.top.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.top.winfo_height()) // 2
        self.top.geometry(f"+{max(x, 0)}+{max(y, 0)}")

        self.top.wait_window()

    def _confirm(self):
        self.result = True
        self.top.destroy()

    def _cancel(self):
        self.result = False
        self.top.destroy()


class MessageBubble(ctk.CTkFrame):
    """One chat message: a rounded, color-coded, left/right aligned bubble."""

    ROLE_STYLE = {
        "user": dict(fg=BUBBLE_USER, text=TEXT_PRIMARY, anchor="e", side="right"),
        "jarvis": dict(fg=BUBBLE_JARVIS, text=TEXT_PRIMARY, anchor="w", side="left"),
        "tool": dict(fg=BUBBLE_TOOL, text=ACCENT, anchor="w", side="left"),
        "error": dict(fg="#3a1a1a", text="#ff8a8a", anchor="w", side="left"),
        "system": dict(fg="transparent", text=TEXT_MUTED, anchor="w", side="left"),
    }

    def __init__(self, parent, role: str, text: str):
        style = self.ROLE_STYLE.get(role, self.ROLE_STYLE["jarvis"])
        super().__init__(parent, fg_color="transparent")

        bubble = ctk.CTkFrame(self, fg_color=style["fg"], corner_radius=14)
        bubble.pack(anchor=style["anchor"])

        font = ("Consolas", 10) if role == "tool" else ("Segoe UI", 12)
        label = ctk.CTkLabel(
            bubble,
            text=text,
            text_color=style["text"],
            font=font,
            justify="left",
            anchor="w",
            wraplength=460,
        )
        label.pack(padx=12, pady=8)

        self.pack(fill="x", padx=16, pady=4, anchor=style["anchor"])


class JarvisApp:
    def __init__(self, root: ctk.CTk, gemini_client_cls, run_turn_fn):
        self.root = root
        self.root.title("Jarvis")
        self.root.geometry("820x640")
        self.root.minsize(520, 420)
        self.root.configure(fg_color=BG)

        self._run_turn = run_turn_fn
        self.messages: list[dict] = []
        self.busy = False

        self._build_ui()
        self._start_orb_animation()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        try:
            self.llm = gemini_client_cls()
        except Exception as exc:  # noqa: BLE001 - e.g. missing API key
            messagebox.showerror("Jarvis \u2014 startup error", str(exc))
            self.root.destroy()
            return

        self._append_system("Jarvis is ready. Ask it to do something.")
        self.entry.focus_set()

    # ---------- UI construction ----------

    def _build_ui(self):
        # -- Header: orb + title --
        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(12, 0))

        self.orb_canvas = tk.Canvas(
            header, width=64, height=64, bg=BG, highlightthickness=0
        )
        self.orb_canvas.pack(side="left", padx=(12, 10))

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", anchor="w")
        ctk.CTkLabel(
            title_box, text="Jarvis", font=("Segoe UI", 18, "bold"), text_color=TEXT_PRIMARY
        ).pack(anchor="w")
        self.status_label = ctk.CTkLabel(
            title_box, text="idle", font=("Segoe UI", 11), text_color=TEXT_MUTED
        )
        self.status_label.pack(anchor="w")

        # -- Chat area: scrollable column of bubbles --
        self.chat_frame = ctk.CTkScrollableFrame(
            self.root, fg_color=BG, scrollbar_button_color=PANEL,
            scrollbar_button_hover_color="#22262f",
        )
        self.chat_frame.pack(fill="both", expand=True, padx=8, pady=(10, 4))

        # -- Input row --
        bottom = ctk.CTkFrame(self.root, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(0, 16))

        self.entry = ctk.CTkEntry(
            bottom,
            placeholder_text="Ask Jarvis to do something...",
            fg_color=PANEL,
            border_width=0,
            corner_radius=20,
            height=40,
            font=("Segoe UI", 12),
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=2)
        self.entry.bind("<Return>", lambda _e: self._on_send())

        self.send_btn = ctk.CTkButton(
            bottom, text="Send", width=80, height=40, corner_radius=20,
            fg_color=BUBBLE_USER, hover_color="#3350c4", command=self._on_send,
        )
        self.send_btn.pack(side="left")

        # Hidden until a turn actually fails -- see _run_turn_worker's
        # except block and _on_retry.
        self.retry_btn = ctk.CTkButton(
            bottom, text="Retry", width=70, height=40, corner_radius=20,
            fg_color="#8a4b1f", hover_color="#753e17", state="disabled",
            command=self._on_retry,
        )
        self.retry_btn.pack(side="left", padx=(8, 0))

    # ---------- Orb animation ----------

    def _start_orb_animation(self):
        """
        Pre-render every breathing-loop frame once (see orb.py), then
        cycle through them on a timer. Cheap: no drawing work happens
        inside the loop itself, just swapping which pre-made image is
        shown.
        """
        pil_frames = generate_orb_frames()
        self._orb_frames = [ImageTk.PhotoImage(f) for f in pil_frames]
        self._orb_index = 0
        self._orb_image_id = self.orb_canvas.create_image(
            32, 32, image=self._orb_frames[0]
        )
        self._tick_orb()

    def _tick_orb(self):
        self._orb_index = (self._orb_index + 1) % len(self._orb_frames)
        self.orb_canvas.itemconfig(self._orb_image_id, image=self._orb_frames[self._orb_index])
        self.root.after(FRAME_DELAY_MS, self._tick_orb)

    # ---------- Chat log helpers ----------

    def _append(self, role: str, text: str):
        MessageBubble(self.chat_frame, role, text)
        self.chat_frame._parent_canvas.yview_moveto(1.0)

    def _append_system(self, text: str):
        self._append("system", text)

    def _set_busy(self, busy: bool, status_text: str = "idle"):
        self.busy = busy
        self.entry.configure(state="disabled" if busy else "normal")
        self.send_btn.configure(state="disabled" if busy else "normal")
        if busy:
            self.retry_btn.configure(state="disabled")
        self.status_label.configure(text=status_text)
        if not busy:
            self.entry.focus_set()

    # ---------- Sending a turn ----------

    def _on_send(self):
        if self.busy:
            return
        text = self.entry.get().strip()
        if not text:
            return

        self.entry.delete(0, "end")
        self._append("user", text)
        self.messages.append({"role": "user", "content": text})

        self.retry_btn.configure(state="disabled")
        self._set_busy(True, "thinking...")
        threading.Thread(target=self._run_turn_worker, daemon=True).start()

    def _run_turn_worker(self):
        """Runs on a background thread -- never touch Tkinter/CTk widgets directly here."""
        try:
            answer = self._run_turn(
                self.llm,
                self.messages,
                self._confirm_action_gui,
                on_tool_start=self._on_tool_start,
                on_tool_end=self._on_tool_end,
            )
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"\n--- turn failed ---\n{tb}\n")
            self.root.after(0, self._append, "error", f"Something went wrong: {exc}")
            self.root.after(0, self._set_busy, False, "error")
            self.root.after(0, lambda: self.retry_btn.configure(state="normal"))
            return

        self.root.after(0, self._append, "jarvis", answer)
        self.root.after(0, self._set_busy, False, "idle")

    def _on_retry(self):
        """
        Resend the exact same turn that just failed. self.messages already
        has the user's message (and any tool calls that succeeded before
        the crash) -- we deliberately don't touch it or ask for new input,
        just call the LLM again from where it left off.
        """
        if self.busy:
            return
        self._append_system("Retrying...")
        self.retry_btn.configure(state="disabled")
        self._set_busy(True, "retrying...")
        threading.Thread(target=self._run_turn_worker, daemon=True).start()

    # ---------- Tool-call feedback (called from the worker thread) ----------

    def _on_tool_start(self, name: str, args: dict):
        self.root.after(0, self._set_busy, True, f"running {name}...")
        self.root.after(0, self._append, "tool", f"\u2699 {name}({args})")

    def _on_tool_end(self, name: str, result: str):
        summary = result if len(result) <= MAX_TOOL_RESULT_CHARS else (
            result[:MAX_TOOL_RESULT_CHARS] + " ...[truncated]"
        )
        self.root.after(0, self._append, "tool", f"\u2192 {summary}")
        self.root.after(0, self._set_busy, True, "thinking...")

    # ---------- Confirmation (called from the worker thread, blocks it) ----------

    def _confirm_action_gui(self, description: str) -> bool:
        result_holder: dict = {}
        done = threading.Event()

        def show_dialog():
            dlg = ConfirmDialog(self.root, description)
            result_holder["confirmed"] = dlg.result
            done.set()

        self.root.after(0, show_dialog)
        done.wait()
        return result_holder.get("confirmed", False)

    def _on_close(self):
        if self.busy:
            if not messagebox.askyesno(
                "Jarvis is busy",
                "Jarvis is still working on something. Close anyway?",
            ):
                return
        self.root.destroy()


def main():
    # Imported here, not at the top of the file, so that if config.py (or
    # anything it pulls in) throws on import -- e.g. GEMINI_API_KEY isn't
    # set -- we land in the except block below instead of crashing before
    # a single line of GUI code has run.
    try:
        from providers import get_llm_client
        from jarvis_core import run_turn
    except Exception:
        _show_fatal_error(
            "Jarvis failed to start",
            "Jarvis hit an error before it could even open the window.\n\n"
            + traceback.format_exc(),
        )
        return

    root = ctk.CTk()
    try:
        JarvisApp(root, get_llm_client, run_turn)
    except Exception:
        _show_fatal_error("Jarvis failed to start", traceback.format_exc())
        return
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _show_fatal_error("Jarvis crashed", traceback.format_exc())
