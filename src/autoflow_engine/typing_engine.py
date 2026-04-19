"""
TypingEngine - Pure typing automation engine (no GUI).
All pause, burst, typo, and delay logic lives here.
Uses pynput for keystroke output (handles Unicode natively).
"""

import logging
import time
import random
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Callable, Optional

try:
    from pynput.keyboard import Controller as _KeyboardController, Key as _Key
    _PYNPUT_IMPORT_ERROR: Optional[BaseException] = None
except Exception as _pynput_exc:  # headless / missing backend at import time
    _KeyboardController = None  # type: ignore[assignment]
    _Key = None  # type: ignore[assignment]
    _PYNPUT_IMPORT_ERROR = _pynput_exc


logger = logging.getLogger(__name__)

_kb = None  # lazily instantiated — avoids failing on headless imports


def _get_kb():
    """Return a module-level pynput Controller, creating it on first use."""
    global _kb
    if _kb is None:
        if _KeyboardController is None:
            raise RuntimeError(
                f"pynput unavailable: {_PYNPUT_IMPORT_ERROR}"
            )
        _kb = _KeyboardController()
    return _kb

# HumanTyping-inspired timing constants
_COMMON_BIGRAMS = {
    "th", "he", "in", "er", "an", "re", "on", "en", "at", "ou",
    "it", "is", "ar", "st", "to", "nt", "nd", "ha", "es", "et",
    "ed", "te", "ti", "or", "hi", "as", "ne", "ng", "al", "se",
    "le", "of", "de", "io", "ea", "li", "ve", "me", "co", "ri",
}
_BIGRAM_SPEED_BOOST = 0.6
_COMMON_WORD_BOOST = 0.65
_COMPLEX_WORD_PENALTY = 1.25
_FATIGUE_FACTOR = 1.0004

