@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] Installing dependencies...
python -m pip install -r requirements.txt pyinstaller

echo [2/3] Building (per LocalToolbox.spec: includes imageio_ffmpeg)...
python -m PyInstaller --noconfirm --clean LocalToolbox.spec

echo [3/3] Done! Executable located in dist directory.
pause
