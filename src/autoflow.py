"""
AutoFlow entry point. Run with --text for CLI typing, or no args for GUI.
  python autoflow.py --text 'hello world' --wpm 50 --human-level 2
  python autoflow.py
"""

from autoflow_v3 import run_cli_or_gui

if __name__ == "__main__":
    run_cli_or_gui()
