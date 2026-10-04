@echo off
rem Finds how many color updates per second the strip handles. Close Neon Lights first.
cd /d "%~dp0"
".venv\Scripts\python.exe" tools\rate_test.py
echo.
pause
