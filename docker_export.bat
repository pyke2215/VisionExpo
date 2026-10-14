@echo off
rem Build Docker image + delivery bundle in dist\VisionExpo-docker
rem   docker_export.bat            GPU image
rem   docker_export.bat -Cpu       CPU-only image
rem   docker_export.bat -Zip       also create dist\VisionExpo-docker.zip
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\docker_export.ps1" %*
pause
