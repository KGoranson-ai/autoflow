"""
Demo mode helpers for Smart Fill.
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd
import tkinter as tk
from tkinter import ttk


class DemoBanner:
    """Floating demo banner."""

    def __init__(self) -> None:
        self.window: Optional[tk.Toplevel] = None

    def show(self, parent: tk.Tk) -> None:
        if self.window and self.window.winfo_exists():
            return
        banner = tk.Toplevel(parent)
        banner.overrideredirect(True)
        banner.attributes("-topmost", True)
        banner.attributes("-alpha", 0.7)
        label = ttk.Label(
            banner,
            text="DEMO MODE - No Real Data Being Entered",
            background="#FF9500",
            foreground="white",
            font=("Helvetica", 14, "bold"),
            padding=(20, 10),
        )
        label.pack()
        banner.update_idletasks()
        screen_width = banner.winfo_screenwidth()
        width = 560
        banner.geometry(f"{width}x50+{(screen_width - width) // 2}+20")
        self.window = banner

    def hide(self) -> None:
        if self.window and self.window.winfo_exists():
            self.window.destroy()
        self.window = None


class DemoMode:
    """State and helpers for demo datasets."""

    def __init__(self) -> None:
        self.enabled = False
        self.demo_type = "candidates"
        self.demo_data = None
        self.banner = DemoBanner()

    def get_resource_path(self, relative_path: str) -> str:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(repo_root, "resources", relative_path)

    def load_demo_csv(self, demo_type: str):
        filename_map = {
            "candidates": "demo_candidates.csv",
            "crm": "demo_crm_contacts.csv",
            "invoices": "demo_invoices.csv",
        }
        if demo_type in filename_map:
            path = self.get_resource_path(f"demo_data/{filename_map[demo_type]}")
            return pd.read_csv(path)
        return pd.read_csv(demo_type)

    def enable(self, parent: tk.Tk, demo_type: str = "candidates") -> None:
        self.enabled = True
        self.demo_type = demo_type
        self.demo_data = self.load_demo_csv(demo_type)
        self.banner.show(parent)

    def disable(self) -> None:
        self.enabled = False
        self.banner.hide()

    def get_demo_speed(self) -> int:
        return 10

    def get_demo_variance(self) -> str:
        return "high"
