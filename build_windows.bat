@echo off
REM Builds a standalone Windows .exe (double-click, no Python/pip needed to run).
REM Run this natively on a Windows machine -- PyInstaller cannot cross-build
REM a Windows .exe from macOS or Linux.
setlocal
cd /d "%~dp0"

python -m venv build_venv
call build_venv\Scripts\activate.bat
pip install -r requirements.txt
pip install "pyinstaller>=6.15.0"

pyinstaller --clean --noconfirm waitlist.spec

call build_venv\Scripts\deactivate.bat

echo.
echo Done. App folder: dist\ImpactChildcareWaitlist\
echo Run:              dist\ImpactChildcareWaitlist\ImpactChildcareWaitlist.exe
echo (Copy/zip the whole ImpactChildcareWaitlist folder to share it -- the exe
echo needs the other files alongside it.)
echo.
echo First launch on someone else's PC: Windows Defender SmartScreen may warn
echo about an unrecognized app (it's unsigned) -- click "More info" then
echo "Run anyway".
