@echo off
REM Double-click to read your Fidelity holdings.
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" fidelity.py
) else (
  py -3 fidelity.py || python fidelity.py
)
echo.
pause
