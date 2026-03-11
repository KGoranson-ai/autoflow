"""
CLI for autoflow-engine. Mirrors the behavior of src/autoflow.py:
with --text runs typing; without --text tries to launch the GUI (when available).
"""

import argparse
import sys

import pyautogui

from autoflow_engine import TypingConfig, TypingEngine


def main() -> None:
    """Entry point for the autoflow console script. Runs CLI typing or launches GUI."""
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
        dest="human_level",
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
        # Try to launch the full GUI (autoflow_v3) when run from source
        try:
            from autoflow_v3 import run_cli_or_gui
            run_cli_or_gui()
        except ImportError:
            parser.print_help()
            print()
            print("Provide --text to type from the CLI.")
            print("For the GUI app, run from source: python src/autoflow.py")
            sys.exit(0)
