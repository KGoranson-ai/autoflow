"""
Browser/app context capture and verification helpers.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from datetime import datetime
from typing import Any, Dict

from tkinter import messagebox

logger = logging.getLogger(__name__)


class BrowserContext:
    """Capture frontmost app context and browser URL when available."""

    def get_frontmost_app(self) -> str:
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

    def is_browser(self, app_name: str) -> bool:
        return app_name in {"Safari", "Google Chrome", "Brave Browser", "Firefox"}

    def get_browser_type(self) -> str:
        """
        Return browser type for current frontmost app.
        """
        frontmost = self.get_frontmost_app()
        browser_map = {
            "Safari": "safari",
            "Google Chrome": "chrome",
            "Brave Browser": "brave",
            "Firefox": "firefox",
        }
        return browser_map.get(frontmost, "other")

    def is_firefox(self) -> bool:
        return self.get_browser_type() == "firefox"

    def get_browser_display_name(self) -> str:
        browser_type = self.get_browser_type()
        display_names = {
            "safari": "Safari",
            "chrome": "Chrome",
            "brave": "Brave",
            "firefox": "Firefox",
            "other": "Unknown Browser",
        }
        return display_names.get(browser_type, "Unknown Browser")

    def is_supported_browser(self) -> bool:
        return self.get_browser_type() in ["safari", "chrome", "brave"]

    def get_window_title(self) -> str:
        if platform.system() != "Darwin":
            return ""
        script = (
            'tell application "System Events" '
            'to tell (first application process whose frontmost is true) '
            "to get name of front window"
        )
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, check=False
        )
        return result.stdout.strip()

    def get_browser_url(self, app_name: str = ""):
        """
        Get current URL via AppleScript.

        Returns: URL string or None if failed.
        """
        browser_type = self.get_browser_type()
        try:
            if browser_type == "safari":
                script = 'tell application "Safari" to get URL of front document'
            elif browser_type == "chrome":
                script = (
                    'tell application "Google Chrome" '
                    "to get URL of active tab of front window"
                )
            elif browser_type == "brave":
                script = (
                    'tell application "Brave Browser" '
                    "to get URL of active tab of front window"
                )
            elif browser_type == "firefox":
                logger.warning("Firefox URL detection not supported, skipping")
                return None
            else:
                return None

            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            logger.warning("AppleScript failed: %s", result.stderr.strip())
            return None
        except subprocess.TimeoutExpired:
            logger.warning("AppleScript timed out")
            return None
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to get browser URL: %s", exc)
            return None

    def capture_context(self) -> Dict[str, Any]:
        app = self.get_frontmost_app()
        return {
            "app": app,
            "window_title": self.get_window_title(),
            "url": self.get_browser_url() if self.is_browser(app) else None,
            "timestamp": datetime.now().isoformat(),
        }


class ContextVerifier:
    """Compare current context against saved context."""

    def __init__(self) -> None:
        self.browser_context = BrowserContext()

    def verify_context(self, saved_context: Dict[str, Any]) -> str:
        current = self.browser_context.capture_context()
        if current["app"] != saved_context.get("app"):
            return "different_app"
        if current.get("url") and saved_context.get("url"):
            if current["url"] != saved_context["url"]:
                return "different_url"
        return "match"

    def show_context_warning(self, saved: Dict[str, Any], current: Dict[str, Any]) -> bool:
        msg = (
            "Context Verification\n\n"
            f"Original: {saved.get('window_title', saved.get('app', 'Unknown'))}\n"
            f"Current:  {current.get('window_title', current.get('app', 'Unknown'))}\n\n"
            "Warning: You're on a different page/app.\n"
            "Continue anyway?"
        )
        return messagebox.askyesno("Typestra Context Warning", msg)
