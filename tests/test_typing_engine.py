"""
Minimal test: instantiate TypingEngine with default config and verify it doesn't crash on a short string.
Mocks pyautogui so no real keystrokes are sent.
Run from project root: PYTHONPATH=src python3 -m pytest tests/test_typing_engine.py
Or with unittest: PYTHONPATH=src python3 tests/test_typing_engine.py
"""

import sys
import unittest
from unittest.mock import patch, MagicMock

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

    @patch("typing_engine.pyautogui.press")
    @patch("typing_engine.pyautogui.write")
    def test_type_text_does_not_crash_on_short_string(self, mock_write, mock_press):
        """Instantiate engine and call type_text with a short string; should not raise."""
        config = TypingConfig(countdown_seconds=0)
        engine = TypingEngine(config)
        engine.type_text("hi")
        # Engine should have written at least the two characters
        self.assertGreaterEqual(mock_write.call_count, 2)


if __name__ == "__main__":
    unittest.main()
