#!/usr/bin/env bash
# Build AutoFlow as a standalone macOS .app using py2app.
# Prerequisites: Xcode Command Line Tools (for iconutil), Python 3.8+,
# Homebrew Tesseract (brew install tesseract) on the build machine.
# Code signing is currently disabled in this script (see py2app signing block).
set -euo pipefail

export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Professional app icon: copy master PNG into the repo for the build (override with ICON_SOURCE=...)
ICON_SOURCE="${ICON_SOURCE:-/Users/kennygoranson/Downloads/Autoflow Icon.png}"
ICON_PNG="$ROOT/resources/autoflow_icon.png"
ICONSET="$ROOT/resources/AutoFlow.iconset"
ICNS_OUT="$ROOT/resources/AutoFlow.icns"
mkdir -p "$ROOT/resources"
if [[ ! -f "$ICON_SOURCE" ]]; then
  echo "Error: Icon source not found: $ICON_SOURCE (set ICON_SOURCE to a PNG path)" >&2
  exit 1
fi
cp "$ICON_SOURCE" "$ICON_PNG"
echo "==> Staged app icon: $ICON_PNG (from $ICON_SOURCE)"

# Use PYTHON if set. Otherwise prefer a Python <3.14 so pinned wheels (e.g. Pillow 10) install cleanly.
if [[ -z "${PYTHON:-}" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    if python3 -c 'import sys; sys.exit(0 if sys.version_info < (3, 14) else 1)' 2>/dev/null; then
      PYTHON=python3
    fi
  fi
  if [[ -z "${PYTHON:-}" ]] && [[ -x /usr/bin/python3 ]]; then
    if /usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info < (3, 14) else 1)' 2>/dev/null; then
      PYTHON=/usr/bin/python3
    fi
  fi
  PYTHON="${PYTHON:-python3}"
fi
echo "Using Python for venv: $PYTHON ($("$PYTHON" --version 2>&1))"

VENV_DIR="$ROOT/build_venv"
if [[ ! -d "$VENV_DIR" ]]; then
  echo "==> Creating Python virtual environment: $VENV_DIR"
  "$PYTHON" -m venv "$VENV_DIR"
fi
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
trap 'if [[ -n "${VIRTUAL_ENV:-}" ]]; then deactivate 2>/dev/null || true; fi' EXIT
PYTHON="$VENV_DIR/bin/python"
echo "Using venv Python: $PYTHON ($("$PYTHON" --version 2>&1))"

DEST="$ROOT/build/tesseract_bundle"

echo "==> Installing build dependencies (py2app, app requirements)"
"$PYTHON" -m pip install -q --upgrade pip
"$PYTHON" -m pip install -q -r "$ROOT/requirements.txt" "py2app>=0.28"

find_tesseract() {
  "$PYTHON" <<'PY'
import os
candidates = [
    "/opt/homebrew/bin/tesseract",
    "/usr/local/bin/tesseract",
]
for c in candidates:
    if os.path.isfile(c):
        real = os.path.realpath(c)
        prefix = os.path.dirname(os.path.dirname(real))
        td = os.path.join(prefix, "share", "tessdata")
        if os.path.isdir(td):
            print(prefix)
            print(real)
            raise SystemExit(0)
raise SystemExit("Could not find Homebrew-style Tesseract with tessdata. Install: brew install tesseract")
PY
}

echo "==> Locating Tesseract (Apple Silicon: /opt/homebrew, Intel: /usr/local)"
TESS_LINES="$(find_tesseract)"
TESS_PREFIX="$(printf '%s\n' "$TESS_LINES" | sed -n '1p')"
TESS_BIN="$(printf '%s\n' "$TESS_LINES" | sed -n '2p')"
echo "    Prefix: $TESS_PREFIX"
echo "    Binary: $TESS_BIN"

echo "==> Cleaning previous py2app output"
rm -rf "$ROOT/dist"
rm -rf "$ROOT/packaging_py2app/build"
# Remove repo build/ for a clean tesseract staging (matches PyInstaller flow)
rm -rf "$ROOT/build"
mkdir -p "$DEST"

echo "==> Staging Tesseract binary and Homebrew dylibs into build/tesseract_bundle"
"$PYTHON" <<PY
import os, shutil, subprocess

def under_prefix(path: str) -> bool:
    path = os.path.realpath(path)
    return path.startswith("/opt/homebrew/") or path.startswith("/usr/local/")

def libs(binary: str):
    out = subprocess.check_output(["otool", "-L", binary], text=True)
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        lib = line.split()[0]
        if lib.startswith("@"):
            continue
        yield lib

def main():
    src_bin = r"""$TESS_BIN"""
    dest_dir = r"""$DEST"""
    os.makedirs(dest_dir, exist_ok=True)
    dst_exe = os.path.join(dest_dir, "tesseract")
    shutil.copy2(src_bin, dst_exe)
    os.chmod(dst_exe, 0o755)
    queue = [dst_exe]
    seen = set()
    while queue:
        b = queue.pop()
        b = os.path.realpath(b)
        if b in seen:
            continue
        seen.add(b)
        try:
            for lib in libs(b):
                if not under_prefix(lib) or not os.path.isfile(lib):
                    continue
                name = os.path.basename(lib)
                dst = os.path.join(dest_dir, name)
                if not os.path.isfile(dst):
                    shutil.copy2(lib, dst)
                    os.chmod(dst, 0o644)
                    queue.append(dst)
        except subprocess.CalledProcessError:
            pass

main()
PY

echo "==> Copying tessdata from $TESS_PREFIX/share/tessdata"
mkdir -p "$DEST/tessdata"
cp -R "$TESS_PREFIX/share/tessdata/"* "$DEST/tessdata/"

echo "==> Building AutoFlow.icns from resources/autoflow_icon.png"
if [[ ! -f "$ICON_PNG" ]]; then
  echo "Error: $ICON_PNG not found" >&2
  exit 1
fi
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
SRC="$ICON_PNG"
sips -z 16 16 "$SRC" --out "$ICONSET/icon_16x16.png" >/dev/null
sips -z 32 32 "$SRC" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$SRC" --out "$ICONSET/icon_32x32.png" >/dev/null
sips -z 64 64 "$SRC" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$SRC" --out "$ICONSET/icon_128x128.png" >/dev/null
sips -z 256 256 "$SRC" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$SRC" --out "$ICONSET/icon_256x256.png" >/dev/null
sips -z 512 512 "$SRC" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$SRC" --out "$ICONSET/icon_512x512.png" >/dev/null
sips -z 1024 1024 "$SRC" --out "$ICONSET/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$ICONSET" -o "$ICNS_OUT"
rm -rf "$ICONSET"
echo "    Created $ICNS_OUT"

echo "==> Running py2app (root setup.py delegates to packaging_py2app/ for pyproject.toml isolation)"
cd "$ROOT"
"$PYTHON" setup.py py2app

# Code signing temporarily disabled: Team ID conflicts between the app bundle
# and bundled Python framework. Re-enable the block below when resolved.
echo "==> Code signing: skipped (unsigned build; Gatekeeper may warn — app still runs)"

# APP_PATH="$ROOT/dist/AutoFlow.app"
# CERT_NAME='Developer ID Application: Goranson Digital, LLC'
#
# echo "==> Code signing"
# if ! command -v codesign >/dev/null 2>&1; then
#   echo "Warning: codesign not found; skipping signing (app works unsigned)." >&2
# elif [[ ! -d "$APP_PATH" ]]; then
#   echo "Warning: $APP_PATH not found; skipping signing." >&2
# elif ! security find-identity -v -p codesigning 2>/dev/null | grep -Fq "$CERT_NAME"; then
#   echo "Warning: Certificate \"$CERT_NAME\" not found (check: security find-identity -v -p codesigning). Continuing without signing." >&2
# else
#   echo "    Found certificate: $CERT_NAME"
#   codesign --force --verify --verbose \
#     --sign "$CERT_NAME" \
#     --options runtime \
#     --timestamp \
#     "$APP_PATH"
#   echo "    Verifying with codesign..."
#   codesign --verify --verbose "$APP_PATH"
#   echo "    Verifying with spctl..."
#   spctl --assess --type execute "$APP_PATH"
#   echo "    Code signing succeeded: $APP_PATH"
# fi

echo "==> Build complete: $ROOT/dist/AutoFlow.app"

if [[ "${CREATE_DMG:-0}" == "1" ]]; then
  echo "==> Creating dist/AutoFlow.dmg (CREATE_DMG=1)"
  DMG_TMP="$ROOT/dist/dmg_root"
  rm -rf "$DMG_TMP"
  mkdir -p "$DMG_TMP"
  cp -R "$ROOT/dist/AutoFlow.app" "$DMG_TMP/"
  hdiutil create -volname "AutoFlow" -srcfolder "$DMG_TMP" -ov -format UDZO "$ROOT/dist/AutoFlow.dmg"
  rm -rf "$DMG_TMP"
  echo "    DMG: $ROOT/dist/AutoFlow.dmg"
fi
