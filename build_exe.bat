@echo off
rem Builds dist\Neon Lights\Neon Lights.exe with PyInstaller.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run run.bat once first to create the virtual environment.
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pyinstaller || exit /b 1
".venv\Scripts\python.exe" tools\make_icon.py || exit /b 1
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed ^
    --name "Neon Lights" ^
    --icon assets\icon.ico ^
    --collect-submodules bleak ^
    --collect-submodules winrt ^
    --collect-submodules dxcam ^
    --exclude-module tkinter ^
    main.py || exit /b 1
echo.
echo Built: dist\Neon Lights\Neon Lights.exe
