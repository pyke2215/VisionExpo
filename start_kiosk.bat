@echo off
cd /d "%~dp0"
title Vision Expo - Kiosk

set "VPY=%~dp0python\python.exe"
if not exist "%VPY%" set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" if exist "%~dp0..\.venv\Scripts\python.exe" set "VPY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo Chua cai dat. Hay chay setup.bat truoc.
  pause
  exit /b 1
)

set "PORT=8000"
rem Man hinh trinh chieu: luoi tat ca camera, an menu. Doi thanh ?kiosk=1^&auto=1 de tu chuyen tung camera.
set "URL=http://127.0.0.1:%PORT%/?kiosk=1&grid=1"

start "Vision Expo - Server" /min "%VPY%" -m app --no-browser --port %PORT%
timeout /t 6 /nobreak >nul
start "" msedge --kiosk "%URL%" --edge-kiosk-type=fullscreen --no-first-run
if errorlevel 1 start "" "%URL%"
