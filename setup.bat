@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Vision Expo - Setup

echo ============================================================
echo  Vision Expo - cai dat lan dau (can internet)
echo ============================================================

set "PY="
py -3.11 --version >nul 2>nul && set "PY=py -3.11"
if not defined PY py -3.12 --version >nul 2>nul && set "PY=py -3.12"
if not defined PY py -3.10 --version >nul 2>nul && set "PY=py -3.10"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
  echo [LOI] Khong tim thay Python 3.10-3.12. Cai Python tu https://www.python.org/downloads/
  goto :fail
)
echo Python: %PY%

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo [1/5] Tao moi truong .venv ...
  %PY% -m venv .venv || goto :fail
)
set "VPY=%~dp0.venv\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip >nul

echo.
set "DEVICE=cpu"
nvidia-smi >nul 2>nul && set "DEVICE=0"
if "%DEVICE%"=="0" (
  echo [2/5] Phat hien GPU NVIDIA - cai PyTorch CUDA 12.6 ...
  "%VPY%" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126 || goto :fail
) else (
  echo [2/5] Khong co GPU NVIDIA - cai PyTorch CPU ^(cham hon^) ...
  "%VPY%" -m pip install torch torchvision || goto :fail
)

echo.
echo [3/5] Cai thu vien ...
"%VPY%" -m pip install -r requirements.txt || goto :fail

echo.
echo [4/5] Cai CLIP tokenizer ^(de ma hoa tu vung do vat^) ...
"%VPY%" -m pip install "git+https://github.com/ultralytics/CLIP.git"
if errorlevel 1 echo [CANH BAO] Khong cai duoc CLIP ^(can git^). App van chay, chi khong them tu moi duoc.

echo.
echo [5/5] Tai model + build tu vung do vat ^(YOLOE^) ...
"%VPY%" tools\prepare_models.py --device %DEVICE% || goto :fail

if not exist "config\settings.json" (
  "%VPY%" -c "import sys; sys.path.insert(0,'.'); from app.settings_store import SettingsStore; SettingsStore()"
)
if "%DEVICE%"=="cpu" (
  "%VPY%" -c "import sys; sys.path.insert(0,'.'); from app.settings_store import SettingsStore; SettingsStore().update_global({'device':'cpu'})"
)

echo.
echo ============================================================
echo  XONG. Chay start.bat de mo ung dung.
echo ============================================================
pause
exit /b 0

:fail
echo.
echo [LOI] Cai dat that bai. Xem thong bao phia tren.
pause
exit /b 1
