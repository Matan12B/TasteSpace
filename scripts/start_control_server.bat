@echo off
setlocal
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\control_server.py"
) else (
  python "scripts\control_server.py"
)
