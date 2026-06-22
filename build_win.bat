@echo off
REM ─────────────────────────────────────────────────────────────
REM Build "ZH PC Cleaner.exe" with PyInstaller. Run on Windows (Python 3.10+).
REM Result: a single self-contained .exe — no Python needed on the user's PC.
REM ─────────────────────────────────────────────────────────────
setlocal
cd /d "%~dp0"

if not exist .build-venv ( py -3 -m venv .build-venv )
call .build-venv\Scripts\activate.bat
python -m pip install -q --upgrade pip pyinstaller certifi

set ICON=
if exist icon.ico set ICON=--icon icon.ico

pyinstaller --noconfirm --clean --windowed --onefile --name "ZH PC Cleaner" %ICON% ^
  --collect-data certifi zh_cleaner_win.py

echo.
echo  Built: dist\ZH PC Cleaner.exe
echo  Next: rename to ZH-PC-Cleaner.exe, upload to zhmotions.com/pccleaner/,
echo        and update pccleaner/version.json to the new version.
pause
