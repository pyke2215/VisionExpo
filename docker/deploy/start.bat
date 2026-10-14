@echo off
cd /d "%~dp0"
docker compose up -d && start "" http://127.0.0.1:8000/
