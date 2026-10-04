@echo off
rem Tests the strip through the app's own Bluetooth + lighting code. Close Neon Lights first.
cd /d "%~dp0"
".venv\Scripts\python.exe" tools\app_test.py
echo.
pause
