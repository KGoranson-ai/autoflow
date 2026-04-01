"""
TypingEngine - Pure typing automation engine (no GUI).
All pause, burst, typo, and delay logic lives here.
Uses pyautogui only for keystroke output.
"""

import pyautogui
import logging
import time
import random
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Callable, Optional


logger = logging.getLogger(__name__)


@dataclass
class TypingConfig:
    """Configuration for human-like typing behavior."""

    wpm: int = 50
    humanization_level: int = 2
    speed_variation: bool = True
    thinking_pauses: bool = True
    punctuation_pauses: bool = True
    typos_enabled: bool = True
    mode: str = "text"
    countdown_seconds: int = 5


class TypingEngine:
    """
    Pure typing engine. No GUI, no threading.
    Accepts optional callables for integration: should_stop, is_paused, on_status.
    """

    def __init__(
        self,
        config: TypingConfig,
        *,
        should_stop: Optional[Callable[[], bool]] = None,
        is_paused: Optional[Callable[[], bool]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self._should_stop = should_stop if should_stop is not None else (lambda: False)
        self._is_paused = is_paused if is_paused is not None else (lambda: False)
        self._on_status = on_status if on_status is not None else (lambda s: None)

    def _debug(self, message: str) -> None:
        logger.debug(message)

    @staticmethod
    def normalize_special_chars(text: str) -> str:
        """
        Normalize special/Unicode characters to ASCII equivalents before typing.
        """
        if not text:
            return text

        # Smart/curly quotes -> regular quotes
        text = text.replace("\u201c", '"')
        text = text.replace("\u201d", '"')
        text = text.replace("\u2018", "'")
        text = text.replace("\u2019", "'")
        text = text.replace("\u2032", "'")
        text = text.replace("\u2033", '"')

        # All dash types -> regular hyphen
        text = text.replace("\u2014", "-")
        text = text.replace("\u2013", "-")
        text = text.replace("\u2212", "-")
        text = text.replace("\u2010", "-")
        text = text.replace("\u2011", "-")
        text = text.replace("\u2012", "-")
        text = text.replace("\u2015", "-")

        # Ellipsis -> three periods
        text = text.replace("\u2026", "...")

        # Bullet points -> asterisk
        text = text.replace("\u2022", "*")
        text = text.replace("\u2023", "*")
        text = text.replace("\u2043", "*")
        text = text.replace("\u25aa", "*")
        text = text.replace("\u25cf", "*")

        # Other common Unicode -> ASCII
        text = text.replace("\u00a0", " ")
        text = text.replace("\u200b", "")
        text = text.replace("\u200c", "")
        text = text.replace("\u200d", "")
        text = text.replace("\ufeff", "")

        # Fallback: NFKD normalize and keep only ASCII where possible
        result = []
        for char in text:
            if ord(char) < 128:
                result.append(char)
                continue
            normalized = unicodedata.normalize("NFKD", char)
            ascii_part = "".join(
                c for c in normalized if ord(c) < 128 and c.isprintable()
            )
            if ascii_part:
                result.append(ascii_part)
            else:
                if char in ("\u2122", "\u00ae", "\u00a9"):
                    result.append("")
                else:
                    result.append("?")
        return "".join(result)

    @staticmethod
    def _is_list_marker(line: str) -> bool:
        """Detect if a line starts with a list marker (a., 1., i., *, -, etc.)."""
        line = line.lstrip()
        if not line:
            return False
        patterns = [
            r"^[a-z]\.\s",
            r"^[A-Z]\.\s",
            r"^\d+\.\s",
            r"^[ivxlcdm]+\.\s",
            r"^[IVXLCDM]+\.\s",
            r"^\*\s",
            r"^-\s",
        ]
        for pattern in patterns:
            if re.match(pattern, line):
                return True
        return False

    def _get_char_delay(
        self, wpm: int, use_variation: bool, human_level: int
    ) -> float:
        """Calculate delay between characters with human-like variation."""
        base_delay = 60.0 / (wpm * 5)
        if use_variation:
            variation_percent = 0.3 + (human_level * 0.15)
            variation = base_delay * variation_percent
            delay = base_delay + random.uniform(-variation, variation)
            return max(delay, base_delay * 0.3)
        return base_delay

    def type_text(self, text: str) -> None:
        """
        Type text with human-like patterns: delays, pauses, typos and corrections.
        Uses should_stop / is_paused / on_status callables if provided.
        """
        cfg = self.config
        wpm = cfg.wpm
        countdown = cfg.countdown_seconds
        human_level = cfg.humanization_level
        use_variation = cfg.speed_variation
        use_thinking = cfg.thinking_pauses
        use_punctuation = cfg.punctuation_pauses
        use_typos = cfg.typos_enabled

        self._debug(
            "type_text start: "
            f"chars={len(text)}, countdown={countdown}, wpm={wpm}, "
            f"human_level={human_level}, variation={use_variation}, "
            f"thinking={use_thinking}, punctuation={use_punctuation}, typos={use_typos}"
        )
        # Countdown
        for i in range(countdown, 0, -1):
            if self._should_stop():
                self._debug("type_text aborted during countdown by should_stop()")
                return
            self._on_status(f"⏱ Starting in {i}... Switch to target app NOW!")
            time.sleep(1)

        if self._should_stop():
            self._debug("type_text aborted before typing by should_stop()")
            return

        self._on_status("⌨️ Typing... (Auto-handling list formatting)")
        text = self.normalize_special_chars(text)
        lines = text.split("\n")
        total_chars = len(text)
        self._debug(f"type_text normalized chars={total_chars}, lines={len(lines)}")
        chars_since_pause = 0
        chars_typed = 0

        wrong_chars = {
            "a": "sq", "b": "vn", "c": "xv", "d": "sf", "e": "wr",
            "f": "dg", "g": "fh", "h": "gj", "i": "uo", "j": "hk",
            "k": "jl", "l": "ko", "m": "n", "n": "bm", "o": "ip",
            "p": "o", "q": "wa", "r": "et", "s": "ad", "t": "ry",
            "u": "yi", "v": "cb", "w": "qe", "x": "zc", "y": "tu",
            "z": "x",
        }

        for line_idx, line in enumerate(lines):
            if self._should_stop():
                self._debug(f"type_text aborted at line_idx={line_idx} by should_stop()")
                return

            is_list_item = self._is_list_marker(line)
            prev_is_list = False
            for prev_idx in range(line_idx - 1, -1, -1):
                prev_line = lines[prev_idx]
                if prev_line.strip():
                    prev_is_list = self._is_list_marker(prev_line)
                    break

            line_to_type = line
            if prev_is_list and is_list_item:
                stripped = re.sub(
                    r"^[a-zA-Z0-9ivxlcdmIVXLCDM]+\.\s*", "", line.lstrip()
                )
                line_to_type = stripped if stripped else line

            i = 0
            while i < len(line_to_type):
                while self._is_paused() and not self._should_stop():
                    time.sleep(0.1)

                if self._should_stop():
                    self._debug(
                        f"type_text aborted at line_idx={line_idx} char_idx={i} by should_stop()"
                    )
                    return

                char = line_to_type[i]
                should_typo = (
                    use_typos
                    and human_level >= 2
                    and random.random() < 0.03
                    and char.isalnum()
                    and i < len(line_to_type) - 5
                    and i > 5
                )

                if should_typo:
                    correct_char = char
                    char_lower = char.lower()
                    if char_lower in wrong_chars:
                        wrong_char = random.choice(wrong_chars[char_lower])
                        if char.isupper():
                            wrong_char = wrong_char.upper()
                    else:
                        wrong_char = random.choice("abcdefghijklmnopqrstuvwxyz")

                    pyautogui.PAUSE = 0
                    self._debug(
                        f"pyautogui.write typo wrong_char={wrong_char!r} line_idx={line_idx} char_idx={i}"
                    )
                    pyautogui.write(wrong_char, interval=0)
                    time.sleep(random.uniform(0.05, 0.15))
                    time.sleep(random.uniform(0.3, 0.7))
                    self._debug("pyautogui.press backspace for typo correction")
                    pyautogui.press("backspace")
                    time.sleep(random.uniform(0.1, 0.2))
                    pyautogui.PAUSE = 0
                    self._debug(
                        f"pyautogui.write corrected_char={correct_char!r} line_idx={line_idx} char_idx={i}"
                    )
                    pyautogui.write(correct_char, interval=0)
                else:
                    pyautogui.PAUSE = 0
                    self._debug(
                        f"pyautogui.write char={char!r} line_idx={line_idx} char_idx={i}"
                    )
                    pyautogui.write(char, interval=0)

                chars_typed += 1
                progress = int((chars_typed / total_chars) * 100)
                self._on_status(f"⌨️ Typing... {progress}% complete")

                delay = self._get_char_delay(wpm, use_variation, human_level)
                if use_punctuation:
                    if char in ".!?":
                        delay += random.uniform(0.3, 0.8) * human_level
                    elif char in ",;:":
                        delay += random.uniform(0.15, 0.4) * human_level

                if use_thinking:
                    chars_since_pause += 1
                    pause_threshold = random.randint(15, 40)
                    if chars_since_pause > pause_threshold:
                        thinking_pause = random.uniform(0.3, 1.5) * human_level
                        time.sleep(thinking_pause)
                        chars_since_pause = 0

                if use_thinking and human_level >= 2:
                    if random.random() < 0.02:
                        confusion_pause = random.uniform(1.0, 2.5)
                        time.sleep(confusion_pause)

                if use_variation and random.random() < 0.1:
                    delay *= random.uniform(0.4, 0.7)

                time.sleep(delay)
                i += 1

            if line_idx < len(lines) - 1:
                chars_typed += 1
                next_line = lines[line_idx + 1]
                next_is_list = self._is_list_marker(next_line)
                if is_list_item and not next_is_list:
                    self._debug("pyautogui.press enter (list spacing 1)")
                    pyautogui.press("enter")
                    time.sleep(0.15)
                    self._debug("pyautogui.press enter (list spacing 2)")
                    pyautogui.press("enter")
                    time.sleep(0.2)
                else:
                    self._debug("pyautogui.press enter (line break)")
                    pyautogui.press("enter")
                    time.sleep(random.uniform(0.2, 0.4))
        self._debug("type_text completed")

    def type_spreadsheet(self, rows: List[List[str]]) -> None:
        """
        Type spreadsheet data cell by cell: pyautogui.write only, then Tab or Enter.
        No clipboard, hotkeys, Cmd keys, or arrow keys — avoids macOS dictation
        and shortcut side effects from synthetic modifier combinations.
        Uses should_stop / is_paused / on_status callables if provided.
        """
        pyautogui.PAUSE = 0
        cfg = self.config
        countdown = cfg.countdown_seconds

        for i in range(countdown, 0, -1):
            if self._should_stop():
                return
            self._on_status(f"⏱ Starting in {i}... Click on cell A1 NOW!")
            time.sleep(1)

        if self._should_stop():
            return

        total_cells = sum(len(row) for row in rows)
        if total_cells == 0:
            return

        cells_typed = 0

        for row_idx, row in enumerate(rows):
            for col_idx, cell in enumerate(row):
                while self._is_paused() and not self._should_stop():
                    time.sleep(0.1)

                if self._should_stop():
                    return

                cell_content = str(cell).strip()
                if cell_content:
                    pyautogui.write(cell_content, interval=0.05)

                cells_typed += 1
                progress = int((cells_typed / total_cells) * 100)
                self._on_status(
                    f"📊 Filling spreadsheet... {progress}% "
                    f"({cells_typed}/{total_cells} cells)"
                )

                is_last_cell = (
                    row_idx == len(rows) - 1 and col_idx == len(row) - 1
                )
                if not is_last_cell:
                    if col_idx < len(row) - 1:
                        pyautogui.press("tab")
                    else:
                        pyautogui.press("enter")
                    time.sleep(0.05)
