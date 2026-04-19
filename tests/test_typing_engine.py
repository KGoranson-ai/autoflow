"""
Minimal TypingEngine tests.
Uses the engine's emit hooks so no real keystrokes are sent.
Run from project root: PYTHONPATH=src python3 -m pytest tests/test_typing_engine.py
Or with unittest: PYTHONPATH=src python3 tests/test_typing_engine.py
"""

import sys
import unittest

# Ensure src is on path when run as script
if __name__ == "__main__":
    sys.path.insert(0, "src")

from typing_engine import TypingEngine, TypingConfig


class TestTypingEngine(unittest.TestCase):
    def test_instantiates_with_default_config(self):
        config = TypingConfig()
        engine = TypingEngine(config)
        self.assertEqual(engine.config.wpm, 50)
        self.assertEqual(engine.config.humanization_level, 2)

    def test_accepts_custom_config(self):
        config = TypingConfig(
            wpm=60,
            humanization_level=3,
            speed_variation=False,
            typos_enabled=False,
        )
        engine = TypingEngine(config)
        self.assertEqual(engine.config.wpm, 60)
        self.assertEqual(engine.config.humanization_level, 3)
        self.assertFalse(engine.config.speed_variation)
        self.assertFalse(engine.config.typos_enabled)

    def test_type_text_emits_characters_without_real_keystrokes(self):
        emitted_chars = []
        emitted_keys = []
        config = TypingConfig(
            countdown_seconds=0,
            speed_variation=False,
            thinking_pauses=False,
            punctuation_pauses=False,
            typos_enabled=False,
        )
        engine = TypingEngine(
            config,
            emit_character=emitted_chars.append,
            emit_key=emitted_keys.append,
        )

        engine.type_text("hi")

        self.assertEqual("".join(emitted_chars), "hi")
        self.assertEqual(emitted_keys, [])

    def test_type_text_emits_enter_for_newline(self):
        emitted_chars = []
        emitted_keys = []
        config = TypingConfig(
            countdown_seconds=0,
            speed_variation=False,
            thinking_pauses=False,
            punctuation_pauses=False,
            typos_enabled=False,
        )
        engine = TypingEngine(
            config,
            emit_character=emitted_chars.append,
            emit_key=emitted_keys.append,
        )

        engine.type_text("a\nb")

        self.assertEqual("".join(emitted_chars), "ab")
        self.assertEqual(emitted_keys, ["enter"])

    def test_type_spreadsheet_emits_tab_and_enter_navigation(self):
        emitted_chars = []
        emitted_keys = []
        config = TypingConfig(countdown_seconds=0)
        engine = TypingEngine(
            config,
            emit_character=emitted_chars.append,
            emit_key=emitted_keys.append,
        )

        engine.type_spreadsheet([["A1", "B1"], ["A2", "B2"]])

        self.assertEqual("".join(emitted_chars), "A1B1A2B2")
        self.assertEqual(emitted_keys, ["tab", "enter", "tab"])


if __name__ == "__main__":
    unittest.main()
