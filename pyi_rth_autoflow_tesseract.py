"""
PyInstaller runtime hook: configure bundled Tesseract before app imports run.
"""
import os
import sys


def _tesseract_dir():
    if not getattr(sys, "frozen", False):
        return None
    return os.path.join(sys._MEIPASS, "tesseract")


def _apply():
    d = _tesseract_dir()
    if not d or not os.path.isdir(d):
        return
    binary = os.path.join(d, "tesseract")
    if not os.path.isfile(binary):
        return
    # Homebrew-style layout: $TESSDATA_PREFIX/tessdata/*.traineddata
    os.environ["TESSDATA_PREFIX"] = d
    # Help dyld find bundled Homebrew dylibs when launching the tesseract child process.
    prev = os.environ.get("DYLD_LIBRARY_PATH", "")
    os.environ["DYLD_LIBRARY_PATH"] = d + (os.pathsep + prev if prev else "")
    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = binary
    except Exception:
        pass


_apply()
