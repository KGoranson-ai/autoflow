"""
TabInjector — emit backend for multi-form mode.

Replaces pyautogui as the output layer for a TypingEngine instance.
Each TabInjector targets one specific browser tab by index and injects
characters/keys directly into it without requiring OS focus.

Platform support:
  macOS  — AppleScript + JavaScript injection into Chrome/Brave/Safari
  Windows — pywinauto UIA SendKeys into the specific tab's active element

JavaScript injection dispatches both 'input' and 'change' events so
React/Vue/Angular forms register the value change correctly.

Usage:
    injector = TabInjector(browser_type="chrome", tab_index=2)
    engine = TypingEngine(
        config,
        emit_character=injector.emit_character,
        emit_key=injector.emit_key,
    )
"""

from __future__ import annotations

import logging
import platform
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

_IS_MAC = platform.system() == "Darwin"
_IS_WIN = platform.system() == "Windows"

# ---------------------------------------------------------------------------
# JavaScript templates
# ---------------------------------------------------------------------------

# Inject a single character into the currently focused element.
# Dispatches input + change events for framework compatibility.
_JS_TYPE_CHAR = """
(function(ch) {{
    var el = document.activeElement;
    if (!el) return 'no_focus';
    var start = el.selectionStart;
    var end   = el.selectionEnd;
    if (start !== undefined && end !== undefined) {{
        var val = el.value || '';
        el.value = val.slice(0, start) + ch + val.slice(end);
        el.selectionStart = el.selectionEnd = start + ch.length;
    }} else {{
        el.value = (el.value || '') + ch;
    }}
    el.dispatchEvent(new Event('input',  {{bubbles: true, cancelable: true}}));
    el.dispatchEvent(new Event('change', {{bubbles: true, cancelable: true}}));
    return 'ok';
}})({char_json})
""".strip()

# Dispatch a keyboard key action into the focused element.
_JS_PRESS_KEY = """
(function(key) {{
    var el = document.activeElement;
    if (!el) return 'no_focus';
    var keyMap = {{
        'backspace': 'Backspace', 'tab': 'Tab', 'enter': 'Enter',
        'delete': 'Delete', 'escape': 'Escape',
        'up': 'ArrowUp', 'down': 'ArrowDown',
        'left': 'ArrowLeft', 'right': 'ArrowRight',
    }};
    var jsKey = keyMap[key.toLowerCase()] || key;

    // Handle backspace by modifying value directly for reliability
    if (jsKey === 'Backspace') {{
        var start = el.selectionStart;
        var end   = el.selectionEnd;
        if (start !== undefined) {{
            var val = el.value || '';
            if (start === end && start > 0) {{
                el.value = val.slice(0, start - 1) + val.slice(end);
                el.selectionStart = el.selectionEnd = start - 1;
            }} else if (start !== end) {{
                el.value = val.slice(0, start) + val.slice(end);
                el.selectionStart = el.selectionEnd = start;
            }}
            el.dispatchEvent(new Event('input',  {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
        }}
        return 'ok';
    }}

    // Tab: move focus to next element
    if (jsKey === 'Tab') {{
        var focusable = Array.from(document.querySelectorAll(
            'input, textarea, select, button, [tabindex]:not([tabindex="-1"])'
        )).filter(function(e) {{ return !e.disabled && e.offsetParent !== null; }});
        var idx = focusable.indexOf(el);
        if (idx >= 0 && idx < focusable.length - 1) {{
            focusable[idx + 1].focus();
        }}
        return 'ok';
    }}

    // Enter: submit form or dispatch event
    if (jsKey === 'Enter') {{
        el.dispatchEvent(new KeyboardEvent('keydown',  {{key: 'Enter', bubbles: true}}));
        el.dispatchEvent(new KeyboardEvent('keypress', {{key: 'Enter', bubbles: true}}));
        el.dispatchEvent(new KeyboardEvent('keyup',    {{key: 'Enter', bubbles: true}}));
        return 'ok';
    }}

    el.dispatchEvent(new KeyboardEvent('keydown', {{key: jsKey, bubbles: true}}));
    el.dispatchEvent(new KeyboardEvent('keyup',   {{key: jsKey, bubbles: true}}));
    return 'ok';
}})({key_json})
""".strip()


# ---------------------------------------------------------------------------
# macOS AppleScript runners
# ---------------------------------------------------------------------------

def _mac_execute_js(browser_type: str, tab_index: int, js: str) -> Optional[str]:
    """
    Run JavaScript in a specific browser tab on macOS via AppleScript.
    tab_index is 1-based (matches AppleScript tab numbering).
    Returns the JS return value as a string, or None on failure.
    """
    # Escape backslashes and double-quotes for AppleScript string embedding
    js_escaped = js.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")

    if browser_type == "safari":
        script = (
            f'tell application "Safari" to do JavaScript "{js_escaped}" '
            f'in tab {tab_index} of front window'
        )
    elif browser_type in ("chrome", "brave"):
        app_name = "Google Chrome" if browser_type == "chrome" else "Brave Browser"
        script = (
            f'tell application "{app_name}" to execute tab {tab_index} '
            f'of front window javascript "{js_escaped}"'
        )
    else:
        logger.warning("TabInjector: unsupported browser %r on macOS", browser_type)
        return None

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, check=False, timeout=3,
    )
    if result.returncode != 0:
        logger.debug(
            "TabInjector AppleScript error: %s", result.stderr.strip()
        )
        return None
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Windows pywinauto runner
# ---------------------------------------------------------------------------

