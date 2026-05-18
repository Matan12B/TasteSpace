@echo off
setlocal
cd /d "%~dp0.."

call "scripts\stop_tastespace.bat"
call "scripts\start_tastespace.bat"
