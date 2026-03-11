"""
AutoFlow v3.0 - Typing & Spreadsheet Automation
Professional workflow automation with human-like typing patterns
Now with spreadsheet support for Excel and Google Sheets
"""

import argparse
import sys
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import pyautogui
import time
import random
import threading
import csv
import io
import os
import re
import unicodedata
from typing import List, Tuple

from typing_engine import TypingEngine, TypingConfig

# Try to import pynput for global hotkeys
try:
    from pynput import keyboard
    PYNPUT_AVAILABLE = True
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


class AutoFlow:
    def __init__(self, root):
        self.root = root
        self.root.title("AutoFlow v3.0 - Professional Workflow Automation")
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
        
        # Setup global hotkey listener if available
        if PYNPUT_AVAILABLE:
            self.setup_hotkey_listener()
        
        # Auto-pause when window gains focus (user clicked AutoFlow window)
        self.root.bind('<FocusIn>', self.on_window_focus)
        
        # Create UI
        self.create_ui()

    def create_ui(self):
        # Create a canvas with scrollbar for the entire interface
        canvas = tk.Canvas(self.root)
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
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
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
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
            command=self.switch_mode
        )
        text_radio.grid(row=0, column=0, sticky=tk.W, pady=5, padx=5)
        
        sheet_radio = ttk.Radiobutton(
            mode_frame,
            text="📊 Spreadsheet Mode (Excel, Google Sheets, CSV)",
            variable=self.mode_var,
            value="spreadsheet",
            command=self.switch_mode
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
            value="tab"
        ).pack(anchor=tk.W, pady=2)
        
        ttk.Radiobutton(
            self.sheet_settings_frame,
            text="Navigate with Enter (moves down, then right)",
            variable=self.nav_var,
            value="enter"
        ).pack(anchor=tk.W, pady=2)

        self.add_totals_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.sheet_settings_frame,
            text="☐ Add totals row (SUM formulas)",
            variable=self.add_totals_var
        ).pack(anchor=tk.W, pady=2)
        
        # Options checkboxes
        self.variation_var = tk.BooleanVar(value=True)
        self.variation_check = ttk.Checkbutton(
            settings_frame,
            text="✓ Speed variation (faster and slower bursts)",
            variable=self.variation_var
        )
        self.variation_check.grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(10, 2))
        
        self.thinking_var = tk.BooleanVar(value=True)
        self.thinking_check = ttk.Checkbutton(
            settings_frame,
            text="✓ Thinking pauses (random hesitations while typing)",
            variable=self.thinking_var
        )
        self.thinking_check.grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=2)
        
        self.punctuation_var = tk.BooleanVar(value=True)
        self.punctuation_check = ttk.Checkbutton(
            settings_frame,
            text="✓ Punctuation pauses (longer breaks after sentences)",
            variable=self.punctuation_var
        )
        self.punctuation_check.grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=2)
        
        self.typos_var = tk.BooleanVar(value=True)
        self.typos_check = ttk.Checkbutton(
            settings_frame,
            text="✓ Realistic typos & corrections (makes and fixes mistakes)",
            variable=self.typos_var
        )
        self.typos_check.grid(row=7, column=0, columnspan=2, sticky=tk.W, pady=2)
        
        # Button frame
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=5, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)
        button_frame.columnconfigure(2, weight=1)
        button_frame.columnconfigure(3, weight=1)
        
        self.start_button = ttk.Button(
            button_frame,
            text="▶ Start AutoFlow",
            command=self.start_typing
        )
        self.start_button.grid(row=0, column=0, padx=(0, 5), sticky=(tk.W, tk.E))
        
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
        
        self.instructions_text = """TEXT MODE: Paste your content (or use 📷 Extract from Image) → Click Start → Switch to target app
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
        
        # Initialize in text mode
        self.switch_mode()
        
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
    
    def setup_hotkey_listener(self):
        """Setup global F8 hotkey for pause/resume"""
        def on_press(key):
            try:
                if key == keyboard.Key.f8 and self.is_typing:
                    # Toggle pause when F8 is pressed
                    self.root.after(0, self.toggle_pause)
            except:
                pass
        
        # Start listener in background thread
        self.listener = keyboard.Listener(on_press=on_press)
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
