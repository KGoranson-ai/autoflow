"""
AutoFlow entry point. Run with --text for CLI typing, or no args for GUI.
  python autoflow.py --text 'hello world' --wpm 50 --human-level 2
  python autoflow.py
"""

import os
import sys


def _configure_bundled_tesseract():
    """PyInstaller (_MEIPASS) and py2app (RESOURCEPATH) bundles."""
    if not getattr(sys, "frozen", False):
        return
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        d = os.path.join(meipass, "tesseract")
    else:
        rp = os.environ.get("RESOURCEPATH")
        if not rp:
            return
        d = os.path.join(rp, "tesseract")
    if not os.path.isdir(d):
        return
    binary = os.path.join(d, "tesseract")
    if not os.path.isfile(binary):
        return
    os.environ["TESSDATA_PREFIX"] = d
    prev = os.environ.get("DYLD_LIBRARY_PATH", "")
    os.environ["DYLD_LIBRARY_PATH"] = d + (os.pathsep + prev if prev else "")
    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = binary
    except Exception:
        pass


_configure_bundled_tesseract()

from autoflow_v3 import run_cli_or_gui

if __name__ == "__main__":
    run_cli_or_gui()
