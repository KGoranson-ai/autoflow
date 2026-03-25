# -*- coding: utf-8 -*-
"""
py2app entry point at repo root.

The real setuptools config lives in packaging_py2app/setup.py and is run from
that directory so setuptools does not merge [project] dependencies from the
root pyproject.toml (py2app rejects install_requires on the distribution).

Prefer: ./build_py2app.sh
Or:     python setup.py py2app
"""
from __future__ import annotations

import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.join(_ROOT, "packaging_py2app")
_SETUP = os.path.join(_PKG, "setup.py")


def main() -> int:
    if not os.path.isfile(_SETUP):
        print("Missing packaging_py2app/setup.py", file=sys.stderr)
        return 1
    cmd = [sys.executable, _SETUP, *sys.argv[1:]]
    if "py2app" in sys.argv and "--dist-dir" not in sys.argv:
        cmd.extend(["--dist-dir", os.path.join(_ROOT, "dist")])
    return subprocess.call(cmd, cwd=_PKG)


if __name__ == "__main__":
    raise SystemExit(main())
