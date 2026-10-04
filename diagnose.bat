@echo off
rem Guided LED strip test. Close Neon Lights and the phone app first.
cd /d "%~dp0"
".venv\Scripts\python.exe" tools\diagnose.py
echo.
pause
