"""
Browser/app context capture and verification helpers.
Supports macOS (AppleScript) and Windows (pywin32 + pywinauto).
"""

from __future__ import annotations

import logging
import platform
import subprocess
from datetime import datetime
from typing import Any, Dict, Optional

from tkinter import messagebox

logger = logging.getLogger(__name__)

_IS_MAC = platform.system() == "Darwin"
_IS_WIN = platform.system() == "Windows"

# Windows process name → canonical browser key
_WIN_BROWSER_MAP = {
    "chrome.exe": "chrome",
    "brave.exe": "brave",
    "firefox.exe": "firefox",
    "msedge.exe": "edge",
}

# macOS app name → canonical browser key
_MAC_BROWSER_MAP = {
    "Safari": "safari",
    "Google Chrome": "chrome",
    "Brave Browser": "brave",
    "Firefox": "firefox",
}


def _get_win_frontmost_process() -> str:
    """Return the exe name of the foreground window process on Windows."""
    try:
        import win32gui
        import win32process
        import psutil
        hwnd = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name().lower()
    except Exception as exc:
        logger.debug("Windows frontmost process lookup failed: %s", exc)
        return ""


def _get_win_browser_url(browser_type: str) -> Optional[str]:
    """
    Read the address bar URL from Chrome/Edge/Brave on Windows
    using the UI Automation accessibility tree via pywinauto.
    Returns None if unavailable or unsupported.
    """
    if browser_type not in ("chrome", "edge", "brave"):
        return None
    exe_map = {
        "chrome": "chrome.exe",
        "edge": "msedge.exe",
        "brave": "brave.exe",
    }
    try:
        from pywinauto import Desktop
        app_exe = exe_map[browser_type]
        # Find the address bar (accessible name varies by browser version)
        desktop = Desktop(backend="uia")
        windows = desktop.windows()
        for win in windows:
            try:
                proc_name = ""
                try:
                    import win32process
                    import psutil
                    _, pid = win32process.GetWindowThreadProcessId(win.handle)
                    proc_name = psutil.Process(pid).name().lower()
                except Exception:
                    pass
                if proc_name != app_exe:
                    continue
                # Address bar control names differ slightly across browsers
                for ctrl_name in ("Address and search bar", "Address bar", "Search or enter address"):
                    try:
                        addr = win.child_window(title=ctrl_name, control_type="Edit")
                        if addr.exists(timeout=0.3):
                            url = addr.get_value()
                            if url:
                                return url
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception as exc:
        logger.debug("pywinauto URL lookup failed: %s", exc)
    return None


class BrowserContext:
    """Capture frontmost app context and browser URL when available.
    Supports macOS via AppleScript and Windows via pywin32/pywinauto.
    """

    def _debug(self, message: str) -> None:
        logger.debug(message)

    def get_frontmost_app(self) -> str:
        """Return a canonical app/process name for the foreground window."""
        if _IS_MAC:
            script = (
                'tell application "System Events" '
                'to get name of first application process whose frontmost is true'
            )
            self._debug("Running AppleScript for frontmost app")
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True, check=False
            )
            self._debug(
                "Frontmost app AppleScript result: "
                f"returncode={result.returncode}, stdout={result.stdout.strip()!r}, "
                f"stderr={result.stderr.strip()!r}"
            )
            return result.stdout.strip()
        if _IS_WIN:
            proc = _get_win_frontmost_process()
            self._debug(f"Windows frontmost process: {proc!r}")
            return proc
        return ""

    def is_browser(self, app_name: str) -> bool:
        mac_browsers = set(_MAC_BROWSER_MAP.keys())
        win_browsers = set(_WIN_BROWSER_MAP.keys())
        return app_name in mac_browsers or app_name in win_browsers

    def get_browser_type(self) -> str:
        """Return canonical browser key for the current foreground window."""
        frontmost = self.get_frontmost_app()
        if _IS_MAC:
            browser_type = _MAC_BROWSER_MAP.get(frontmost, "other")
        elif _IS_WIN:
            browser_type = _WIN_BROWSER_MAP.get(frontmost, "other")
        else:
            browser_type = "other"
        self._debug(
            f"Mapped frontmost app to browser_type: app={frontmost!r} -> {browser_type!r}"
        )
        return browser_type

    def is_firefox(self) -> bool:
        return self.get_browser_type() == "firefox"

    def get_browser_display_name(self) -> str:
        browser_type = self.get_browser_type()
        display_names = {
            "safari": "Safari",
            "chrome": "Chrome",
            "brave": "Brave",
            "firefox": "Firefox",
            "edge": "Microsoft Edge",
            "other": "Unknown Browser",
        }
        return display_names.get(browser_type, "Unknown Browser")

    def is_supported_browser(self) -> bool:
        """Safari/Chrome/Brave/Edge are supported; Firefox is not for URL detection."""
        return self.get_browser_type() in ("safari", "chrome", "brave", "edge")

    def get_window_title(self) -> str:
        if _IS_MAC:
            script = (
                'tell application "System Events" '
                'to tell (first application process whose frontmost is true) '
                "to get name of front window"
            )
            self._debug("Running AppleScript for front window title")
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True, check=False
            )
            self._debug(
                "Window title AppleScript result: "
                f"returncode={result.returncode}, stdout={result.stdout.strip()!r}, "
                f"stderr={result.stderr.strip()!r}"
            )
            return result.stdout.strip()
        if _IS_WIN:
            try:
                import win32gui
                title = win32gui.GetWindowText(win32gui.GetForegroundWindow())
                self._debug(f"Windows window title: {title!r}")
                return title
            except Exception as exc:
                logger.debug("Windows window title lookup failed: %s", exc)
                return ""
        return ""

    def get_browser_url(self, app_name: str = "") -> Optional[str]:
        """
        Get current browser URL.
        - macOS: AppleScript (Safari, Chrome, Brave).
        - Windows: pywinauto accessibility tree (Chrome, Edge, Brave).
        - Firefox: unsupported on both platforms.
        Returns URL string or None if unavailable.
        """
        browser_type = self.get_browser_type()
        self._debug(
            f"get_browser_url requested; app_name={app_name!r}, resolved_browser_type={browser_type!r}"
        )

        if _IS_MAC:
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
                self._debug(
                    "Browser URL AppleScript result: "
                    f"returncode={result.returncode}, stdout={result.stdout.strip()!r}, "
                    f"stderr={result.stderr.strip()!r}"
                )
                if result.returncode == 0:
                    return result.stdout.strip()
                logger.warning("AppleScript failed: %s", result.stderr.strip())
                return None
            except subprocess.TimeoutExpired:
                logger.warning("AppleScript timed out")
                self._debug("Browser URL AppleScript timed out")
                return None
            except Exception as exc:  # pragma: no cover
                logger.warning("Failed to get browser URL: %s", exc)
                self._debug(f"Browser URL AppleScript exception: {type(exc).__name__}: {exc}")
                return None

        if _IS_WIN:
            if browser_type == "firefox":
                logger.warning("Firefox URL detection not supported on Windows, skipping")
                return None
            url = _get_win_browser_url(browser_type)
            self._debug(f"Windows browser URL result: {url!r}")
            return url

        return None

    def capture_context(self) -> Dict[str, Any]:
        app = self.get_frontmost_app()
        context = {
            "app": app,
            "window_title": self.get_window_title(),
            "url": self.get_browser_url() if self.is_browser(app) else None,
            "timestamp": datetime.now().isoformat(),
        }
        self._debug(f"capture_context result: {context}")
        return context


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
