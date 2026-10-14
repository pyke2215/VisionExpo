@echo off
cd /d "%~dp0"
title Vision Expo - Server

set "VPY=%~dp0python\python.exe"
if not exist "%VPY%" set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" if exist "%~dp0..\.venv\Scripts\python.exe" set "VPY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo Chua cai dat. Hay chay setup.bat truoc.
  pause
  exit /b 1
)

rem Tham so them, vi du:  start.bat --host 0.0.0.0 --port 8000 --device cpu
"%VPY%" -m app %*
pause
