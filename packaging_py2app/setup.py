# -*- coding: utf-8 -*-
"""
py2app build for Typestra (macOS .app).

Lives in packaging_py2app/ so `python setup.py py2app` runs without a local
pyproject.toml — py2app aborts if the distribution has install_requires, and
setuptools merges [project] from the repo root pyproject.toml when setup.py is
at the root.

Run ../build_py2app.sh from the repository root (it stages Tesseract first).
"""
from __future__ import annotations

import os
from collections import defaultdict

from setuptools import setup

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TESS_STAGING = os.path.join(REPO, "build", "tesseract_bundle")
ICON_ICNS = os.path.join(REPO, "resources", "AutoFlow.icns")
APP_SCRIPT = os.path.join(REPO, "src", "autoflow.py")

APP_NAME = "Typestra"
BUNDLE_ID = "com.goransondigital.autoflow"
VERSION = "3.0.0"


def _tesseract_resource_pairs() -> list[tuple[str, list[str]]]:
    """Map staged files into Contents/Resources/tesseract/... (py2app data_files layout)."""
    if not os.path.isdir(TESS_STAGING):
        raise SystemExit(
            "Missing build/tesseract_bundle/. Run ./build_py2app.sh from the repo root first."
        )
    tess_bin = os.path.join(TESS_STAGING, "tesseract")
    if not os.path.isfile(tess_bin):
        raise SystemExit(
            "Missing build/tesseract_bundle/tesseract. Run ./build_py2app.sh from the repo root first."
        )
    groups: dict[str, list[str]] = defaultdict(list)
    for dirpath, _dirnames, filenames in os.walk(TESS_STAGING):
        rel = os.path.relpath(dirpath, TESS_STAGING)
        dest_dir = "tesseract" if rel in (".", os.curdir) else os.path.join("tesseract", rel)
        for name in filenames:
            groups[dest_dir].append(os.path.join(dirpath, name))
    return sorted(groups.items(), key=lambda x: x[0])


def _py2app_options() -> dict:
    opts: dict = {
        "argv_emulation": True,
        "plist": {
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "Copyright © Typestra",
            "NSHighResolutionCapable": True,
        },
        "semi_standalone": False,
        "site_packages": True,
        # Beeware / iOS-adjacent stacks: modulegraph may follow optional imports;
        # excluding prevents py2app from requiring or resolving these packages.
        "excludes": [
            "rubicon",
            "rubicon.objc",
            "toga",
            "briefcase",
        ],
        # Whole packages to copy; keep aligned with requirements.txt / real imports.
        "packages": [
            "PIL",
            "tkinter",
            "pyautogui",
            "pynput",
            "pytesseract",
            "pyperclip",
            "requests",
            "urllib3",
            "certifi",
            "charset_normalizer",
            "idna",
            "cryptography",
        ],
        "includes": [
            "typing_engine",
            "autoflow_v3",
            "pytesseract",
            "PIL.Image",
            "pyperclip",
            "requests",
            "cryptography",
            "_tkinter",
            "tkinter",
            "tkinter.ttk",
            "pynput.keyboard._darwin",
            "pynput.mouse._darwin",
            "pynput._util.darwin",
        ],
        "resources": _tesseract_resource_pairs(),
    }
    # python.org and similar builds link _tkinter against Tcl/Tk in /Library/Frameworks;
    # bundling them avoids missing-GUI failures when the destination Mac lacks matching libs.
    _fw_root = "/Library/Frameworks"
    _tcl_tk = [
        os.path.join(_fw_root, name)
        for name in ("Tcl.framework", "Tk.framework")
        if os.path.isdir(os.path.join(_fw_root, name))
    ]
    if _tcl_tk:
        opts["frameworks"] = _tcl_tk
    if os.path.isfile(ICON_ICNS):
        opts["iconfile"] = ICON_ICNS
    return opts


setup(
    name=APP_NAME,
    version=VERSION,
    description="Typestra typing and spreadsheet automation",
    app=[APP_SCRIPT],
    options={"py2app": _py2app_options()},
)
