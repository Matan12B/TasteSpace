@echo off
setlocal
cd /d "%~dp0.."

if not exist "data" mkdir "data"

if exist "data\tastespace.pid" (
  for /f "usebackq delims=" %%p in ("data\tastespace.pid") do (
    tasklist /FI "PID eq %%p" | find "%%p" >nul
    if not errorlevel 1 (
      echo TasteSpace is already running on PID %%p.
      echo Open http://localhost:8000/ui
      exit /b 0
    )
  )
  del "data\tastespace.pid" >nul 2>nul
)

if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Run:
  echo py -m venv .venv
  echo .venv\Scripts\python.exe -m pip install -r requirements.txt
  exit /b 1
)

set "TASTESPACE_APP_HOST=0.0.0.0"
set "TASTESPACE_APP_PORT=8000"

echo Starting TasteSpace on http://0.0.0.0:8000
echo Log: data\tastespace_server.log

powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process -FilePath '.venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','app:app','--host','0.0.0.0','--port','8000' -WorkingDirectory (Get-Location) -RedirectStandardOutput 'data\tastespace_server.log' -RedirectStandardError 'data\tastespace_server.log' -PassThru; Set-Content -Path 'data\tastespace.pid' -Value $p.Id; Write-Host ('TasteSpace PID ' + $p.Id)"

echo.
echo UI:       http://localhost:8000/ui
echo Manifest: http://localhost:8000/manifest.json
