@echo off
setlocal
cd /d "%~dp0.."

if not exist "data\tastespace.pid" (
  echo TasteSpace PID file not found. Nothing to stop.
  exit /b 0
)

for /f "usebackq delims=" %%p in ("data\tastespace.pid") do set "PID=%%p"

if "%PID%"=="" (
  del "data\tastespace.pid" >nul 2>nul
  echo Empty PID file removed.
  exit /b 0
)

tasklist /FI "PID eq %PID%" | find "%PID%" >nul
if errorlevel 1 (
  del "data\tastespace.pid" >nul 2>nul
  echo TasteSpace was not running. Removed stale PID file.
  exit /b 0
)

echo Stopping TasteSpace PID %PID%...
taskkill /PID %PID% /T /F >nul
del "data\tastespace.pid" >nul 2>nul
echo TasteSpace stopped.