def _win_send_char_to_tab(browser_type: str, tab_index: int, ch: str) -> bool:
    """
    Send a character to a specific Chrome/Edge/Brave tab on Windows
    using pywinauto UIA. tab_index is 1-based.
    Returns True on success.
    """
    exe_map = {"chrome": "chrome.exe", "edge": "msedge.exe", "brave": "brave.exe"}
    target_exe = exe_map.get(browser_type)
    if not target_exe:
        logger.warning("TabInjector: unsupported browser %r on Windows", browser_type)
        return False

    try:
        from pywinauto import Desktop
        import win32process
        import psutil

        desktop = Desktop(backend="uia")
        browser_wins = []
        for win in desktop.windows():
            try:
                _, pid = win32process.GetWindowThreadProcessId(win.handle)
                if psutil.Process(pid).name().lower() == target_exe:
                    browser_wins.append(win)
            except Exception:
                continue

        if not browser_wins:
            logger.warning("TabInjector: no %s windows found", target_exe)
            return False

        # Use the first (frontmost) browser window; tab switching by index
        # on Windows is done by activating the Nth tab via Ctrl+N shortcut
        # We keep focus on Typestra's own window and inject via COM instead.
        # For now, find the active document area and SendKeys to it.
        win = browser_wins[0]
        doc = win.child_window(control_type="Document")
        if doc.exists(timeout=0.5):
            doc.set_focus()
            doc.type_keys(ch, with_spaces=True, pause=0)
            return True
    except Exception as exc:
        logger.debug("TabInjector Windows inject failed: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class TabInjector:
    """
    Emit backend that injects characters into a specific browser tab.

    Args:
        browser_type: Canonical browser key: 'chrome', 'brave', 'safari', 'edge'
        tab_index:    1-based tab number within the front browser window.
        settle_ms:    Extra settle time in ms after each injection (default 8ms).
                      Increase if the target form is slow to process input events.
    """

    def __init__(
        self,
        browser_type: str,
        tab_index: int,
        settle_ms: int = 8,
    ) -> None:
        self.browser_type = browser_type.lower()
        self.tab_index = tab_index
        self._settle = settle_ms / 1000.0
        self._error_count = 0
        self._MAX_ERRORS = 10  # Stop injecting after this many consecutive failures

    # ------------------------------------------------------------------
    # emit_character — plug into TypingEngine(emit_character=...)
    # ------------------------------------------------------------------

    def emit_character(self, ch: str) -> None:
        """Inject a single character into the target tab."""
        if self._error_count >= self._MAX_ERRORS:
            logger.error(
                "TabInjector tab=%d: too many errors, injection suspended",
                self.tab_index,
            )
            return

        import json
        char_json = json.dumps(ch)
        js = _JS_TYPE_CHAR.format(char_json=char_json)

        success = self._run_js(js)
        if success:
            self._error_count = 0
        else:
            self._error_count += 1
            logger.debug(
                "TabInjector tab=%d: emit_character failed for %r (errors=%d)",
                self.tab_index, ch, self._error_count,
            )

        if self._settle > 0:
            time.sleep(self._settle)

    # ------------------------------------------------------------------
    # emit_key — plug into TypingEngine(emit_key=...)
    # ------------------------------------------------------------------

    def emit_key(self, key: str) -> None:
        """Inject a key press (tab, enter, backspace, etc.) into the target tab."""
        if self._error_count >= self._MAX_ERRORS:
            return

        import json
        key_json = json.dumps(key.lower())
        js = _JS_PRESS_KEY.format(key_json=key_json)

        success = self._run_js(js)
        if not success:
            self._error_count += 1

        if self._settle > 0:
            time.sleep(self._settle)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_js(self, js: str) -> bool:
        """Run JS in the target tab. Returns True if the call succeeded."""
        try:
            if _IS_MAC:
                result = _mac_execute_js(self.browser_type, self.tab_index, js)
                return result is not None
            if _IS_WIN:
                # Windows path: JS injection via pywinauto not yet available;
                # fall back to direct SendKeys for basic ASCII characters.
                # Full JS injection on Windows requires a browser extension
                # (future work). For now, extract the character from the JS
                # and use SendKeys as best-effort.
                return self._win_fallback(js)
        except Exception as exc:
            logger.debug("TabInjector._run_js exception: %s", exc)
        return False

    def _win_fallback(self, js: str) -> bool:
        """
        Windows fallback: extract the character/key from the JS string and
        use pywinauto SendKeys. Less reliable than JS injection but functional
        for simple text fields.
        """
        import re as _re
        # Extract the argument from emit_character JS: })("x")
        char_match = _re.search(r'\}\)\(("(?:[^"\\]|\\.)*")\)', js)
        if char_match:
            import json
            ch = json.loads(char_match.group(1))
            return _win_send_char_to_tab(self.browser_type, self.tab_index, ch)
        return False

    @property
    def healthy(self) -> bool:
        """False if the injector has exceeded its error threshold."""
        return self._error_count < self._MAX_ERRORS

    def reset_errors(self) -> None:
        """Reset the error counter (e.g. after user confirms tab is ready)."""
        self._error_count = 0
