@echo off
setlocal
cd /d "%~dp0"
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker chua chay. Hay mo Docker Desktop roi chay lai.
  pause
  exit /b 1
)
echo Dang nap image (vai phut)...
docker load -i vision-expo-image.tar || goto :fail
docker compose up -d || goto :fail
echo.
echo Da cai dat. Mo http://127.0.0.1:8000/
start "" http://127.0.0.1:8000/
pause
exit /b 0
:fail
echo Loi khi cai dat.
pause
exit /b 1
