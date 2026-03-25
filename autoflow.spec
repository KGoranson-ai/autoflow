# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for AutoFlow (macOS .app).
Run ./build_mac.sh — it stages Tesseract + tessdata into build/tesseract_bundle/ first.
"""
import os

try:
    from PyInstaller.utils.hooks import collect_all
except ImportError:
    collect_all = None

ROOT = os.path.dirname(os.path.abspath(SPEC))

ENTRY = os.path.join(ROOT, "src", "autoflow.py")
RUNTIME_HOOK = os.path.join(ROOT, "pyi_rth_autoflow_tesseract.py")
ICON_ICNS = os.path.join(ROOT, "resources", "AutoFlow.icns")
TESS_STAGING = os.path.join(ROOT, "build", "tesseract_bundle")

APP_NAME = "AutoFlow"
BUNDLE_ID = "com.goransondigital.autoflow"
VERSION = "3.0.0"

tesseract_datas = []
tesseract_binaries = []
tess_bin = os.path.join(TESS_STAGING, "tesseract")
if not os.path.isfile(tess_bin):
    raise SystemExit(
        "Missing build/tesseract_bundle/tesseract. Run ./build_mac.sh first "
        "(it stages Homebrew Tesseract and tessdata)."
    )

tesseract_binaries.append((tess_bin, "tesseract"))
tessdata_src = os.path.join(TESS_STAGING, "tessdata")
if os.path.isdir(tessdata_src):
    tesseract_datas.append((tessdata_src, "tesseract/tessdata"))

for name in os.listdir(TESS_STAGING):
    if not name.endswith(".dylib"):
        continue
    p = os.path.join(TESS_STAGING, name)
    if os.path.isfile(p):
        tesseract_binaries.append((p, "tesseract"))

extra_datas = []
extra_binaries = []
extra_hidden = []
if collect_all:
    for pkg in ("PIL", "pynput", "cryptography"):
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
    "pyperclip",
    "requests",
    "cryptography",
    "tkinter",
    "tkinter.ttk",
    "pynput.keyboard._darwin",
    "pynput.mouse._darwin",
    "pynput._util.darwin",
]

hiddenimports = list(dict.fromkeys(hiddenimports))

a = Analysis(
    [ENTRY],
    pathex=[os.path.join(ROOT, "src")],
    binaries=tesseract_binaries + extra_binaries,
    datas=tesseract_datas + extra_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[RUNTIME_HOOK] if os.path.isfile(RUNTIME_HOOK) else [],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,
    target_os_version="11.0",
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

icon_arg = ICON_ICNS if os.path.isfile(ICON_ICNS) else None

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=icon_arg,
    bundle_identifier=BUNDLE_ID,
    info_plist={
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "Copyright © AutoFlow",
        "NSHighResolutionCapable": True,
    },
)
