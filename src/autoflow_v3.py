"""
AutoFlow v3.0 - Typing & Spreadsheet Automation
Professional workflow automation with human-like typing patterns
Now with spreadsheet support for Excel and Google Sheets
"""

import argparse
import logging
import platform
import sys
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog, simpledialog
import pyautogui
import time
import random
import threading
import csv
import io
import json
import os
import re
import unicodedata
from datetime import datetime
from typing import List, Tuple

from typing_engine import TypingEngine, TypingConfig
from smart_fill import SmartFillSession
from error_detection import TimeoutDetector, CheckpointManager
from retry_manager import RetryManager, BatchHistory
from browser_context import BrowserContext, ContextVerifier
from demo_mode import DemoMode
from firefox_warning import FirefoxWarningDialog
from sleep_wake_detector import SleepWakeDetector
from resume_prompt import ResumePrompt

# Try to import pynput for global hotkeys
try:
    from pynput import keyboard
    PYNPUT_AVAILABLE = sys.version_info < (3, 13)
except ImportError:
    PYNPUT_AVAILABLE = False

# Try to import OCR dependencies
try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


# Max file size for OCR (10MB)
OCR_MAX_FILE_BYTES = 10 * 1024 * 1024

# Persistent user settings (~/.autoflow/settings.json)
AUTOFLOW_DIR = os.path.join(os.path.expanduser("~"), ".autoflow")
SETTINGS_PATH = os.path.join(AUTOFLOW_DIR, "settings.json")
SMART_FILL_SETTINGS_PATH = os.path.expanduser("~/Documents/Typestra/Settings/smart_fill.json")
logger = logging.getLogger(__name__)


class OCREngine:
    """Pro feature: Extract text from images using Tesseract OCR."""

    @staticmethod
    def get_supported_formats():
        """Return list of supported file extensions for OCR."""
        return [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".pdf"]

    @staticmethod
    def _cleanup_text(text: str) -> str:
        """Remove extra spaces and normalize unicode in extracted text."""
        if not text or not text.strip():
            return ""
        # Normalize line endings
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Collapse multiple spaces to one (but preserve newlines)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
        # Collapse multiple blank lines to one
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def extract_text(image_path: str) -> str:
        """
        Extract text from image using Tesseract.
        Supports image formats only; PDF returns a friendly error.
        """
        if not OCR_AVAILABLE:
            raise RuntimeError("OCR dependencies not installed. Install: pip install pytesseract Pillow")
        path_lower = image_path.lower()
        if path_lower.endswith(".pdf"):
            raise ValueError("PDF support coming soon. Please use an image file (.jpg, .png, .gif, .bmp).")
        if not os.path.isfile(image_path):
            raise FileNotFoundError(f"File not found: {image_path}")
        img = Image.open(image_path)
        # Handle RGBA/P mode for compatibility
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        raw = pytesseract.image_to_string(img)
        return OCREngine._cleanup_text(raw)


class SpreadsheetCalculator:
    """Add totals row with SUM formulas for numeric columns."""

    @staticmethod
    def col_index_to_letter(index: int) -> str:
        """Convert 0, 1, 2, ... to A, B, C, ... (Excel column letters)."""
        result = ""
        while index >= 0:
            result = chr(index % 26 + ord("A")) + result
            index = index // 26 - 1
        return result

    @staticmethod
    def _is_numeric(value: str) -> bool:
        """Return True if the cell value looks numeric."""
        s = value.strip()
        if not s:
            return False
        try:
            float(s.replace(",", ""))
            return True
        except ValueError:
            return False

    @staticmethod
    def detect_numeric_columns(csv_text: str) -> List[Tuple[int, str]]:
        """
        Analyze CSV and return list of (column_index, header_name) for columns
        that contain only numeric values (excluding header row).
        """
        try:
            reader = csv.reader(io.StringIO(csv_text))
            rows = list(reader)
        except Exception:
            return []
        if len(rows) < 2:
            return []
        headers = rows[0]
        data_rows = rows[1:]
        result = []
        for col_idx in range(len(headers)):
            header = (headers[col_idx] if col_idx < len(headers) else "").strip() or f"Col{col_idx + 1}"
            values = []
            for row in data_rows:
                if col_idx < len(row):
                    values.append(row[col_idx].strip())
            if not values:
                continue
            # Column is numeric if all non-empty values are numeric
            non_empty = [v for v in values if v]
            if not non_empty:
                continue
            if all(SpreadsheetCalculator._is_numeric(v) for v in non_empty):
                result.append((col_idx, header))
        return result

    @staticmethod
    def add_totals_row(csv_text: str, numeric_columns: List[Tuple[int, str]]) -> str:
        """
        Append a totals row: "Total" in first column, =SUM(ColN2:ColNlast) in numeric columns.
        numeric_columns is list of (col_index, header_name) from detect_numeric_columns.
        """
        if not numeric_columns:
            return csv_text
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        if len(rows) < 2:
            return csv_text
        num_data_rows = len(rows) - 1  # exclude header
        num_cols = max(len(r) for r in rows)
        totals_row = [""] * num_cols
        totals_row[0] = "Total"
        for col_idx, _ in numeric_columns:
            if col_idx >= num_cols:
                continue
            col_letter = SpreadsheetCalculator.col_index_to_letter(col_idx)
            # Data is in rows 2..N (1-based); in 0-based, rows[1] to rows[-1]
            start_cell = f"{col_letter}2"
            end_cell = f"{col_letter}{num_data_rows + 1}"
            totals_row[col_idx] = f"=SUM({start_cell}:{end_cell})"
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        for row in rows:
            writer.writerow(row)
        writer.writerow(totals_row)
        return output.getvalue().strip()


def _attach_tooltip(widget, text: str):
    """Minimal hover tooltip for ttk widgets."""
    tip = {"win": None}

    def show(_event=None):
        if tip["win"] is not None:
            return
        x = widget.winfo_rootx() + 20
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(
            tw,
            text=text,
            justify=tk.LEFT,
            background="#ffffe0",
            relief=tk.SOLID,
            borderwidth=1,
            font=("Arial", 9),
            padx=6,
            pady=4,
        )
        lbl.pack()
        tip["win"] = tw

    def hide(_event=None):
        w = tip["win"]
        tip["win"] = None
        if w is not None:
            try:
                w.destroy()
            except tk.TclError:
                pass

    widget.bind("<Enter>", show)
    widget.bind("<Leave>", hide)


class SmartFillTypingAdapter:
    """Small adapter to reuse TypingEngine settings for Smart Fill field typing."""

    def __init__(self, autoflow_app: "AutoFlow"):
        self.app = autoflow_app

    def _debug(self, message: str) -> None:
        logger.debug(message)

    def _build_engine(self) -> TypingEngine:
        config = TypingConfig(
            wpm=self.app.demo_mode.get_demo_speed() if self.app.demo_mode.enabled else self.app.wpm_var.get(),
            humanization_level=self.app.human_var.get(),
            speed_variation=self.app.variation_var.get(),
            thinking_pauses=self.app.thinking_var.get(),
            punctuation_pauses=self.app.punctuation_var.get(),
            typos_enabled=self.app.typos_var.get(),
            mode="text",
            countdown_seconds=0,
        )
        self._debug(
            "Building TypingEngine with config: "
            f"wpm={config.wpm}, humanization={config.humanization_level}, "
            f"variation={config.speed_variation}, thinking={config.thinking_pauses}, "
            f"punctuation={config.punctuation_pauses}, typos={config.typos_enabled}, "
            f"countdown={config.countdown_seconds}"
        )
        return TypingEngine(
            config,
            should_stop=lambda: self.app.should_stop or not self.app.smart_fill_session.is_running,
            is_paused=lambda: self.app.is_paused or self.app.smart_fill_session.is_paused,
            on_status=lambda s: self.app.root.after(0, lambda: self.app.status_label.config(text=s)),
        )

    def type_text(self, text: str) -> None:
        preview = text if len(text) <= 80 else text[:77] + "..."
        self._debug(f"type_text called len={len(text)} preview={preview!r}")
        self._build_engine().type_text(text)
        self._debug("type_text completed")

    def press_tab(self) -> None:
        self._debug("press_tab called")
        pyautogui.press("tab")
        self._debug("press_tab completed")

    def press_enter(self) -> None:
        self._debug("press_enter called")
        pyautogui.press("enter")
        self._debug("press_enter completed")

    def send_key(self, key: str, test_mode: bool = False) -> str:
        try:
            self._debug(f"send_key called key={key!r} test_mode={test_mode}")
            pyautogui.press(key)
            self._debug("send_key accepted")
            return "accepted"
        except Exception as exc:
            self._debug(f"send_key exception: {type(exc).__name__}: {exc}")
            return "unknown"


