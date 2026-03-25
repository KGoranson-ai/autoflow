#!/usr/bin/env bash
# Build AutoFlow as a standalone macOS .app using PyInstaller.
# Prerequisites: Xcode Command Line Tools (for iconutil), Python 3.8+,
# Homebrew Tesseract (brew install tesseract) on the build machine.
set -euo pipefail

export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

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
echo "Using Python: $PYTHON ($("$PYTHON" --version 2>&1))"

DEST="$ROOT/build/tesseract_bundle"
PNG_MASTER="$ROOT/resources/icon_master.png"
ICONSET="$ROOT/resources/AutoFlow.iconset"
ICNS_OUT="$ROOT/resources/AutoFlow.icns"

echo "==> Installing build dependencies (PyInstaller, app requirements)"
"$PYTHON" -m pip install -q --upgrade pip
"$PYTHON" -m pip install -q -r "$ROOT/requirements.txt" "pyinstaller>=6.0"

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

echo "==> Cleaning previous PyInstaller output"
rm -rf "$ROOT/dist" "$ROOT/build"
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

echo "==> Building placeholder icon (resources/AutoFlow.icns)"
mkdir -p "$ROOT/resources"
export ROOT
"$PYTHON" <<'PY'
import os
from pathlib import Path

root = Path(os.environ["ROOT"])
png = root / "resources" / "icon_master.png"
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise SystemExit("Pillow should be installed from requirements.txt")

size = 1024
img = Image.new("RGB", (size, size), "#0f172a")
d = ImageDraw.Draw(img)
margin = size // 6
d.rounded_rectangle(
    [margin, margin, size - margin, size - margin],
    radius=size // 8,
    fill="#1e293b",
    outline="#38bdf8",
    width=8,
)
try:
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", size // 4)
except Exception:
    font = ImageFont.load_default()
text = "AF"
bbox = d.textbbox((0, 0), text, font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
tx = (size - tw) // 2
ty = (size - th) // 2 - size // 28
d.text((tx, ty), text, fill="#f8fafc", font=font)
png.parent.mkdir(parents=True, exist_ok=True)
img.save(png)
PY

rm -rf "$ICONSET"
mkdir -p "$ICONSET"
SRC="$PNG_MASTER"
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

echo "==> Running PyInstaller"
"$PYTHON" -m PyInstaller --clean --noconfirm "$ROOT/autoflow.spec"

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
