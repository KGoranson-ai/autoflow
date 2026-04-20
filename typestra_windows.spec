# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Windows Typestra executable.

Run on Windows:
  powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
"""
import os

try:
    from PyInstaller.utils.hooks import collect_all
except ImportError:
    collect_all = None

ROOT = os.path.dirname(os.path.abspath(SPEC))

ENTRY = os.path.join(ROOT, "src", "autoflow.py")
RUNTIME_HOOK = os.path.join(ROOT, "pyi_rth_autoflow_tesseract.py")

APP_NAME = "typestra-latest-win"

extra_datas = []
extra_binaries = []
extra_hidden = []

if collect_all:
    for pkg in (
        "PIL",
        "pynput",
        "cryptography",
        "pyautogui",
        "pyperclip",
        "requests",
        "pandas",
        "pdfplumber",
        "pytesseract",
        "pywinauto",
        "psutil",
    ):
        try:
            ds, bs, hid = collect_all(pkg)
            extra_datas.extend(ds)
            extra_binaries.extend(bs)
            extra_hidden.extend(hid)
        except Exception:
            pass

hiddenimports = extra_hidden + [
    "typing_engine",
    "autoflow_v3",
    "pytesseract",
    "PIL",
    "PIL.Image",
    "pyautogui",
    "pyperclip",
    "requests",
    "cryptography",
    "tkinter",
    "tkinter.ttk",
    "pynput.keyboard._win32",
    "pynput.mouse._win32",
    "pynput._util.win32",
    "pywinauto",
    "psutil",
    "win32api",
    "win32con",
    "win32gui",
    "win32process",
]

hiddenimports = list(dict.fromkeys(hiddenimports))

a = Analysis(
    [ENTRY],
    pathex=[os.path.join(ROOT, "src")],
    binaries=extra_binaries,
    datas=extra_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[RUNTIME_HOOK] if os.path.isfile(RUNTIME_HOOK) else [],
    excludes=[
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
        "pynput._util.darwin",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
)
