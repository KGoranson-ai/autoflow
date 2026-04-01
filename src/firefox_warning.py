"""
Firefox warning dialog and preference persistence.
"""

from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import ttk


class FirefoxWarningDialog:
    """Show warning when Firefox is detected for Smart Fill."""

    def __init__(self):
        self.pref_file = os.path.expanduser(
            "~/Documents/Typestra/Settings/firefox_warning.json"
        )

    def should_show_warning(self):
        if not os.path.exists(self.pref_file):
            return True
        try:
            with open(self.pref_file, "r", encoding="utf-8") as f:
                prefs = json.load(f)
            return not prefs.get("hide_warning", False)
        except (OSError, json.JSONDecodeError):
            return True

    def save_preference(self, hide_warning):
        os.makedirs(os.path.dirname(self.pref_file), exist_ok=True)
        with open(self.pref_file, "w", encoding="utf-8") as f:
            json.dump({"hide_warning": hide_warning}, f, indent=2)

    def show_warning(self, parent=None):
        dialog = tk.Toplevel(parent)
        dialog.title("Firefox Detected")
        dialog.geometry("420x340")
        dialog.resizable(False, False)
        dialog.transient(parent)
        dialog.grab_set()

        title_frame = ttk.Frame(dialog)
        title_frame.pack(pady=20)
        ttk.Label(
            title_frame,
            text="⚠️  Firefox Detected",
            font=("Helvetica", 14, "bold"),
            foreground="#FF9500",
        ).pack()

        message_frame = ttk.Frame(dialog)
        message_frame.pack(pady=10, padx=30)
        ttk.Label(
            message_frame,
            text="Smart Fill's error detection may be\nunreliable in Firefox.",
            font=("Helvetica", 11),
            justify="center",
        ).pack()
        ttk.Label(
            message_frame,
            text="\nFirefox uses different automation APIs\nthat limit timeout detection accuracy.",
            font=("Helvetica", 9),
            foreground="gray",
            justify="center",
        ).pack(pady=(5, 0))

        ttk.Label(
            dialog,
            text="For best results, use:",
            font=("Helvetica", 11, "bold"),
        ).pack(pady=(15, 5))

        rec_frame = ttk.Frame(dialog)
        rec_frame.pack(pady=5)
        ttk.Label(rec_frame, text="• Safari (recommended)", font=("Helvetica", 10)).pack(
            anchor="w", padx=40
        )
        ttk.Label(rec_frame, text="• Google Chrome", font=("Helvetica", 10)).pack(
            anchor="w", padx=40
        )
        ttk.Label(rec_frame, text="• Brave Browser", font=("Helvetica", 10)).pack(
            anchor="w", padx=40
        )

        ttk.Label(dialog, text="Continue anyway?", font=("Helvetica", 11)).pack(pady=15)
        dont_show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            dialog, text="Don't show this again", variable=dont_show_var
        ).pack()

        result = {"continue": False}

        def cancel():
            result["continue"] = False
            dialog.destroy()

        def continue_anyway():
            result["continue"] = True
            if dont_show_var.get():
                self.save_preference(hide_warning=True)
            dialog.destroy()

        button_frame = ttk.Frame(dialog)
        button_frame.pack(pady=20)
        ttk.Button(button_frame, text="Cancel", command=cancel, width=15).pack(
            side="left", padx=5
        )
        ttk.Button(
            button_frame,
            text="Continue with Firefox",
            command=continue_anyway,
            width=22,
        ).pack(side="left", padx=5)

        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.wait_window()
        return result["continue"]
