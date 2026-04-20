param(
    [string]$Python = "python",
    [string]$VenvDir = "build_venv_win"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "Using Python launcher: $Python"
& $Python --version

if (-not (Test-Path $VenvDir)) {
    Write-Host "==> Creating Python virtual environment: $VenvDir"
    & $Python -m venv $VenvDir
}

$VenvPython = Join-Path $Root "$VenvDir\Scripts\python.exe"
Write-Host "Using venv Python: $VenvPython"
& $VenvPython --version

Write-Host "==> Installing Windows desktop build dependencies"
& $VenvPython -m pip install -q --upgrade pip
& $VenvPython -m pip install -q -r "$Root\requirements-desktop.txt" "pyinstaller>=6.0"

Write-Host "==> Running PyInstaller"
& $VenvPython -m PyInstaller --clean --noconfirm "$Root\typestra_windows.spec"

$Exe = Join-Path $Root "dist\typestra-latest-win.exe"
if (-not (Test-Path $Exe)) {
    throw "Expected build output not found: $Exe"
}

Write-Host "==> Build complete: $Exe"
