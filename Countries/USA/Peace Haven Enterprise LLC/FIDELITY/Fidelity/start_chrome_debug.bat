@echo off
REM Opens an ordinary Chrome window that the script can read afterwards.
REM This is NOT an automated browser - you sign in to Fidelity here yourself.
setlocal

set "PROFILE=%USERPROFILE%\chrome-fidelity"
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"

if not exist "%CHROME%" (
  echo Could not find chrome.exe in the usual places.
  echo Edit this file and set CHROME to the full path of your Chrome.
  pause
  exit /b 1
)

echo Opening Chrome with a debugging port on 127.0.0.1:9222
echo Profile: %PROFILE%
echo.
echo 1. Sign in to Fidelity in the window that opens.
echo 2. Leave it open.
echo 3. Then run:  python fidelity.py all holdings
echo.

start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%PROFILE%" https://digital.fidelity.com/ftgw/digital/portfolio/positions
