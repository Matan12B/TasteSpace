@echo off
setlocal
cd /d "%~dp0.."

set "TASTESPACE_APP_HOST=0.0.0.0"
set "TASTESPACE_APP_PORT=8000"
set "TASTESPACE_CONTROL_HOST=0.0.0.0"
set "TASTESPACE_CONTROL_PORT=9000"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\control_server.py"
) else (
  python "scripts\control_server.py"
)
