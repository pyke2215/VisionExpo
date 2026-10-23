@echo off
cd /d "%~dp0"
title Vision Expo - Show
rem Man hinh 3D tren chinh may nay: http://127.0.0.1:8000/show/
rem   start_show.bat          cong 8000
rem   start_show.bat 8080     cong khac (khi 8000 bi chan / dang dung)

set "VPY=%~dp0python\python.exe"
if not exist "%VPY%" set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" if exist "%~dp0..\.venv\Scripts\python.exe" set "VPY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo Chua cai dat. Hay chay setup.bat truoc.
  pause
  exit /b 1
)
if not exist "%~dp0show\index.html" if not exist "%~dp0Greco2026DashBoard\dist\index.html" (
  echo Chua co dashboard. Tren may dev chay build_show.bat truoc.
  pause
  exit /b 1
)

set "PORT=8000"
if not "%~1"=="" set "PORT=%~1"
set "URL=http://127.0.0.1:%PORT%/show/"

start "Vision Expo - Server" /min "%VPY%" -m app --no-browser --port %PORT%
timeout /t 6 /nobreak >nul
start "" msedge --kiosk "%URL%" --edge-kiosk-type=fullscreen --no-first-run
if errorlevel 1 start "" "%URL%"
