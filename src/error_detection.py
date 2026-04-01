"""
Error detection and logging for Smart Fill.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import platform
import subprocess
import time
from datetime import datetime
from typing import Any, Optional

from browser_context import BrowserContext


logger = logging.getLogger(__name__)


class TimeoutDetector:
    """Detect likely form submission failures using OS-level signals."""

    BROWSER_APPS = {"Safari", "Google Chrome", "Firefox", "Brave Browser"}

    def __init__(self, typing_engine: Optional[Any] = None) -> None:
        self.typing_engine = typing_engine

    def detect_error(self, max_wait: int = 10) -> str:
        """
        Returns: success, timeout_error, or unknown.
        URL-based detection is limited in Firefox.
        """
        if self.is_browser_active():
            browser_type = BrowserContext().get_browser_type()
            if browser_type in ["safari", "chrome", "brave"]:
                try:
                    return self.detect_via_url_change(max_wait)
                except Exception as exc:
                    logger.warning("URL detection failed: %s", exc)
            else:
                logger.info(
                    "Skipping URL detection for %s, using keystroke fallback",
                    browser_type,
                )
        return self.detect_via_keystroke_test(max_wait)

    def detect_via_url_change(self, max_wait: int) -> str:
        initial_url = self.get_browser_url()
        time.sleep(max_wait)
        current_url = self.get_browser_url()
        if not initial_url or not current_url:
            return "unknown"
        return "success" if current_url != initial_url else "timeout_error"

    def detect_via_keystroke_test(self, max_wait: int) -> str:
        time.sleep(max_wait)
        if not self.typing_engine:
            return "unknown"
        result = self.typing_engine.send_key("backspace", test_mode=True)
        if result == "accepted":
            return "timeout_error"
        if result in ("blocked", "beep"):
            return "success"
        return "unknown"

    def is_browser_active(self) -> bool:
        return self.get_frontmost_app_name() in self.BROWSER_APPS

    def get_frontmost_app_name(self) -> str:
        if platform.system() != "Darwin":
            return ""
        script = (
            'tell application "System Events" '
            'to get name of first application process whose frontmost is true'
        )
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, check=False
        )
        return result.stdout.strip()

    def get_browser_url(self) -> str:
        app = self.get_frontmost_app_name()
        if app == "Safari":
            script = 'tell application "Safari" to get URL of front document'
        elif app in {"Google Chrome", "Brave Browser"}:
            script = f'tell application "{app}" to get URL of active tab of front window'
        elif app == "Firefox":
            return ""
        else:
            return ""

        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, check=False
        )
        return result.stdout.strip()


class CheckpointManager:
    """Pause every N rows for manual verification."""

    def __init__(self, every_n_rows: int = 5, pause_duration: int = 5, enabled: bool = True):
        self.every_n_rows = max(1, int(every_n_rows))
        self.pause_duration = max(1, int(pause_duration))
        self.enabled = enabled

    def should_pause_for_checkpoint(self, row_num: int) -> bool:
        if not self.enabled:
            return False
        return row_num > 0 and row_num % self.every_n_rows == 0

    def show_checkpoint_notification(self, row_num: int) -> None:
        text = f"Checkpoint: Verify row {row_num} submitted correctly"
        script = f'display notification "{text}" with title "Typestra"'
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


class ErrorLogger:
    """Write failed rows to a CSV log for one batch."""

    def __init__(self, batch_id: str):
        self.batch_id = batch_id
        self.filepath = os.path.expanduser(f"~/Documents/Typestra/Errors/{batch_id}.csv")
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["row_number", "data", "timestamp", "error_type", "notes"])

    def log_error(self, row_number: int, data: dict, error_type: str, notes: str = "") -> None:
        with open(self.filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    row_number,
                    json.dumps(data, ensure_ascii=False),
                    datetime.now().isoformat(),
                    error_type,
                    notes,
                ]
            )
