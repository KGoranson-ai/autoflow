"""
Resume prompt for interrupted Smart Fill sessions.
"""

from __future__ import annotations

import json
import os
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Any, Dict, Optional


class ResumePrompt:
    """
    Show dialog on wake/startup if Smart Fill session was interrupted.
    """

    def __init__(self):
        self.recovery_file = os.path.expanduser(
            "~/Documents/Typestra/Recovery/session_state.json"
        )

    def check_for_interrupted_session(self) -> Optional[Dict[str, Any]]:
        # Note: Recovery file is deleted after showing prompt regardless
        # of user choice. This prevents repeated prompts on every startup.
        # If user discards, they can't resume later (intentional).
        if not os.path.exists(self.recovery_file):
            return None
        try:
            with open(self.recovery_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

        saved_time = datetime.fromisoformat(state["timestamp"])
        if (datetime.now() - saved_time).total_seconds() > 86400:
            self.delete_recovery_file()
            return None
        return state

    def show_resume_dialog(self, saved_state, parent=None):
        dialog = tk.Toplevel(parent)
        dialog.title("Interrupted Session")
        dialog.geometry("450x380")
        dialog.minsize(450, 380)
        dialog.resizable(False, False)
        dialog.transient(parent)
        dialog.grab_set()

        title_frame = ttk.Frame(dialog)
        title_frame.pack(pady=20)

        ttk.Label(
            title_frame,
            text="⚠️  Interrupted Session Detected",
            font=("Helvetica", 14, "bold"),
        ).pack()

        ttk.Label(
            dialog,
            text="Smart Fill was interrupted:",
            font=("Helvetica", 11),
        ).pack(pady=(0, 10))

        details_frame = ttk.Frame(dialog)
        details_frame.pack(pady=10)

        csv_filename = os.path.basename(saved_state.get("csv_file", "Unknown"))
        ttk.Label(
            details_frame,
            text=f"Batch: {csv_filename}",
            font=("Helvetica", 10),
        ).pack(anchor="w", padx=20)
        csv_full_path = saved_state.get("csv_file", "Unknown")
        home = os.path.expanduser("~")
        if csv_full_path.startswith(home):
            display_path = csv_full_path.replace(home, "~", 1)
        else:
            display_path = csv_full_path
        ttk.Label(
            details_frame,
            text=f"Source: {display_path}",
            font=("Helvetica", 9),
            foreground="gray",
        ).pack(anchor="w", padx=20)
        ttk.Label(
            details_frame,
            text=f"Progress: {saved_state['current_row']} / {saved_state['total_rows']} rows completed",
            font=("Helvetica", 10),
        ).pack(anchor="w", padx=20)

        saved_time = datetime.fromisoformat(saved_state["timestamp"])
        time_ago = self.format_time_ago(datetime.now() - saved_time)
        ttk.Label(
            details_frame,
            text=f"Last active: {time_ago}",
            font=("Helvetica", 10),
        ).pack(anchor="w", padx=20)

        ttk.Label(
            dialog,
            text="Would you like to resume where you left off?",
            font=("Helvetica", 11, "bold"),
        ).pack(pady=10)
        ttk.Label(
            dialog,
            text="(Discarding will permanently clear this recovery state)",
            font=("Helvetica", 9),
            foreground="gray",
        ).pack(pady=(0, 15))

        button_frame = ttk.Frame(dialog)
        button_frame.pack(pady=20)
        result = {"action": None}

        def discard():
            result["action"] = "discard"
            dialog.destroy()

        def resume():
            result["action"] = "resume"
            dialog.destroy()

        ttk.Button(
            button_frame, text="Discard & Start Fresh", command=discard, width=20
        ).pack(side="left", padx=5)
        ttk.Button(
            button_frame,
            text=f"Resume from Row {saved_state['current_row'] + 1}",
            command=resume,
            width=22,
        ).pack(side="left", padx=5)

        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.wait_window()
        return result["action"]

    def format_time_ago(self, timedelta):
        seconds = timedelta.total_seconds()
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        if seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = int(seconds / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"

    def delete_recovery_file(self):
        if os.path.exists(self.recovery_file):
            os.remove(self.recovery_file)