class AutoFlow:
    def __init__(self, root):
        self.root = root
        self._is_mac = platform.system() == "Darwin"
        self._mod = "Command" if self._is_mac else "Control"
        self._status_flash_after_id = None
        self.root.title("Typestra")
        self.root.geometry("850x900")
        self.root.resizable(True, True)
        
        # Make window scrollable
        self.root.update_idletasks()
        self.root.minsize(750, 700)
        
        # Configure pyautogui
        pyautogui.FAILSAFE = True  # Move mouse to corner to stop
        
        # State variables
        self.is_typing = False
        self.should_stop = False
        self.is_paused = False  # NEW: Track pause state
        self.mode = "text"  # "text" or "spreadsheet"
        self.smart_fill_session = SmartFillSession()
        self.retry_manager = RetryManager()
        self.batch_history = BatchHistory()
        self.context_verifier = ContextVerifier()
        self.demo_mode = DemoMode()
        self.smart_fill_thread = None
        self.smart_fill_settings = {}
        self.sleep_wake_detector = None
        self.current_browser_type = "other"
        
        # Setup global hotkey listener if available
        if PYNPUT_AVAILABLE:
            self.setup_hotkey_listener()
        else:
            print(
                "[AutoFlow] Global pynput listener disabled (using Tk key bindings only).",
                flush=True,
            )
        
        # Auto-pause when window gains focus (user clicked AutoFlow window)
        self.root.bind('<FocusIn>', self.on_window_focus)

        self._loading_settings = False

        # Create UI, then restore saved preferences
        self.create_ui()
        self.load_settings()
        self._setup_keyboard_shortcuts()
        self.sleep_wake_detector = SleepWakeDetector()
        self.sleep_wake_detector.register_wake_handler(
            lambda: self.root.after(0, self.on_wake_from_sleep)
        )
        self.root.after(200, self.check_for_interrupted_session)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def create_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        autoflow_tab = ttk.Frame(self.notebook)
        self.notebook.add(autoflow_tab, text="AutoFlow")

        smart_fill_tab = ttk.Frame(self.notebook)
        self.notebook.add(smart_fill_tab, text="Smart Fill")

        # Create a canvas with scrollbar for the AutoFlow tab
        canvas = tk.Canvas(autoflow_tab)
        scrollbar = ttk.Scrollbar(autoflow_tab, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Pack canvas and scrollbar
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Enable mousewheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        # Main container with padding (now inside scrollable frame)
        main_frame = ttk.Frame(scrollable_frame, padding="20")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights
        autoflow_tab.columnconfigure(0, weight=1)
        autoflow_tab.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)
        
        # Title
        title_label = ttk.Label(
            main_frame, 
            text="⚡ AutoFlow v3.0",
            font=("Arial", 18, "bold")
        )
        title_label.grid(row=0, column=0, pady=(0, 5), sticky=tk.W)
        
        subtitle_label = ttk.Label(
            main_frame,
            text="Professional Workflow Automation | Text & Spreadsheet Support",
            font=("Arial", 9)
        )
        subtitle_label.grid(row=1, column=0, pady=(0, 15), sticky=tk.W)
        
        # Mode selection
        mode_frame = ttk.LabelFrame(main_frame, text="Mode Selection", padding="10")
        mode_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        mode_frame.columnconfigure(0, weight=1)
        
        self.mode_var = tk.StringVar(value="text")
        
        text_radio = ttk.Radiobutton(
            mode_frame,
            text="📝 Text Mode (Documents, emails, content)",
            variable=self.mode_var,
            value="text",
            command=self._on_mode_changed
        )
        text_radio.grid(row=0, column=0, sticky=tk.W, pady=5, padx=5)
        
        sheet_radio = ttk.Radiobutton(
            mode_frame,
            text="📊 Spreadsheet Mode (Excel, Google Sheets, CSV)",
            variable=self.mode_var,
            value="spreadsheet",
            command=self._on_mode_changed
        )
        sheet_radio.grid(row=1, column=0, sticky=tk.W, pady=5, padx=5)
        
        # Text input area
        self.text_frame = ttk.LabelFrame(main_frame, text="Your Content", padding="10")
        self.text_frame.grid(row=3, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 15))
        self.text_frame.columnconfigure(0, weight=1)
        self.text_frame.rowconfigure(0, weight=1)
        
        self.text_input = scrolledtext.ScrolledText(
            self.text_frame,
            wrap=tk.WORD,
            width=70,
            height=8,
            font=("Arial", 10)
        )
        self.text_input.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.text_input.bind('<KeyRelease>', self.update_stats)
        
        # Stats/help text
        self.stats_frame = ttk.Frame(self.text_frame)
        self.stats_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(5, 0))
        
        self.stats_label = ttk.Label(self.stats_frame, text="0 words, 0 characters")
        self.stats_label.pack(side=tk.LEFT)
        
        # Clear All button
        self.clear_text_btn = ttk.Button(
            self.stats_frame,
            text="Clear All",
            command=self.clear_text
        )
        self.clear_text_btn.pack(side=tk.LEFT, padx=(10, 0))
        _clear_tip = f"{'⌘' if self._is_mac else 'Ctrl+'}K: Clear all text"
        _attach_tooltip(self.clear_text_btn, _clear_tip)

        # Extract from Image (Pro) - only in text mode
        self.extract_image_btn = ttk.Button(
            self.stats_frame,
            text="📷 Extract from Image",
            command=self.extract_from_image
        )
        self.extract_image_btn.pack(side=tk.LEFT, padx=(10, 0))
        
        self.help_label = ttk.Label(
            self.stats_frame, 
            text="",
            foreground="gray"
        )
        self.help_label.pack(side=tk.RIGHT)
        
        # File import button (for spreadsheet mode)
        self.import_button = ttk.Button(
            self.text_frame,
            text="📂 Import CSV File",
            command=self.import_csv
        )
        # Don't grid it yet - only shown in spreadsheet mode
        
        # Settings frame
        settings_frame = ttk.LabelFrame(main_frame, text="Settings", padding="10")
        settings_frame.grid(row=4, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        settings_frame.columnconfigure(1, weight=1)
        
        # WPM setting
        ttk.Label(settings_frame, text="Base Typing Speed:").grid(row=0, column=0, sticky=tk.W, pady=5)
        
        wpm_frame = ttk.Frame(settings_frame)
        wpm_frame.grid(row=0, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        self.wpm_var = tk.IntVar(value=50)
        self.wpm_scale = ttk.Scale(
            wpm_frame,
            from_=30,
            to=80,
            orient=tk.HORIZONTAL,
            variable=self.wpm_var,
            command=self.update_wpm_label
        )
        self.wpm_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.wpm_scale.bind("<ButtonRelease-1>", self._on_slider_released)
        
        self.wpm_label = ttk.Label(wpm_frame, text="50 WPM", width=10)
        self.wpm_label.pack(side=tk.LEFT, padx=(10, 0))
        
        # Countdown setting
        ttk.Label(settings_frame, text="Countdown:").grid(row=1, column=0, sticky=tk.W, pady=5)
        
        countdown_frame = ttk.Frame(settings_frame)
        countdown_frame.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        self.countdown_var = tk.IntVar(value=5)
        self.countdown_scale = ttk.Scale(
            countdown_frame,
            from_=3,
            to=10,
            orient=tk.HORIZONTAL,
            variable=self.countdown_var,
            command=self.update_countdown_label
        )
        self.countdown_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.countdown_scale.bind("<ButtonRelease-1>", self._on_slider_released)
        
        self.countdown_label = ttk.Label(countdown_frame, text="5 seconds", width=10)
        self.countdown_label.pack(side=tk.LEFT, padx=(10, 0))
        
        # Humanization level
        ttk.Label(settings_frame, text="Humanization Level:").grid(row=2, column=0, sticky=tk.W, pady=5)
        
        human_frame = ttk.Frame(settings_frame)
        human_frame.grid(row=2, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        self.human_var = tk.IntVar(value=2)
        self.human_scale = ttk.Scale(
            human_frame,
            from_=1,
            to=3,
            orient=tk.HORIZONTAL,
            variable=self.human_var,
            command=self.update_human_label
        )
        self.human_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.human_scale.bind("<ButtonRelease-1>", self._on_slider_released)
        
        self.human_label = ttk.Label(human_frame, text="Medium", width=10)
        self.human_label.pack(side=tk.LEFT, padx=(10, 0))
        
        # Spreadsheet-specific settings
        self.sheet_settings_frame = ttk.LabelFrame(settings_frame, text="Spreadsheet Options", padding="5")
        # Don't grid it yet - only shown in spreadsheet mode
        
        self.nav_var = tk.StringVar(value="tab")
        ttk.Radiobutton(
            self.sheet_settings_frame,
            text="Navigate with Tab (moves right, then down)",
            variable=self.nav_var,
            value="tab",
            command=self._on_settings_changed,
        ).pack(anchor=tk.W, pady=2)

        ttk.Radiobutton(
            self.sheet_settings_frame,
            text="Navigate with Enter (moves down, then right)",
            variable=self.nav_var,
            value="enter",
            command=self._on_settings_changed,
        ).pack(anchor=tk.W, pady=2)

        self.add_totals_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.sheet_settings_frame,
            text="☐ Add totals row (SUM formulas)",
            variable=self.add_totals_var,
            command=self._on_settings_changed,
        ).pack(anchor=tk.W, pady=2)
        
        # Options checkboxes
        self.variation_var = tk.BooleanVar(value=True)
        self.variation_check = ttk.Checkbutton(
            settings_frame,
            text="✓ Speed variation (faster and slower bursts)",
            variable=self.variation_var,
            command=self._on_settings_changed,
        )
        self.variation_check.grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(10, 2))
        
        self.thinking_var = tk.BooleanVar(value=True)
        self.thinking_check = ttk.Checkbutton(
            settings_frame,
            text="✓ Thinking pauses (random hesitations while typing)",
            variable=self.thinking_var,
            command=self._on_settings_changed,
        )
        self.thinking_check.grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=2)
        
        self.punctuation_var = tk.BooleanVar(value=True)
        self.punctuation_check = ttk.Checkbutton(
            settings_frame,
            text="✓ Punctuation pauses (longer breaks after sentences)",
            variable=self.punctuation_var,
            command=self._on_settings_changed,
        )
        self.punctuation_check.grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=2)
        
        self.typos_var = tk.BooleanVar(value=True)
        self.typos_check = ttk.Checkbutton(
            settings_frame,
            text="✓ Realistic typos & corrections (makes and fixes mistakes)",
            variable=self.typos_var,
            command=self._on_settings_changed,
        )
        self.typos_check.grid(row=7, column=0, columnspan=2, sticky=tk.W, pady=2)

        # Smart Fill settings quick panel
        self.sf_settings_frame = ttk.LabelFrame(settings_frame, text="Smart Fill Settings", padding="5")
        self.sf_settings_frame.grid(row=8, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 2))
        self.sf_global_auto_var = tk.BooleanVar(value=True)
        self.sf_global_delay_var = tk.IntVar(value=3)
        self.sf_global_checkpoint_var = tk.IntVar(value=5)
        self.sf_global_timeout_var = tk.IntVar(value=10)
        self.sf_global_demo_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(
            self.sf_settings_frame,
            text="Enable auto-advance to next row",
            variable=self.sf_global_auto_var,
            command=self._on_settings_changed,
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(self.sf_settings_frame, text="Delay").grid(row=1, column=0, sticky="w")
        ttk.Spinbox(
            self.sf_settings_frame, from_=1, to=30, width=5, textvariable=self.sf_global_delay_var,
            command=self._on_settings_changed
        ).grid(row=1, column=1, sticky="w", padx=5)
        ttk.Label(self.sf_settings_frame, text="Checkpoint every N rows").grid(row=2, column=0, sticky="w")
        ttk.Spinbox(
            self.sf_settings_frame, from_=1, to=50, width=5, textvariable=self.sf_global_checkpoint_var,
            command=self._on_settings_changed
        ).grid(row=2, column=1, sticky="w", padx=5)
        ttk.Label(self.sf_settings_frame, text="Timeout seconds").grid(row=3, column=0, sticky="w")
        ttk.Spinbox(
            self.sf_settings_frame, from_=3, to=60, width=5, textvariable=self.sf_global_timeout_var,
            command=self._on_settings_changed
        ).grid(row=3, column=1, sticky="w", padx=5)
        ttk.Checkbutton(
            self.sf_settings_frame,
            text="Enable Demo Mode",
            variable=self.sf_global_demo_var,
            command=self._on_settings_changed,
        ).grid(row=4, column=0, columnspan=3, sticky="w")
        
        # Button frame
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=5, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)
        button_frame.columnconfigure(2, weight=1)
        button_frame.columnconfigure(3, weight=1)
        
        _start_tip = "⌘+Enter: Start typing" if self._is_mac else "Ctrl+Enter: Start typing"
        self.start_button = ttk.Button(
            button_frame,
            text="▶ Start AutoFlow",
            command=self.start_typing
        )
        self.start_button.grid(row=0, column=0, padx=(0, 5), sticky=(tk.W, tk.E))
        _attach_tooltip(self.start_button, _start_tip)
        
        self.pause_button = ttk.Button(
            button_frame,
            text="⏸ Pause",
            command=self.toggle_pause,
            state=tk.DISABLED
        )
        self.pause_button.grid(row=0, column=1, padx=5, sticky=(tk.W, tk.E))
        
        self.stop_button = ttk.Button(
            button_frame,
            text="⏹ Stop",
            command=self.stop_typing,
            state=tk.DISABLED
        )
        self.stop_button.grid(row=0, column=2, padx=5, sticky=(tk.W, tk.E))
        
        self.clear_button = ttk.Button(
            button_frame,
            text="🗑 Clear",
            command=self.clear_text
        )
        self.clear_button.grid(row=0, column=3, padx=(5, 0), sticky=(tk.W, tk.E))
        
        # Status area
        status_frame = ttk.LabelFrame(main_frame, text="Status", padding="10")
        status_frame.grid(row=6, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        status_frame.columnconfigure(0, weight=1)
        
        self.status_label = ttk.Label(
            status_frame,
            text="Ready. Select mode and paste your content.",
            font=("Arial", 10)
        )
        self.status_label.grid(row=0, column=0, sticky=tk.W)
        
        # Instructions
        instructions_frame = ttk.LabelFrame(main_frame, text="Quick Start", padding="10")
        instructions_frame.grid(row=7, column=0, sticky=(tk.W, tk.E))
        
        _sk = (
            "⌘V Paste · ⌘K Clear · ⌘⏎ Start · ⌘Q Quit"
            if self._is_mac
            else "Ctrl+V Paste · Ctrl+K Clear · Ctrl+Enter Start · Ctrl+Q Quit"
        )
        self.instructions_text = f"""KEYBOARD: {_sk} (paste/clear/start shortcuts are off while AutoFlow is typing; quit always saves)

TEXT MODE: Paste your content (or use 📷 Extract from Image) → Click Start → Switch to target app
SPREADSHEET MODE: Paste CSV data → Click Start → Click first cell (A1)

NOTE: Accented characters are automatically converted to plain text:
• café → cafe, José → Jose, résumé → resume
• This ensures reliable typing across all apps

PASTING TIPS:
• Paste formatted text directly - AutoFlow handles bullets (•), apostrophes (')
• Lists with numbers/letters work great (a., b., 1., 2.)

PAUSE/RESUME: Press F8 anytime OR click AutoFlow window to auto-pause

EMERGENCY STOP: Move mouse to top-left corner"""
        
        instructions_label = ttk.Label(
            instructions_frame,
            text=self.instructions_text,
            justify=tk.LEFT,
            font=("Arial", 9)
        )
        instructions_label.grid(row=0, column=0, sticky=tk.W)

        self.create_smart_fill_tab(smart_fill_tab)

    def create_smart_fill_tab(self, parent):
        self.smart_fill_frame = parent
        self.smart_fill_content = ttk.Frame(parent, padding=12)
        self.smart_fill_content.pack(fill="both", expand=True)
        self.load_smart_fill_settings()
        self.show_import_screen()

    def _clear_smart_fill_content(self):
        for widget in self.smart_fill_content.winfo_children():
            widget.destroy()

    def show_import_screen(self):
        """
        STATE 1: No data loaded - FIXED LAYOUT
        """
        # Clear existing content
        for widget in self.smart_fill_content.winfo_children():
            widget.destroy()

        # Main container with proper centering
        main_container = ttk.Frame(self.smart_fill_content)
        main_container.pack(expand=True, fill="both")

        # Center everything vertically and horizontally
        center_frame = ttk.Frame(main_container)
        center_frame.place(relx=0.5, rely=0.5, anchor="center")

        # Icon/Title
        title = ttk.Label(
            center_frame,
            text="No Data Loaded",
            font=("Helvetica", 24, "bold"),
        )
        title.pack(pady=(0, 10))

        # Subtitle
        subtitle = ttk.Label(
            center_frame,
            text="Load data to start Smart Fill automation",
            font=("Helvetica", 12),
        )
        subtitle.pack(pady=(0, 30))

        # Buttons frame
        buttons_frame = ttk.Frame(center_frame)
        buttons_frame.pack(pady=20)

        # Import CSV button
        import_btn = ttk.Button(
            buttons_frame,
            text="📁 Import CSV File",
            command=self.import_csv_file,
            width=30,
        )
        import_btn.pack(pady=8)

        # Paste from clipboard button
        paste_btn = ttk.Button(
            buttons_frame,
            text="📋 Paste from Clipboard",
            command=self.paste_from_clipboard,
            width=30,
        )
        paste_btn.pack(pady=8)

        # Demo mode button
        demo_btn = ttk.Button(
            buttons_frame,
            text="🎭 Toggle Demo Mode",
            command=self.toggle_demo_mode,
            width=30,
        )
        demo_btn.pack(pady=8)

        # Separator
        ttk.Separator(center_frame, orient="horizontal").pack(fill="x", pady=30, padx=40)

        # Recent mappings section
        recent_label = ttk.Label(
            center_frame,
            text="Recent Mappings",
            font=("Helvetica", 12, "bold"),
        )
        recent_label.pack(pady=(10, 5))

        # Recent mappings list
        mappings_frame = ttk.Frame(center_frame)
        mappings_frame.pack()

        # Check for recent mappings
        recent_mappings = self.get_recent_mappings()

        if recent_mappings:
            for mapping in recent_mappings[:3]:  # Show top 3
                mapping_btn = ttk.Button(
                    mappings_frame,
                    text=f"• {mapping['name']}",
                    command=lambda m=mapping: self.load_saved_mapping(m),
                    width=30,
                )
                mapping_btn.pack(pady=2)
        else:
            no_mappings_label = ttk.Label(
                mappings_frame,
                text="No saved mappings yet.",
                foreground="gray",
                font=("Helvetica", 10),
            )
            no_mappings_label.pack()

    def get_recent_mappings(self):
        """Get list of recent mapping templates"""
        mappings_dir = os.path.expanduser("~/Documents/Typestra/Mappings/")

        if not os.path.exists(mappings_dir):
            return []

        # Get all .json files
        mapping_files = []
        for filename in os.listdir(mappings_dir):
            if filename.endswith(".json"):
                filepath = os.path.join(mappings_dir, filename)
                mapping_files.append(
                    {
                        "name": filename.replace(".json", ""),
                        "path": filepath,
                        "modified": os.path.getmtime(filepath),
                    }
                )

        # Sort by modification time, most recent first
        mapping_files.sort(key=lambda x: x["modified"], reverse=True)

        return mapping_files

    def load_saved_mapping(self, mapping_info):
        """Load a saved mapping template"""
        try:
            with open(mapping_info["path"], "r", encoding="utf-8") as f:
                mapping_data = json.load(f)

            # Load mapping into session
            if hasattr(self, "smart_fill_session"):
                self.smart_fill_session.field_mappings = mapping_data.get("fields", [])
                self.smart_fill_session.auto_advance_config = mapping_data.get("auto_advance", {})

            messagebox.showinfo(
                "Mapping Loaded",
                f"Loaded mapping: {mapping_info['name']}\n\nNow import a CSV to use this mapping.",
            )
        except Exception as e:
            messagebox.showerror(
                "Load Failed",
                f"Could not load mapping:\n{str(e)}",
            )

    def import_csv_file(self):
        filename = filedialog.askopenfilename(
            title="Select CSV File",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not filename:
            return
        result = self.smart_fill_session.load_csv(filename)
        if not result.get("success"):
            messagebox.showerror("CSV Error", result.get("error", "Unknown CSV error"))
            return
        self.show_field_mapping_screen()

    def paste_from_clipboard(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("Clipboard Empty", "Clipboard does not contain text.")
            return
        temp_dir = os.path.expanduser("~/Documents/Typestra/Demo")
        os.makedirs(temp_dir, exist_ok=True)
        temp_file = os.path.join(temp_dir, "clipboard_import.csv")
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(text)
        result = self.smart_fill_session.load_csv(temp_file)
        if not result.get("success"):
            messagebox.showerror("CSV Error", result.get("error", "Invalid clipboard CSV"))
            return
        self.show_field_mapping_screen()

    def show_field_mapping_screen(self):
        """
        STATE 2: Data loaded, map fields - FIXED LAYOUT
        """
        # Clear existing content
        for widget in self.smart_fill_content.winfo_children():
            widget.destroy()

        # Top header - outside scrollable area so it is always visible.
        rows = len(self.smart_fill_session.csv_data) if self.smart_fill_session.csv_data is not None else 0
        csv_name = os.path.basename(self.smart_fill_session.csv_filename) if self.smart_fill_session.csv_filename else "Imported CSV"
        header_frame = ttk.Frame(self.smart_fill_content)
        header_frame.pack(side="top", fill="x", pady=(10, 5))
        ttk.Label(
            header_frame,
            text=f"Data: {csv_name} ({rows} rows)",
            font=("Helvetica", 11),
            anchor="center",
        ).pack()

        # Main content + fixed footer to avoid bottom empty space near action buttons.
        content_area = ttk.Frame(self.smart_fill_content)
        content_area.pack(fill="both", expand=True)

        # Main scrollable container
        canvas = tk.Canvas(content_area, highlightthickness=0)
        scrollbar = ttk.Scrollbar(content_area, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Field Mapping section header
        mapping_label = ttk.Label(
            scrollable_frame,
            text="FIELD MAPPING",
            font=("Helvetica", 11, "bold"),
            anchor="center",
        )
        mapping_label.pack(pady=(10, 12), fill="x")

        # Container for field mapping rows
        self.sf_fields_container = ttk.Frame(scrollable_frame)
        self.sf_fields_container.pack(padx=20, fill="x")

        # Create field mapping rows
        self.field_mapping_widgets = []
        existing_count = max(5, len([m for m in self.smart_fill_session.field_mappings if m]))
        for i in range(existing_count):
            self.add_field_mapping_row(self.sf_fields_container, i + 1)

        # Add Field button - centered
        add_field_frame = ttk.Frame(scrollable_frame)
        add_field_frame.pack(pady=14, fill="x")
        add_field_btn = ttk.Button(
            add_field_frame,
            text="+ Add Field",
            command=self.add_more_field,
            width=20,
        )
        add_field_btn.pack()

        # Separator
        ttk.Separator(scrollable_frame, orient="horizontal").pack(fill="x", pady=18, padx=20)

        # Automation Settings header
        auto_label = ttk.Label(
            scrollable_frame,
            text="AUTOMATION SETTINGS",
            font=("Helvetica", 11, "bold"),
            anchor="center",
        )
        auto_label.pack(pady=(4, 12), fill="x")

        # Settings container - centered with max width
        settings_container = ttk.Frame(scrollable_frame)
        settings_container.pack(padx=40, pady=(0, 12))

        self.sf_auto_advance_var = tk.BooleanVar(value=self.smart_fill_settings.get("enabled", True))
        self.sf_delay_var = tk.IntVar(value=int(self.smart_fill_settings.get("delay_seconds", 3)))
        self.sf_checkpoint_var = tk.BooleanVar(value=self.smart_fill_settings.get("checkpoint_enabled", True))
        self.sf_checkpoint_every_var = tk.IntVar(value=int(self.smart_fill_settings.get("checkpoint_every", 5)))
        self.sf_checkpoint_pause_var = tk.IntVar(value=int(self.smart_fill_settings.get("checkpoint_pause", 5)))
        self.sf_timeout_var = tk.IntVar(value=int(self.smart_fill_settings.get("timeout_seconds", 10)))
        self.sf_stop_on_error_var = tk.BooleanVar(value=bool(self.smart_fill_settings.get("stop_on_error", False)))
        self.sf_press_enter_var = tk.BooleanVar(value=self.smart_fill_settings.get("action", "next_row") == "submit_form")
        self.sf_action_var = tk.StringVar(value="submit_form" if self.sf_press_enter_var.get() else "next_row")
        self.sf_demo_enabled_var = tk.BooleanVar(value=bool(self.smart_fill_settings.get("demo_enabled", False)))

        # Optional aliases for legacy/new naming compatibility.
        self.auto_advance_var = self.sf_auto_advance_var
        self.delay_var = self.sf_delay_var
        self.press_enter_var = self.sf_press_enter_var
        self.checkpoint_var = self.sf_checkpoint_var
        self.checkpoint_rows_var = self.sf_checkpoint_every_var
        self.checkpoint_pause_var = self.sf_checkpoint_pause_var
        self.timeout_var = self.sf_timeout_var
        self.stop_on_error_var = self.sf_stop_on_error_var
        self.action_var = self.sf_action_var
        self.demo_mode_var = self.sf_demo_enabled_var

        auto_frame = ttk.Frame(settings_container)
        auto_frame.grid(row=0, column=0, columnspan=4, sticky="w", pady=5)
        ttk.Checkbutton(
            auto_frame,
            text="Auto-advance after filling",
            variable=self.sf_auto_advance_var,
        ).pack(side="left")
        ttk.Label(auto_frame, text="(").pack(side="left", padx=(5, 0))
        ttk.Spinbox(
            auto_frame,
            from_=1,
            to=10,
            textvariable=self.sf_delay_var,
            width=5,
        ).pack(side="left")
        ttk.Label(auto_frame, text="second delay)").pack(side="left", padx=(2, 0))

        ttk.Checkbutton(
            settings_container,
            text="Press Enter after last field (submits form before advancing)",
            variable=self.sf_press_enter_var,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(5, 10), padx=(20, 0))

        ttk.Checkbutton(
            settings_container,
            text="Manual checkpoints",
            variable=self.sf_checkpoint_var,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(5, 5))

        ttk.Label(settings_container, text="    Every N rows:").grid(row=4, column=0, sticky="w", padx=(20, 5))
        ttk.Spinbox(
            settings_container,
            from_=1,
            to=50,
            width=8,
            textvariable=self.sf_checkpoint_every_var,
        ).grid(row=4, column=1, sticky="w")
        ttk.Label(settings_container, text="Pause seconds:").grid(row=4, column=2, sticky="w", padx=(10, 5))
        ttk.Spinbox(
            settings_container,
            from_=1,
            to=30,
            width=8,
            textvariable=self.sf_checkpoint_pause_var,
        ).grid(row=4, column=3, sticky="w")

        additional_frame = ttk.Frame(settings_container)
        additional_frame.grid(row=5, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Label(additional_frame, text="    Timeout seconds:").pack(side="left", padx=(0, 5))
        ttk.Spinbox(
            additional_frame,
            from_=3,
            to=60,
            width=8,
            textvariable=self.sf_timeout_var,
        ).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(
            additional_frame,
            text="Stop batch on error",
            variable=self.sf_stop_on_error_var,
        ).pack(side="left", padx=(0, 15))
        ttk.Checkbutton(
            additional_frame,
            text="Enable Demo Mode",
            variable=self.sf_demo_enabled_var,
        ).pack(side="left")

        # Pack canvas and scrollbar
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Bottom button frame - fixed position
        button_frame = ttk.Frame(self.smart_fill_content)
        button_frame.pack(side="bottom", pady=12, fill="x")
        button_container = ttk.Frame(button_frame)
        button_container.pack()
        ttk.Button(
            button_container,
            text="Save Mapping",
            command=self.save_mapping_dialog,
            width=18,
        ).pack(side="left", padx=8)
        ttk.Button(
            button_container,
            text="Start Filling",
            command=self.start_batch_execution,
            width=18,
        ).pack(side="left", padx=8)

    def add_field_mapping_row(self, parent, field_num):
        row = ttk.Frame(parent, relief="groove", borderwidth=1, padding=6)
        row.pack(fill="x", padx=10, pady=4)
        ttk.Label(row, text=f"Field {field_num}:", width=10).grid(row=0, column=0, padx=5, pady=4, sticky="w")

        type_var = tk.StringVar(value="Text")
        type_dropdown = ttk.Combobox(
            row,
            textvariable=type_var,
            values=["Text", "Click (Coming in Phase 2)", "Dropdown (Coming in Phase 2)", "Checkbox (Coming in Phase 2)"],
            state="readonly",
            width=28,
        )
        type_dropdown.current(0)
        type_dropdown.grid(row=0, column=1, padx=5, pady=4)
        type_dropdown.bind("<<ComboboxSelected>>", lambda _e: type_dropdown.current(0))

        col_var = tk.StringVar(value="-- None --")
        columns = ["-- None --"] + self.smart_fill_session.column_headers
        col_dd = ttk.Combobox(row, textvariable=col_var, values=columns, state="readonly", width=24)
        col_dd.grid(row=0, column=2, padx=5, pady=4)

        preview = ttk.Label(row, text="Preview: ", foreground="gray")
        preview.grid(row=1, column=1, columnspan=2, sticky="w", padx=5, pady=2)
        col_dd.bind("<<ComboboxSelected>>", lambda _e, n=field_num, v=col_var: self.update_preview(n, v.get()))

        skip_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Skip if empty", variable=skip_var).grid(row=0, column=3, padx=5, pady=4)

        existing = None
        if field_num <= len(self.smart_fill_session.field_mappings):
            existing = self.smart_fill_session.field_mappings[field_num - 1]
        if existing:
            selected_column = existing.get("column", "-- None --")
            if selected_column in columns:
                col_var.set(selected_column)
            skip_var.set(bool(existing.get("skip_empty", False)))
            self.update_preview(field_num, col_var.get(), preview_widget=preview)

        self.field_mapping_widgets.append(
            {
                "field_num": field_num,
                "type_var": type_var,
                "column_var": col_var,
                "skip_var": skip_var,
                "preview_label": preview,
            }
        )

    def add_more_field(self, parent=None):
        target_parent = parent or getattr(self, "sf_fields_container", None)
        if target_parent is None:
            return
        self.add_field_mapping_row(target_parent, len(self.field_mapping_widgets) + 1)

    def update_preview(self, field_num, column_name, preview_widget=None):
        widget = preview_widget
        if widget is None:
            for entry in self.field_mapping_widgets:
                if entry["field_num"] == field_num:
                    widget = entry["preview_label"]
                    break
        if widget is None:
            return
        if column_name in ("", "-- None --") or self.smart_fill_session.csv_data is None:
            widget.config(text="Preview: ")
            return
        try:
            value = str(self.smart_fill_session.csv_data.loc[0, column_name])
        except Exception:
            value = ""
        value = value.replace("\n", " ").replace("\r", " ")
        if len(value) > 30:
            value = value[:30] + "..."
        widget.config(text=f'Preview: "{value}"')

    def collect_field_mappings(self):
        mappings = []
        for entry in self.field_mapping_widgets:
            col = entry["column_var"].get()
            if col == "-- None --":
                mappings.append(None)
                continue
            mappings.append(
                {
                    "type": "text",
                    "column": col,
                    "skip_empty": bool(entry["skip_var"].get()),
                }
            )
        self.smart_fill_session.field_mappings = mappings

    def save_mapping_dialog(self):
        self.collect_field_mappings()
        name = simpledialog.askstring("Save Mapping", "Template name:")
        if not name:
            return
        path = self.smart_fill_session.save_mapping(name.strip())
        messagebox.showinfo("Mapping Saved", f"Saved to:\n{path}")

    def save_smart_fill_settings(self):
        os.makedirs(os.path.dirname(SMART_FILL_SETTINGS_PATH), exist_ok=True)
        payload = {
            "enabled": bool(self.sf_auto_advance_var.get()) if hasattr(self, "sf_auto_advance_var") else True,
            "delay_seconds": int(self.sf_delay_var.get()) if hasattr(self, "sf_delay_var") else 3,
            "action": (
                "submit_form"
                if hasattr(self, "sf_press_enter_var") and bool(self.sf_press_enter_var.get())
                else "next_row"
            ),
            "checkpoint_enabled": bool(self.sf_checkpoint_var.get()) if hasattr(self, "sf_checkpoint_var") else True,
            "checkpoint_every": int(self.sf_checkpoint_every_var.get()) if hasattr(self, "sf_checkpoint_every_var") else 5,
            "checkpoint_pause": int(self.sf_checkpoint_pause_var.get()) if hasattr(self, "sf_checkpoint_pause_var") else 5,
            "timeout_seconds": int(self.sf_timeout_var.get()) if hasattr(self, "sf_timeout_var") else 10,
            "stop_on_error": bool(self.sf_stop_on_error_var.get()) if hasattr(self, "sf_stop_on_error_var") else False,
            "demo_enabled": bool(self.sf_demo_enabled_var.get()) if hasattr(self, "sf_demo_enabled_var") else False,
        }
        with open(SMART_FILL_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        self.smart_fill_settings = payload

    def load_smart_fill_settings(self):
        if os.path.isfile(SMART_FILL_SETTINGS_PATH):
            try:
                with open(SMART_FILL_SETTINGS_PATH, "r", encoding="utf-8") as f:
                    self.smart_fill_settings = json.load(f)
            except (OSError, json.JSONDecodeError):
                self.smart_fill_settings = {}
        else:
            self.smart_fill_settings = {}

    def start_batch_execution(self):
        if self.smart_fill_session.csv_data is None:
            messagebox.showwarning("No Data", "Import CSV data first.")
            return

        context = BrowserContext()
        browser_type = context.get_browser_type()
        if browser_type == "firefox":
            firefox_warning = FirefoxWarningDialog()
            if firefox_warning.should_show_warning():
                continue_anyway = firefox_warning.show_warning(parent=self.root)
                if not continue_anyway:
                    return
        self.current_browser_type = browser_type

        self.collect_field_mappings()
        self.save_smart_fill_settings()

        action = "submit_form" if bool(self.sf_press_enter_var.get()) else "next_row"
        self.sf_action_var.set(action)

        self.smart_fill_session.auto_advance.enabled = bool(self.sf_auto_advance_var.get())
        self.smart_fill_session.auto_advance.delay_seconds = int(self.sf_delay_var.get())
        self.smart_fill_session.auto_advance.action = action
        self.smart_fill_session.auto_advance.timeout_seconds = int(self.sf_timeout_var.get())
        self.smart_fill_session.auto_advance.stop_on_error = bool(self.sf_stop_on_error_var.get())

        if bool(self.sf_demo_enabled_var.get()) and not self.demo_mode.enabled:
            self.demo_mode.enable(self.root, "candidates")
        elif not bool(self.sf_demo_enabled_var.get()) and self.demo_mode.enabled:
            self.demo_mode.disable()

        checkpoint = CheckpointManager(
            every_n_rows=int(self.sf_checkpoint_every_var.get()),
            pause_duration=int(self.sf_checkpoint_pause_var.get()),
            enabled=bool(self.sf_checkpoint_var.get()),
        )
        typing_adapter = SmartFillTypingAdapter(self)
        detector = TimeoutDetector(typing_engine=typing_adapter)
        self.smart_fill_thread = threading.Thread(
            target=self.smart_fill_session.execute_batch,
            kwargs={
                "typing_engine": typing_adapter,
                "error_detector": detector,
                "checkpoint_manager": checkpoint,
                "status_cb": lambda s: self.root.after(0, lambda: self.countdown_label.config(text=s)),
                "row_cb": lambda _r: self.root.after(0, self.refresh_active_filling_screen),
                "browser_cb": lambda b: self.root.after(0, lambda: self._on_batch_browser_detected(b)),
                "completion_cb": lambda ok, err: self.root.after(0, lambda: self.on_smart_fill_complete(ok, err)),
            },
            daemon=True,
        )
        self.show_active_filling_screen()
        self.register_smart_fill_hotkeys()
        # Must be set immediately before thread start to avoid focus/hotkey races.
        self.smart_fill_session.is_paused = False
        self.smart_fill_session.is_running = True
        self.smart_fill_thread.start()

    def show_active_filling_screen(self):
        self._clear_smart_fill_content()
        ttk.Label(
            self.smart_fill_content,
            text="FILLING IN PROGRESS...",
            font=("Helvetica", 16, "bold"),
            foreground="green",
        ).pack(pady=20)

        self.row_counter_label = ttk.Label(self.smart_fill_content, text="", font=("Helvetica", 14))
        self.row_counter_label.pack(pady=6)

        browser_frame = ttk.Frame(self.smart_fill_content)
        browser_frame.pack(pady=5)
        display_names = {
            "safari": "Safari",
            "chrome": "Chrome",
            "brave": "Brave",
            "firefox": "Firefox",
            "other": "Unknown Browser",
        }
        browser_name = display_names.get(self.current_browser_type, "Unknown Browser")
        is_supported = self.current_browser_type in {"safari", "chrome", "brave"}
        browser_label_text = f"Browser: {browser_name}"
        if is_supported:
            self.browser_status_label = ttk.Label(
                browser_frame,
                text=f"{browser_label_text} ✓",
                font=("Helvetica", 10),
                foreground="green",
            )
            self.browser_status_label.pack()
            self.browser_hint_label = None
        else:
            self.browser_status_label = ttk.Label(
                browser_frame,
                text=f"{browser_label_text} ⚠️",
                font=("Helvetica", 10),
                foreground="orange",
            )
            self.browser_status_label.pack()
            self.browser_hint_label = ttk.Label(
                browser_frame,
                text="(Error detection may be unreliable)",
                font=("Helvetica", 8),
                foreground="gray",
            )
            self.browser_hint_label.pack()

        data_frame = ttk.LabelFrame(self.smart_fill_content, text="Current Data", padding=10)
        data_frame.pack(pady=10, padx=20, fill="x")
        self.current_data_labels = {}
        for col in self.smart_fill_session.column_headers[:4]:
            row = ttk.Frame(data_frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{col}:", width=15).pack(side="left")
            lbl = ttk.Label(row, text="", foreground="blue")
            lbl.pack(side="left")
            self.current_data_labels[col] = lbl

        self.progress_bar = ttk.Progressbar(
            self.smart_fill_content,
            length=420,
            mode="determinate",
            maximum=max(1, len(self.smart_fill_session.csv_data)),
        )
        self.progress_bar.pack(pady=10)
        self.progress_pct_label = ttk.Label(self.smart_fill_content, text="0%")
        self.progress_pct_label.pack(pady=4)
        self.countdown_label = ttk.Label(self.smart_fill_content, text="")
        self.countdown_label.pack(pady=8)

        controls = ttk.Frame(self.smart_fill_content)
        controls.pack(pady=14)
        self.pause_btn = ttk.Button(controls, text="Pause", command=self.pause_batch)
        self.pause_btn.pack(side="left", padx=5)
        ttk.Button(controls, text="Skip Row", command=self.skip_current_row).pack(side="left", padx=5)
        ttk.Button(controls, text="Stop", command=self.stop_batch).pack(side="left", padx=5)
        self.refresh_active_filling_screen()

    def _render_browser_status(self):
        if not hasattr(self, "browser_status_label"):
            return
        display_names = {
            "safari": "Safari",
            "chrome": "Chrome",
            "brave": "Brave",
            "firefox": "Firefox",
            "other": "Unknown Browser",
        }
        browser_name = display_names.get(self.current_browser_type, "Unknown Browser")
        is_supported = self.current_browser_type in {"safari", "chrome", "brave"}
        if is_supported:
            self.browser_status_label.config(
                text=f"Browser: {browser_name} ✓",
                foreground="green",
            )
            if hasattr(self, "browser_hint_label") and self.browser_hint_label is not None:
                self.browser_hint_label.config(text="")
        else:
            self.browser_status_label.config(
                text=f"Browser: {browser_name} ⚠️",
                foreground="orange",
            )
            if hasattr(self, "browser_hint_label") and self.browser_hint_label is not None:
                self.browser_hint_label.config(text="(Error detection may be unreliable)")

    def _on_batch_browser_detected(self, browser_type: str):
        self.current_browser_type = browser_type or "other"
        self._render_browser_status()

    def refresh_active_filling_screen(self):
        if self.smart_fill_session.csv_data is None:
            return
        self._render_browser_status()
        total = len(self.smart_fill_session.csv_data)
        cur = min(self.smart_fill_session.current_row + 1, total)
        self.row_counter_label.config(text=f"Current Row: {cur} / {total}")
        idx = min(max(self.smart_fill_session.current_row, 0), max(total - 1, 0))
        row_data = self.smart_fill_session.csv_data.loc[idx]
        for col, lbl in self.current_data_labels.items():
            value = str(row_data.get(col, "")).replace("\n", " ").replace("\r", " ")
            if len(value) > 30:
                value = value[:30] + "..."
            lbl.config(text=value)
        progress_value = min(max(self.smart_fill_session.current_row, 0), total)
        self.progress_bar["value"] = progress_value
        pct = (progress_value / max(total, 1)) * 100
        self.progress_pct_label.config(text=f"{pct:.0f}%")

    def on_smart_fill_complete(self, successful_count, error_count):
        if self.demo_mode.enabled:
            self.demo_mode.disable()
        total_rows = len(self.smart_fill_session.csv_data) if self.smart_fill_session.csv_data is not None else 0
        if self.smart_fill_session.batch_id:
            self.batch_history.save_batch_metadata(
                batch_id=self.smart_fill_session.batch_id,
                csv_file=self.smart_fill_session.csv_filename or "clipboard_import.csv",
                total_rows=total_rows,
                successful=successful_count,
                errors=error_count,
                mapping_used="active_mapping",
            )
        self.smart_fill_session.clear_recovery_state()
        self.show_completion_screen(successful_count, error_count)

    def show_completion_screen(self, successful_count, error_count):
        self._clear_smart_fill_content()
        ttk.Label(
            self.smart_fill_content,
            text="Batch Complete!",
            font=("Helvetica", 18, "bold"),
            foreground="green",
        ).pack(pady=20)
        ttk.Label(self.smart_fill_content, text=f"Successful: {successful_count}", foreground="green").pack()
        ttk.Label(self.smart_fill_content, text=f"Errors: {error_count}", foreground="orange").pack()

        details_frame = ttk.LabelFrame(
            self.smart_fill_content,
            text="Batch Details",
            padding=10,
        )
        details_frame.pack(pady=10, padx=20, fill="x")
        display_names = {
            "safari": "Safari",
            "chrome": "Chrome",
            "brave": "Brave",
            "firefox": "Firefox",
            "other": "Unknown Browser",
        }
        browser_name = display_names.get(self.current_browser_type, "Unknown Browser")
        ttk.Label(details_frame, text=f"Browser: {browser_name}", font=("Helvetica", 9)).pack(anchor="w")
        ttk.Label(
            details_frame,
            text=f"Completed: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}",
            font=("Helvetica", 9),
        ).pack(anchor="w")

        actions = ttk.Frame(self.smart_fill_content)
        actions.pack(pady=16)
        if error_count > 0:
            ttk.Button(actions, text="View Error Log", command=self.view_error_log).pack(side="left", padx=5)
            ttk.Button(actions, text="Retry Failed Rows", command=self.retry_failed_rows).pack(side="left", padx=5)
        ttk.Button(actions, text="New Batch", command=self.show_import_screen).pack(side="left", padx=5)

        ttk.Label(self.smart_fill_content, text="Recent batches", font=("Helvetica", 12, "bold")).pack(pady=(20, 10))
        self.display_recent_batches()

    def display_recent_batches(self):
        batches = self.retry_manager.get_recent_batches(limit=5)
        container = ttk.Frame(self.smart_fill_content)
        container.pack(fill="x", padx=20)
        if not batches:
            ttk.Label(container, text="No batches yet.", foreground="gray").pack(anchor="w")
            return
        for batch in batches:
            success = batch.get("successful", 0)
            total = batch.get("total_rows", 0)
            ts = batch.get("timestamp", "")[:19].replace("T", " ")
            ttk.Label(container, text=f"- {ts}  {success}/{total} success").pack(anchor="w")

    def view_error_log(self):
        if not self.smart_fill_session.batch_id:
            messagebox.showinfo("No Errors", "No batch error log is available.")
            return
        path = os.path.expanduser(f"~/Documents/Typestra/Errors/{self.smart_fill_session.batch_id}.csv")
        if os.path.isfile(path):
            messagebox.showinfo("Error Log", f"Error log file:\n{path}")
        else:
            messagebox.showinfo("Error Log", "No error log file found.")

    def retry_failed_rows(self):
        if not self.smart_fill_session.batch_id:
            messagebox.showwarning("Retry", "No failed rows available to retry.")
            return
        saved_context = self.smart_fill_session.browser_context or BrowserContext().capture_context()
        current_context = BrowserContext().capture_context()
        context_status = self.context_verifier.verify_context(saved_context)
        if context_status != "match":
            proceed = self.context_verifier.show_context_warning(saved_context, current_context)
            if not proceed:
                return
        error_path = os.path.expanduser(f"~/Documents/Typestra/Errors/{self.smart_fill_session.batch_id}.csv")
        if not os.path.isfile(error_path):
            messagebox.showinfo("Retry", "No error file found.")
            return
        errors = self.retry_manager.load_error_log(error_path)
        if not errors:
            messagebox.showinfo("Retry", "No retryable rows found.")
            return
        mapping = {"fields": self.smart_fill_session.field_mappings, "auto_advance": self.smart_fill_session.auto_advance.__dict__}
        self.smart_fill_session = self.retry_manager.create_retry_session(errors, mapping)
        self.smart_fill_session.csv_filename = "retry_errors.csv"
        self.show_field_mapping_screen()
        messagebox.showinfo("Retry Ready", f"Loaded {len(errors)} failed rows. Click Start Filling to retry.")

    def pause_batch(self):
        if not self.smart_fill_session.is_running:
            return
        if self.smart_fill_session.is_paused:
            self.smart_fill_session.resume()
            if hasattr(self, "pause_btn"):
                self.pause_btn.config(text="Pause")
        else:
            self.smart_fill_session.pause()
            if hasattr(self, "pause_btn"):
                self.pause_btn.config(text="Resume")

    def skip_current_row(self):
        self.smart_fill_session.next_row_manual()
        self.refresh_active_filling_screen()

    def stop_batch(self):
        self.smart_fill_session.stop()
        self.status_label.config(text="Smart Fill stopped.")

    def start_or_resume_smart_fill(self):
        if self.smart_fill_session.csv_data is None:
            self.notebook.select(self.smart_fill_frame)
            self.show_import_screen()
            return
        if self.smart_fill_session.is_running:
            self.smart_fill_session.resume()
        else:
            self.notebook.select(self.smart_fill_frame)
            self.start_batch_execution()

    def start_smart_fill_only(self):
        """
        Hotkey action for Cmd/Ctrl+Shift+F:
        start Smart Fill if idle; never toggles pause/resume or stop.
        """
        if self.smart_fill_session.is_running:
            self.status_label.config(text="Smart Fill already running.")
            return
        self.start_or_resume_smart_fill()

    def next_row_manual_smart_fill(self):
        if self.smart_fill_session.is_running:
            self.smart_fill_session.next_row_manual()
            self.refresh_active_filling_screen()

    def reset_to_row_zero(self):
        self.smart_fill_session.reset()
        if hasattr(self, "row_counter_label"):
            self.refresh_active_filling_screen()

    def mark_current_row_error(self):
        if not self.smart_fill_session.is_running:
            return
        self.smart_fill_session.log_error(self.smart_fill_session.current_row, "manual_marked_error")
        self.smart_fill_session.next_row_manual()
        self.refresh_active_filling_screen()

    def toggle_demo_mode(self):
        if self.demo_mode.enabled:
            self.demo_mode.disable()
            self.status_label.config(text="Demo mode disabled.")
            return

        choice = simpledialog.askstring("Demo Mode", "Dataset: candidates, crm, invoices", initialvalue="candidates")
        demo_type = (choice or "candidates").strip().lower()
        if demo_type not in {"candidates", "crm", "invoices"}:
            demo_type = "candidates"
        self.demo_mode.enable(self.root, demo_type)
        result = self.smart_fill_session.load_demo_csv(demo_type=demo_type)
        if not result.get("success"):
            messagebox.showerror("Demo Load Error", result.get("error", "Could not load demo dataset"))
            return
        self.status_label.config(text=f"Demo mode enabled ({demo_type}).")
        self.notebook.select(self.smart_fill_frame)
        self.show_field_mapping_screen()

    def check_for_interrupted_session(self):
        """
        Check for interrupted session on app start.
        """
        resume_prompt = ResumePrompt()
        saved_state = resume_prompt.check_for_interrupted_session()
        if not saved_state:
            return

        action = resume_prompt.show_resume_dialog(saved_state, parent=self.root)
        if action == "resume":
            restored = self.restore_smart_fill_session(saved_state)
            if restored:
                self.notebook.select(1)
                messagebox.showinfo(
                    "Session Restored",
                    f"Ready to resume from row {saved_state['current_row'] + 1}",
                )

        # Delete recovery file regardless of action.
        resume_prompt.delete_recovery_file()

    def on_wake_from_sleep(self):
        """
        Called when Mac wakes from sleep.
        """
        if hasattr(self, "smart_fill_session") and self.smart_fill_session.is_running:
            self.smart_fill_session.save_state_to_disk()
            self.smart_fill_session.stop()
            messagebox.showwarning(
                "Batch Interrupted",
                "Smart Fill was interrupted by system sleep.\n\n"
                "Progress has been saved. Restart Typestra to resume.",
            )

    def restore_smart_fill_session(self, saved_state):
        """
        Restore Smart Fill session from saved state.
        """
        csv_file = saved_state.get("csv_file", "")
        if not csv_file:
            return False
        if not os.path.exists(csv_file):
            messagebox.showerror(
                "Resume Failed",
                f"CSV file not found:\n{csv_file}\n\n"
                "The file may have been moved or deleted.\n"
                "Recovery state has been cleared.",
            )
            ResumePrompt().delete_recovery_file()
            return False
        session = SmartFillSession()
        result = session.load_csv(csv_file)
        if not result.get("success"):
            messagebox.showerror(
                "Resume Failed",
                f"Could not reload CSV file:\n{csv_file}\n\n"
                f"Error: {result.get('error', 'Unknown error')}",
            )
            ResumePrompt().delete_recovery_file()
            return False

        session.field_mappings = saved_state.get("field_mappings", [])
        session.auto_advance_config = saved_state.get("auto_advance_config", {})
        session.current_row = int(saved_state.get("current_row", 0))
        session.batch_id = saved_state.get("batch_id")
        session.browser_context = saved_state.get("browser_context")
        self.smart_fill_session = session
        self.show_field_mapping_screen()
        return True

    def show_resume_ready_screen(self, saved_state):
        self.show_field_mapping_screen()
        next_row = int(saved_state.get("current_row", 0)) + 1
        total_rows = int(saved_state.get("total_rows", 0))
        self.status_label.config(
            text=f"Resume ready: row {next_row} of {total_rows}. Click Start Filling to continue."
        )
        messagebox.showinfo(
            "Resume Ready",
            f"Smart Fill session restored.\nReady to resume from row {next_row}.",
        )

    def _on_close(self):
        """Persist settings and destroy the window."""
        self.save_settings()
        if self.smart_fill_session.is_running:
            self.smart_fill_session.save_state_to_disk()
        if self.demo_mode.enabled:
            self.demo_mode.disable()
        self.root.destroy()

    def _flash_status(self, message: str, ms: int = 1800):
        """Show a brief status message, then restore the previous line."""
        if self._status_flash_after_id is not None:
            try:
                self.root.after_cancel(self._status_flash_after_id)
            except tk.TclError:
                pass
        baseline = self.status_label.cget("text")
        self.status_label.config(text=message)

        def restore():
            self._status_flash_after_id = None
            self.status_label.config(text=baseline)

        self._status_flash_after_id = self.root.after(ms, restore)

    def _shortcut_paste_bindtag(self, event):
        """Runs before Text class: block paste while automation is typing."""
        if self.is_typing:
            return "break"
        return None

    def _paste_event_block_if_typing(self, event):
        """Block context-menu / programmatic paste while automation is typing."""
        if self.is_typing:
            return "break"
        return None

    def _shortcut_paste_root(self, event):
        """Paste clipboard into the text box when focus is not on the text widget."""
        if self.is_typing:
            return "break"
        if self.root.focus_get() == self.text_input:
            return None
        try:
            clip = self.root.clipboard_get()
        except tk.TclError:
            clip = ""
        self.text_input.focus_set()
        self.text_input.insert(tk.INSERT, clip)
        self.update_stats()
        self._flash_status("Pasted from clipboard (shortcut)")
        return "break"

    def _shortcut_clear(self, event):
        if self.is_typing:
            return "break"
        self.clear_text()
        self._flash_status("Cleared text (shortcut)")
        return "break"

    def _shortcut_start(self, event):
        if self.is_typing:
            return "break"
        if self.text_input.get("1.0", tk.END).strip():
            self._flash_status("Starting… (shortcut)")
        self.start_typing()
        return "break"

    def _shortcut_quit(self, event):
        self._on_close()
        return "break"

    def _setup_keyboard_shortcuts(self):
        """
        Window-level shortcuts using the Command key on macOS and Control elsewhere.
        text_input uses a leading bindtag so we can block paste during typing and
        map Clear/Start without relying on focus being elsewhere.
        """
        m = self._mod
        seq_v = f"<{m}-v>"
        seq_k = f"<{m}-k>"
        seq_ret = f"<{m}-Return>"
        seq_q = f"<{m}-q>"
        seq_sf_start = f"<{m}-Shift-F>"
        seq_sf_next = f"<{m}-Shift-N>"
        seq_sf_pause = f"<{m}-Shift-P>"
        seq_sf_stop = f"<{m}-Shift-S>"
        seq_sf_reset = f"<{m}-Shift-R>"
        seq_sf_error = f"<{m}-Shift-E>"
        seq_sf_demo = f"<{m}-Shift-D>"

        w = self.text_input
        w.bindtags(("AutoFlowShortcuts",) + w.bindtags())

        self.root.bind_class("AutoFlowShortcuts", seq_v, self._shortcut_paste_bindtag)
        self.root.bind_class("AutoFlowShortcuts", "<<Paste>>", self._paste_event_block_if_typing)
        self.root.bind_class("AutoFlowShortcuts", seq_k, self._shortcut_clear)
        self.root.bind_class("AutoFlowShortcuts", seq_ret, self._shortcut_start)
        self.root.bind_class("AutoFlowShortcuts", seq_q, self._shortcut_quit)

        self.root.bind(seq_v, self._shortcut_paste_root)
        self.root.bind(seq_k, self._shortcut_clear)
        self.root.bind(seq_ret, self._shortcut_start)
        self.root.bind(seq_q, self._shortcut_quit)
        self.root.bind(seq_sf_start, lambda e: (self.start_smart_fill_only(), "break")[1])
        self.root.bind(seq_sf_next, lambda e: (self.next_row_manual_smart_fill(), "break")[1])
        self.root.bind(seq_sf_pause, lambda e: (self.pause_batch(), "break")[1])
        self.root.bind(seq_sf_stop, lambda e: (self.stop_batch(), "break")[1])
        self.root.bind(seq_sf_reset, lambda e: (self.reset_to_row_zero(), "break")[1])
        self.root.bind(seq_sf_error, lambda e: (self.mark_current_row_error(), "break")[1])
        self.root.bind(seq_sf_demo, lambda e: (self.toggle_demo_mode(), "break")[1])

    def register_smart_fill_hotkeys(self):
        """Smart Fill shortcuts are registered in _setup_keyboard_shortcuts."""
        return

    def _on_slider_released(self, event=None):
        """Save after user releases a WPM / countdown / humanization slider."""
        self._on_settings_changed()

    def _on_mode_changed(self):
        """Mode radio changed: update UI and persist."""
        self.switch_mode()

    def _on_settings_changed(self):
        """Persist when any saved control changes (not during load)."""
        if self._loading_settings:
            return
        self.save_settings()

    def _clamp_int(self, value, lo, hi, default):
        try:
            v = int(value)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    def load_settings(self):
        """Load ~/.autoflow/settings.json if present and apply to UI; else defaults."""
        self._loading_settings = True
        try:
            data = {}
            if os.path.isfile(SETTINGS_PATH):
                try:
                    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, json.JSONDecodeError):
                    data = {}

            wpm = self._clamp_int(data.get("wpm"), 30, 80, 50)
            countdown = self._clamp_int(data.get("countdown"), 3, 10, 5)
            human = self._clamp_int(data.get("humanization"), 1, 3, 2)

            self.wpm_var.set(wpm)
            self.countdown_var.set(countdown)
            self.human_var.set(human)

            self.variation_var.set(bool(data.get("variation", True)))
            self.thinking_var.set(bool(data.get("thinking", True)))
            self.punctuation_var.set(bool(data.get("punctuation", True)))
            self.typos_var.set(bool(data.get("typos", True)))

            mode = data.get("mode", "text")
            if mode not in ("text", "spreadsheet"):
                mode = "text"
            self.mode_var.set(mode)

            self.add_totals_var.set(bool(data.get("add_totals", False)))

            nav = data.get("nav", "tab")
            if nav not in ("tab", "enter"):
                nav = "tab"
            self.nav_var.set(nav)

            self.load_smart_fill_settings()
            self.sf_global_auto_var.set(bool(self.smart_fill_settings.get("enabled", True)))
            self.sf_global_delay_var.set(int(self.smart_fill_settings.get("delay_seconds", 3)))
            self.sf_global_checkpoint_var.set(int(self.smart_fill_settings.get("checkpoint_every", 5)))
            self.sf_global_timeout_var.set(int(self.smart_fill_settings.get("timeout_seconds", 10)))
            self.sf_global_demo_var.set(bool(self.smart_fill_settings.get("demo_enabled", False)))

            geom = data.get("geometry")
            if isinstance(geom, str) and geom.strip():
                try:
                    self.root.geometry(geom.strip())
                except tk.TclError:
                    pass

            self.update_wpm_label(str(self.wpm_var.get()))
            self.update_countdown_label(str(self.countdown_var.get()))
            self.update_human_label(str(self.human_var.get()))

            self.switch_mode()
        finally:
            self._loading_settings = False

    def save_settings(self):
        """Write current preferences and window geometry to settings.json."""
        try:
            os.makedirs(AUTOFLOW_DIR, mode=0o700, exist_ok=True)
            payload = {
                "wpm": int(self.wpm_var.get()),
                "humanization": int(self.human_var.get()),
                "variation": bool(self.variation_var.get()),
                "thinking": bool(self.thinking_var.get()),
                "punctuation": bool(self.punctuation_var.get()),
                "typos": bool(self.typos_var.get()),
                "mode": self.mode_var.get(),
                "countdown": int(self.countdown_var.get()),
                "add_totals": bool(self.add_totals_var.get()),
                "nav": self.nav_var.get(),
                "geometry": self.root.geometry(),
            }
            tmp_path = SETTINGS_PATH + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, SETTINGS_PATH)
            os.makedirs(os.path.dirname(SMART_FILL_SETTINGS_PATH), exist_ok=True)
            sf_payload = {
                "enabled": bool(self.sf_global_auto_var.get()),
                "delay_seconds": int(self.sf_global_delay_var.get()),
                "checkpoint_every": int(self.sf_global_checkpoint_var.get()),
                "timeout_seconds": int(self.sf_global_timeout_var.get()),
                "demo_enabled": bool(self.sf_global_demo_var.get()),
            }
            with open(SMART_FILL_SETTINGS_PATH, "w", encoding="utf-8") as sf:
                json.dump(sf_payload, sf, indent=2)
            self.smart_fill_settings = sf_payload
        except OSError:
            pass

    def switch_mode(self):
        """Switch between text and spreadsheet mode"""
        mode = self.mode_var.get()
        
        if mode == "text":
            # Text mode settings
            self.text_frame.config(text="Your Content")
            self.help_label.config(text="")
            
            # Hide spreadsheet-specific elements
            self.import_button.grid_forget()
            self.sheet_settings_frame.grid_forget()
            # Show Extract from Image (Pro)
            self.extract_image_btn.pack(side=tk.LEFT, padx=(10, 0))
            
            # Clear and show example text
            self.text_input.delete("1.0", tk.END)
            self.update_stats()
            
        else:  # spreadsheet
            # Spreadsheet mode settings
            self.text_frame.config(text="Spreadsheet Data (CSV Format)")
            self.help_label.config(text="Format: Column1,Column2,Column3")
            
            # Show spreadsheet-specific elements
            self.import_button.grid(row=2, column=0, pady=(5, 0), sticky=tk.W)
            self.sheet_settings_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 5))
            # Hide Extract from Image (text mode only)
            self.extract_image_btn.pack_forget()
            
            # Clear and show example CSV
            self.text_input.delete("1.0", tk.END)
            self.text_input.insert("1.0", "Name,Age,City\nJohn,25,New York\nSarah,30,Los Angeles")
            self.update_stats()

        if not self._loading_settings:
            self.save_settings()

    def import_csv(self):
        """Import CSV file"""
        filename = filedialog.askopenfilename(
            title="Select CSV File",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        
        if filename:
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.text_input.delete("1.0", tk.END)
                self.text_input.insert("1.0", content)
                self.update_stats()
                self.status_label.config(text=f"✓ Imported: {filename.split('/')[-1]}")
            except Exception as e:
                messagebox.showerror("Import Error", f"Could not read file: {str(e)}")
        
    def update_wpm_label(self, value):
        wpm = int(float(value))
        self.wpm_label.config(text=f"{wpm} WPM")
        
    def update_countdown_label(self, value):
        seconds = int(float(value))
        self.countdown_label.config(text=f"{seconds} seconds")
        
    def update_human_label(self, value):
        level = int(float(value))
        labels = {1: "Low", 2: "Medium", 3: "High"}
        self.human_label.config(text=labels[level])
        
    def update_stats(self, event=None):
        text = self.text_input.get("1.0", tk.END).strip()
        
        if self.mode_var.get() == "text":
            words = len(text.split()) if text else 0
            chars = len(text)
            self.stats_label.config(text=f"{words} words, {chars} characters")
        else:  # spreadsheet
            try:
                reader = csv.reader(io.StringIO(text))
                rows = list(reader)
                cells = sum(len(row) for row in rows)
                self.stats_label.config(text=f"{len(rows)} rows, {cells} cells")
            except:
                self.stats_label.config(text="Invalid CSV format")
        
    def clear_text(self):
        self.text_input.delete("1.0", tk.END)
        self.update_stats()

    def extract_from_image(self):
        """Pro: Extract text from image via OCR and insert into text box."""
        if not OCR_AVAILABLE:
            messagebox.showerror(
                "OCR Not Available",
                "OCR dependencies are not installed.\n\nInstall with: pip install pytesseract Pillow\n\nYou also need Tesseract installed (e.g. brew install tesseract on macOS)."
            )
            return
        exts = OCREngine.get_supported_formats()
        # File dialog: image types only (no PDF in picker to avoid confusion, or include and handle in validation)
        filetypes = [
            ("Image files", " ".join("*" + e for e in exts if e != ".pdf")),
            ("All files", "*.*"),
        ]
        path = filedialog.askopenfilename(
            title="Select image to extract text from",
            filetypes=filetypes,
        )
        if not path:
            return
        # Validate file exists
        if not os.path.isfile(path):
            messagebox.showerror("Error", "File not found.")
            return
        # Validate extension
        ext = os.path.splitext(path)[1].lower()
        if ext not in OCREngine.get_supported_formats():
            messagebox.showerror("Error", f"Unsupported file type: {ext}\nSupported: {', '.join(OCREngine.get_supported_formats())}")
            return
        if ext == ".pdf":
            messagebox.showerror("Error", "PDF support coming soon. Please use an image file (.jpg, .png, .gif, .bmp).")
            return
        # Validate file size (10MB)
        try:
            size = os.path.getsize(path)
        except OSError:
            messagebox.showerror("Error", "Could not read file size.")
            return
        if size > OCR_MAX_FILE_BYTES:
            messagebox.showerror("Error", f"File too large (max {OCR_MAX_FILE_BYTES // (1024*1024)}MB).")
            return
        self.status_label.config(text="Extracting text...")
        self.extract_image_btn.config(state=tk.DISABLED)
        result_holder = []

        def do_ocr():
            try:
                text = OCREngine.extract_text(path)
                result_holder.append(("ok", text))
            except Exception as e:
                result_holder.append(("err", str(e)))
            self.root.after(0, _on_ocr_finished)

        threading.Thread(target=do_ocr, daemon=True).start()

        def _on_ocr_finished():
            self.extract_image_btn.config(state=tk.NORMAL)
            if not result_holder:
                self.status_label.config(text="OCR failed.")
                return
            status, value = result_holder[0]
            if status == "err":
                messagebox.showerror("OCR Error", value)
                self.status_label.config(text="OCR failed.")
                return
            # Clear existing text, normalize, insert
            self.text_input.delete("1.0", tk.END)
            normalized = TypingEngine.normalize_special_chars(value)
            self.text_input.insert("1.0", normalized)
            self.update_stats()
            count = len(normalized)
            self.status_label.config(text=f"✓ Extracted {count} characters from image. Edit if needed, then Start.")
        
    def start_typing(self):
        text = self.text_input.get("1.0", tk.END).strip()
        
        if not text:
            messagebox.showwarning("No Content", "Please paste some content first!")
            return
            
        # Disable start button, enable pause and stop buttons
        self.start_button.config(state=tk.DISABLED)
        self.pause_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.NORMAL)
        self.clear_button.config(state=tk.DISABLED)
        
        # Spreadsheet mode: optionally add totals row with SUM formulas
        if self.mode_var.get() == "spreadsheet" and self.add_totals_var.get():
            numeric_cols = SpreadsheetCalculator.detect_numeric_columns(text)
            if numeric_cols:
                names = ", ".join(name for _, name in numeric_cols)
                msg = f"Found {len(numeric_cols)} numeric column(s): {names}.\n\nAdd SUM formulas in a totals row?"
                if messagebox.askyesno("Add totals row?", msg):
                    text = SpreadsheetCalculator.add_totals_row(text, numeric_cols)
                    # Update text box so user sees the result
                    self.text_input.delete("1.0", tk.END)
                    self.text_input.insert("1.0", text)
                    self.update_stats()
            else:
                messagebox.showinfo("Add totals row", "No numeric columns found. Typing without totals row.")

        # Start typing in a separate thread
        self.is_typing = True
        self.should_stop = False
        self.is_paused = False

        if self.mode_var.get() == "text":
            thread = threading.Thread(target=self._run_type_text, args=(text,))
        else:
            thread = threading.Thread(
                target=self._run_type_spreadsheet, args=(text,)
            )
        thread.daemon = True
        thread.start()
        
    def stop_typing(self):
        self.should_stop = True
        self.is_paused = False
        self.status_label.config(text="Stopping...")
    
    def toggle_pause(self):
        """Toggle between pause and resume"""
        if self.is_paused:
            # Resume
            self.is_paused = False
            self.pause_button.config(text="⏸ Pause")
            self.status_label.config(text="▶ Resuming... Click back to your document NOW!")
            # Give user 2 seconds to click back to document
            time.sleep(2)
            self.status_label.config(text="⌨️ Typing resumed...")
        else:
            # Pause
            self.is_paused = True
            self.pause_button.config(text="▶ Resume")
            self.status_label.config(text="⏸ PAUSED - Click Resume or press F8, then click back to your document")
        
    def on_window_focus(self, event):
        """Auto-pause when user clicks on AutoFlow window"""
        if self.is_typing and not self.is_paused:
            self.toggle_pause()
            # Show helpful message
            self.status_label.config(text="⏸ Auto-paused (you clicked AutoFlow). Click RESUME or press F8 to continue!")
        if self.smart_fill_session.preflight_active:
            return
        if self.smart_fill_session.is_running and not self.smart_fill_session.is_paused:
            self.smart_fill_session.pause()
            self.status_label.config(text="Typestra paused (you switched apps/window focus).")
    
    def setup_hotkey_listener(self):
        """Setup global hotkeys for pause/resume and Smart Fill controls."""
        self._pressed_keys = set()

        key_map = {
            "f": self.start_smart_fill_only,
            "n": self.next_row_manual_smart_fill,
            "p": self.pause_batch,
            "s": self.stop_batch,
            "r": self.reset_to_row_zero,
            "e": self.mark_current_row_error,
            "d": self.toggle_demo_mode,
        }

        def normalize(key):
            try:
                return key.char.lower()
            except Exception:
                return str(key)

        def on_press(key):
            try:
                if key == keyboard.Key.f8 and self.is_typing:
                    # Toggle pause when F8 is pressed
                    self.root.after(0, self.toggle_pause)
                self._pressed_keys.add(normalize(key))
                cmd_pressed = (
                    "Key.cmd" in self._pressed_keys
                    or "Key.cmd_l" in self._pressed_keys
                    or "Key.cmd_r" in self._pressed_keys
                    or "Key.ctrl" in self._pressed_keys
                    or "Key.ctrl_l" in self._pressed_keys
                    or "Key.ctrl_r" in self._pressed_keys
                )
                shift_pressed = (
                    "Key.shift" in self._pressed_keys
                    or "Key.shift_l" in self._pressed_keys
                    or "Key.shift_r" in self._pressed_keys
                )
                if cmd_pressed and shift_pressed:
                    char = None
                    try:
                        char = key.char.lower()
                    except Exception:
                        pass
                    if char in key_map:
                        self.root.after(0, key_map[char])
            except:
                pass

        def on_release(key):
            try:
                k = normalize(key)
                if k in self._pressed_keys:
                    self._pressed_keys.remove(k)
            except Exception:
                pass
        
        # Start listener in background thread
        self.listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.listener.daemon = True
        self.listener.start()
    
    def _run_type_text(self, text: str) -> None:
        """Thread target: build config, run TypingEngine.type_text, then reset UI."""
        config = TypingConfig(
            wpm=self.wpm_var.get(),
            humanization_level=self.human_var.get(),
            speed_variation=self.variation_var.get(),
            thinking_pauses=self.thinking_var.get(),
            punctuation_pauses=self.punctuation_var.get(),
            typos_enabled=self.typos_var.get(),
            mode=self.mode_var.get(),
            countdown_seconds=self.countdown_var.get(),
        )
        engine = TypingEngine(
            config,
            should_stop=lambda: self.should_stop,
            is_paused=lambda: self.is_paused,
            on_status=lambda s: self.root.after(0, lambda: self.status_label.config(text=s)),
        )
        try:
            engine.type_text(text)
            if not self.should_stop:
                self.root.after(
                    0,
                    lambda: self.reset_ui(
                        "✅ Typing complete! Apply formatting in your document as needed."
                    ),
                )
            else:
                self.root.after(0, lambda: self.reset_ui("Stopped by user"))
        except pyautogui.FailSafeException:
            self.root.after(
                0,
                lambda: self.reset_ui("🛑 Emergency stop - mouse moved to corner"),
            )
        except Exception as e:
            self.root.after(0, lambda: self.reset_ui(f"❌ Error: {str(e)}"))

    def _run_type_spreadsheet(self, csv_text: str) -> None:
        """Thread target: parse CSV, build config, run TypingEngine.type_spreadsheet, then reset UI."""
        try:
            reader = csv.reader(io.StringIO(csv_text))
            rows = list(reader)
        except Exception as e:
            self.root.after(
                0,
                lambda: self.reset_ui(f"❌ CSV parsing error: {str(e)}"),
            )
            return
        if not rows:
            self.root.after(0, lambda: self.reset_ui("❌ No data to type"))
            return
        config = TypingConfig(
            wpm=self.wpm_var.get(),
            humanization_level=self.human_var.get(),
            speed_variation=self.variation_var.get(),
            thinking_pauses=self.thinking_var.get(),
            punctuation_pauses=self.punctuation_var.get(),
            typos_enabled=self.typos_var.get(),
            mode=self.mode_var.get(),
            countdown_seconds=self.countdown_var.get(),
        )
        engine = TypingEngine(
            config,
            should_stop=lambda: self.should_stop,
            is_paused=lambda: self.is_paused,
            on_status=lambda s: self.root.after(0, lambda: self.status_label.config(text=s)),
        )
        try:
            engine.type_spreadsheet(rows)
            if not self.should_stop:
                self.root.after(
                    0,
                    lambda: self.reset_ui("✅ Spreadsheet complete!"),
                )
            else:
                self.root.after(0, lambda: self.reset_ui("Stopped by user"))
        except pyautogui.FailSafeException:
            self.root.after(
                0,
                lambda: self.reset_ui("🛑 Emergency stop - mouse moved to corner"),
            )
        except Exception as e:
            self.root.after(0, lambda: self.reset_ui(f"❌ Error: {str(e)}"))

    def reset_ui(self, status_message):
        self.is_typing = False
        self.should_stop = False
        self.is_paused = False
        self.start_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED, text="⏸ Pause")
        self.stop_button.config(state=tk.DISABLED)
        self.clear_button.config(state=tk.NORMAL)
        self.status_label.config(text=status_message)

def main():
    """Launch the GUI."""
    root = tk.Tk()
    app = AutoFlow(root)
    root.mainloop()


def run_cli_or_gui():
    """Parse argv: if --text given run CLI typing, else launch GUI."""
    parser = argparse.ArgumentParser(
        description="AutoFlow - Human-like typing automation"
    )
    parser.add_argument(
        "--text",
        type=str,
        default=None,
        help="Text to type (CLI mode; omit to launch GUI)",
    )
    parser.add_argument(
        "--wpm",
        type=int,
        default=50,
        help="Words per minute (default: 50)",
    )
    parser.add_argument(
        "--human-level",
        type=int,
        default=2,
        choices=[1, 2, 3],
        metavar="1|2|3",
        help="Humanization level: 1=Low, 2=Medium, 3=High (default: 2)",
    )
    parser.add_argument(
        "--countdown",
        type=int,
        default=5,
        help="Countdown seconds before typing (default: 5)",
    )
    parser.add_argument(
        "--no-speed-variation",
        action="store_true",
        help="Disable speed variation",
    )
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        help="Disable thinking pauses",
    )
    parser.add_argument(
        "--no-punctuation",
        action="store_true",
        help="Disable punctuation pauses",
    )
    parser.add_argument(
        "--no-typos",
        action="store_true",
        help="Disable typos and corrections",
    )
    args = parser.parse_args()

    if args.text is not None:
        config = TypingConfig(
            wpm=args.wpm,
            humanization_level=args.human_level,
            speed_variation=not args.no_speed_variation,
            thinking_pauses=not args.no_thinking,
            punctuation_pauses=not args.no_punctuation,
            typos_enabled=not args.no_typos,
            mode="text",
            countdown_seconds=args.countdown,
        )
        engine = TypingEngine(config)
        try:
            engine.type_text(args.text)
            print("Typing complete.")
        except pyautogui.FailSafeException:
            print("Emergency stop - mouse moved to corner.")
        except KeyboardInterrupt:
            print("Interrupted.")
    else:
        main()


if __name__ == "__main__":
    run_cli_or_gui()
