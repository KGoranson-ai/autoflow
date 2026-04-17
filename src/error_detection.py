"""
Error detection and logging for Smart Fill.
Supports macOS (AppleScript) and Windows (pywin32 + BrowserContext delegation).
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

_IS_MAC = platform.system() == "Darwin"
_IS_WIN = platform.system() == "Windows"


def _ed_debug(message: str) -> None:
    logger.debug(message)


class TimeoutDetector:
    """Detect likely form submission failures using OS-level signals."""

    BROWSER_APPS = {
        # macOS app names
        "Safari", "Google Chrome", "Firefox", "Brave Browser",
        # Windows process names (lowercase, matched after .lower())
        "chrome.exe", "brave.exe", "firefox.exe", "msedge.exe",
    }

    def __init__(self, typing_engine: Optional[Any] = None) -> None:
        self.typing_engine = typing_engine

    def detect_error(self, max_wait: int = 10) -> str:
        """
        Returns: success, timeout_error, or unknown.
        URL-based detection is limited in Firefox.
        """
        _ed_debug(f"detect_error start max_wait={max_wait}")
        if self.is_browser_active():
            browser_type = BrowserContext().get_browser_type()
            _ed_debug(f"Frontmost browser detected browser_type={browser_type}")
            if browser_type in ["safari", "chrome", "brave"]:
                try:
                    _ed_debug("Using URL-change detection path")
                    return self.detect_via_url_change(max_wait)
                except Exception as exc:
                    logger.warning("URL detection failed: %s", exc)
                    _ed_debug(f"URL detection exception: {type(exc).__name__}: {exc}")
            else:
                logger.info(
                    "Skipping URL detection for %s, using keystroke fallback",
                    browser_type,
                )
                _ed_debug(f"Unsupported browser_type={browser_type}, falling back to keystroke test")
        else:
            _ed_debug("Frontmost app is not recognized as browser, using keystroke fallback")
        return self.detect_via_keystroke_test(max_wait)

    def detect_via_url_change(self, max_wait: int) -> str:
        _ed_debug(f"detect_via_url_change start max_wait={max_wait}")
        initial_url = self.get_browser_url()
        _ed_debug(f"Initial URL: {initial_url!r}")
        start = time.time()
        time.sleep(max_wait)
        elapsed = time.time() - start
        _ed_debug(f"Waited for URL change: elapsed={elapsed:.2f}s")
        current_url = self.get_browser_url()
        _ed_debug(f"Current URL: {current_url!r}")
        if not initial_url or not current_url:
            _ed_debug("URL detection returned unknown (missing initial or current URL)")
            return "unknown"
        result = "success" if current_url != initial_url else "timeout_error"
        _ed_debug(f"URL detection result={result}")
        return result

    def detect_via_keystroke_test(self, max_wait: int) -> str:
        _ed_debug(f"detect_via_keystroke_test start max_wait={max_wait}")
        start = time.time()
        time.sleep(max_wait)
        elapsed = time.time() - start
        _ed_debug(f"Keystroke fallback waited elapsed={elapsed:.2f}s")
        if not self.typing_engine:
            _ed_debug("No typing_engine available -> unknown")
            return "unknown"
        result = self.typing_engine.send_key("backspace", test_mode=True)
        _ed_debug(f"typing_engine.send_key backspace returned {result!r}")
        if result == "accepted":
            _ed_debug("Fallback result=timeout_error (key accepted)")
            return "timeout_error"
        if result in ("blocked", "beep"):
            _ed_debug("Fallback result=success (key blocked/beep)")
            return "success"
        _ed_debug("Fallback result=unknown")
        return "unknown"

    def is_browser_active(self) -> bool:
        return self.get_frontmost_app_name() in self.BROWSER_APPS

    def get_frontmost_app_name(self) -> str:
        """Delegate to BrowserContext for cross-platform frontmost app detection."""
        return BrowserContext().get_frontmost_app()

    def get_browser_url(self) -> str:
        """Delegate to BrowserContext for cross-platform URL detection."""
        url = BrowserContext().get_browser_url()
        return url if url is not None else ""


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
        """Show a desktop notification. Uses plyer for cross-platform support."""
        text = f"Checkpoint: Verify row {row_num} submitted correctly"
        try:
            from plyer import notification
            notification.notify(
                title="Typestra",
                message=text,
                app_name="Typestra",
                timeout=5,
            )
        except Exception as exc:
            # Fallback: macOS osascript if plyer is unavailable
            logger.debug("plyer notification failed (%s), trying osascript fallback", exc)
            if _IS_MAC:
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
