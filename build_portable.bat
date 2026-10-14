@echo off
cd /d "%~dp0"
title Vision Expo - Build portable
rem Tao ban portable trong dist\VisionExpo (+ dist\VisionExpo.zip). Can internet lan dau.
rem Tham so: -Clean (build lai Python), -Cpu (PyTorch CPU), bo -Zip neu khong can nen.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\build_portable.ps1" -Zip %*
pause