_COMMON_WORDS = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "it",
    "for", "not", "on", "with", "he", "as", "you", "do", "at", "this",
    "but", "his", "by", "from", "they", "we", "say", "her", "she", "or",
    "an", "will", "my", "one", "all", "would", "there", "their", "what", "so",
    "up", "out", "if", "about", "who", "get", "which", "go", "me", "when",
    "make", "can", "like", "time", "no", "just", "him", "know", "take", "people",
    "into", "year", "your", "good", "some", "could", "them", "see", "other", "than",
    "then", "now", "look", "only", "come", "its", "over", "think", "also", "back",
    "after", "use", "two", "how", "our", "work", "first", "well", "way", "even",
    "new", "want", "because", "any", "these", "give", "day", "most", "us", "is",
}


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
        emit_character: Optional[Callable[[str], None]] = None,
        emit_key: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self._should_stop = should_stop if should_stop is not None else (lambda: False)
        self._is_paused = is_paused if is_paused is not None else (lambda: False)
        self._on_status = on_status if on_status is not None else (lambda s: None)
        self._emit_character = emit_character
        self._emit_key = emit_key

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

    def _pg_write(self, text: str, *, interval: float = 0) -> None:
        """Emit typed text; uses pynput unless emit_character is set (e.g. Smart Fill)."""
        if self._emit_character:
            if not text:
                return
            for i, ch in enumerate(text):
                self._emit_character(ch)
                if interval > 0 and i < len(text) - 1:
                    time.sleep(interval)
            return

        if not text:
            return

        kb = _get_kb()
        invalid_exc = getattr(_KeyboardController, "InvalidCharacterException", None)
        for i, ch in enumerate(text):
            try:
                kb.type(ch)
            except Exception as exc:
                if invalid_exc is not None and isinstance(exc, invalid_exc):
                    logger.warning("pynput could not type character %r; skipping", ch)
                else:
                    logger.warning("pynput type error for %r: %s", ch, exc)
            if interval > 0 and i < len(text) - 1:
                time.sleep(interval)

    def _pg_press(self, key: str) -> None:
        """Emit a key press; uses pynput unless emit_key is set."""
        if self._emit_key:
            self._emit_key(key)
            return
        kb = _get_kb()
        target = (getattr(_Key, key, None) if _Key is not None else None) or key
        try:
            kb.tap(target)
        except Exception as exc:
            logger.warning("pynput tap error for %r: %s", key, exc)

    def _get_char_delay(
        self,
        wpm: int,
        use_variation: bool,
        human_level: int,
        prev_char: str = "",
        current_char: str = "",
        current_word: str = "",
        fatigue_multiplier: float = 1.0,
    ) -> float:
        """Calculate delay between characters with human-like variation.

        Incorporates bigram acceleration, common-word awareness, complex-word
        penalty, and fatigue multiplier (HumanTyping-inspired).
        """
        base_delay = 60.0 / (wpm * 5)
        base_delay *= fatigue_multiplier

        if prev_char and current_char:
            bigram = (prev_char + current_char).lower()
            if bigram in _COMMON_BIGRAMS:
                base_delay *= _BIGRAM_SPEED_BOOST

        if current_word:
            lw = current_word.lower()
            if lw in _COMMON_WORDS:
                base_delay *= _COMMON_WORD_BOOST
            elif len(current_word) >= 8:
                base_delay *= _COMPLEX_WORD_PENALTY

        if use_variation:
            variation_percent = 0.3 + (human_level * 0.15)
            variation = base_delay * variation_percent
            delay = base_delay + random.uniform(-variation, variation)
            return max(delay, base_delay * 0.3)
        return base_delay

    @staticmethod
    def _word_at(line: str, idx: int) -> str:
        """Return the whitespace-delimited word containing position idx."""
        if idx < 0 or idx >= len(line):
            return ""
        start = idx
        while start > 0 and not line[start - 1].isspace():
            start -= 1
        end = idx
        while end < len(line) and not line[end].isspace():
            end += 1
        return line[start:end]

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
        total_chars = len(text) if len(text) > 0 else 1
        self._debug(f"type_text normalized chars={total_chars}, lines={len(lines)}")
        chars_since_pause = 0
        chars_typed = 0
        fatigue_multiplier = 1.0
        prev_char = ""

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
                current_word = self._word_at(line_to_type, i)

                # Swap error: type current+next in wrong order, then fix.
                can_swap = (
                    use_typos
                    and human_level >= 2
                    and i < len(line_to_type) - 1
                    and char.isalnum()
                    and line_to_type[i + 1].isalnum()
                    and char != line_to_type[i + 1]
                    and i > 2
                    and random.random() < 0.015
                )

                should_typo = (
                    not can_swap
                    and use_typos
                    and human_level >= 2
                    and random.random() < 0.03
                    and char.isalnum()
                    and i < len(line_to_type) - 5
                    and i > 5
                )

                if can_swap:
                    next_char = line_to_type[i + 1]
                    self._debug(
                        f"type swap error {char!r}<->{next_char!r} line_idx={line_idx} char_idx={i}"
                    )
                    # Type in wrong order
                    self._pg_write(next_char, interval=0)
                    self._pg_write(char, interval=0)
                    # Reaction "oops" delay before correcting
                    time.sleep(max(0.1, random.gauss(0.35, 0.08)))
                    self._pg_press("backspace")
                    time.sleep(random.uniform(0.05, 0.12))
                    self._pg_press("backspace")
                    time.sleep(random.uniform(0.08, 0.15))
                    # Retype in correct order
                    self._pg_write(char, interval=0)
                    self._pg_write(next_char, interval=0)

                    chars_typed += 2
                    prev_char = next_char
                    fatigue_multiplier *= _FATIGUE_FACTOR
                    fatigue_multiplier *= _FATIGUE_FACTOR

                    progress = int((chars_typed / total_chars) * 100)
                    self._on_status(f"⌨️ Typing... {progress}% complete")

                    delay = self._get_char_delay(
                        wpm, use_variation, human_level,
                        prev_char=char, current_char=next_char,
                        current_word=current_word,
                        fatigue_multiplier=fatigue_multiplier,
                    )
                    time.sleep(delay)
                    i += 2
                    continue

                if should_typo:
                    correct_char = char
                    char_lower = char.lower()
                    if char_lower in wrong_chars:
                        wrong_char = random.choice(wrong_chars[char_lower])
                        if char.isupper():
                            wrong_char = wrong_char.upper()
                    else:
                        wrong_char = random.choice("abcdefghijklmnopqrstuvwxyz")

                    self._debug(
                        f"type typo wrong_char={wrong_char!r} line_idx={line_idx} char_idx={i}"
                    )
                    self._pg_write(wrong_char, interval=0)
                    # Reaction "oops" delay
                    time.sleep(max(0.1, random.gauss(0.35, 0.08)))
                    self._debug("press backspace for typo correction")
                    self._pg_press("backspace")
                    time.sleep(random.uniform(0.1, 0.2))
                    self._debug(
                        f"type corrected_char={correct_char!r} line_idx={line_idx} char_idx={i}"
                    )
                    self._pg_write(correct_char, interval=0)
                else:
                    self._debug(
                        f"type char={char!r} line_idx={line_idx} char_idx={i}"
                    )
                    self._pg_write(char, interval=0)

                chars_typed += 1
                fatigue_multiplier *= _FATIGUE_FACTOR
                progress = int((chars_typed / total_chars) * 100)
                self._on_status(f"⌨️ Typing... {progress}% complete")

                delay = self._get_char_delay(
                    wpm, use_variation, human_level,
                    prev_char=prev_char, current_char=char,
                    current_word=current_word,
                    fatigue_multiplier=fatigue_multiplier,
                )
                if use_punctuation:
                    if char in ".!?":
                        delay += random.uniform(0.3, 0.8) * human_level
                    elif char in ",;:":
                        delay += random.uniform(0.15, 0.4) * human_level

                # Word-boundary micro-pause after spaces
                if char == " ":
                    wb_pause = random.gauss(0.25, 0.05)
                    wb_pause = max(0.05, min(0.6, wb_pause))
                    time.sleep(wb_pause)
                    chars_since_pause = 0
                elif use_thinking:
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
                prev_char = char
                i += 1

            if line_idx < len(lines) - 1:
                chars_typed += 1
                next_line = lines[line_idx + 1]
                next_is_list = self._is_list_marker(next_line)
                if is_list_item and not next_is_list:
                    self._debug("press enter (list spacing 1)")
                    self._pg_press("enter")
                    time.sleep(0.15)
                    self._debug("press enter (list spacing 2)")
                    self._pg_press("enter")
                    time.sleep(0.2)
                else:
                    self._debug("press enter (line break)")
                    self._pg_press("enter")
                    time.sleep(random.uniform(0.2, 0.4))
                prev_char = ""
        self._debug("type_text completed")

    def type_spreadsheet(self, rows: List[List[str]]) -> None:
        """
        Type spreadsheet data cell by cell via _pg_write, then Tab or Enter.
        No clipboard, hotkeys, Cmd keys, or arrow keys — avoids macOS dictation
        and shortcut side effects from synthetic modifier combinations.
        Uses should_stop / is_paused / on_status callables if provided.
        """
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
                    self._pg_write(cell_content, interval=0.05)

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
                        self._pg_press("tab")
                    else:
                        self._pg_press("enter")
                    time.sleep(0.05)
