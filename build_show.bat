@echo off
setlocal
cd /d "%~dp0"
title Vision Expo - Build show
rem Build the 3D dashboard into show\ (no Node needed on the exhibition PC after this).
rem   build_show.bat
rem   build_show.bat nopause
set "DOPAUSE=1"
if /I "%~1"=="nopause" set "DOPAUSE=0"

where npm >nul 2>&1
if errorlevel 1 (
  if exist "%~dp0show\index.html" (
    echo [show] npm khong co, giu ban da build trong show\
    goto done
  )
  echo [show] Can Node.js de build dashboard lan dau.
  goto fail
)

if not exist "%~dp0Greco2026DashBoard\package.json" (
  echo [show] Khong thay thu muc Greco2026DashBoard
  goto fail
)

pushd "%~dp0Greco2026DashBoard"
if not exist node_modules (
  echo [show] npm ci
  call npm ci
  if errorlevel 1 goto fail_pop
)
echo [show] vite build
call npm run build
if errorlevel 1 goto fail_pop
popd

echo [show] copy dist -^> show\
robocopy "%~dp0Greco2026DashBoard\dist" "%~dp0show" /MIR /NFL /NDL /NJH /NJS /NP
if %ERRORLEVEL% GEQ 8 goto fail

rem show\VERSION.txt: which Greco commit this build came from (served at /show/VERSION.txt).
set "GH=unknown"
for /f %%h in ('git -C "%~dp0Greco2026DashBoard" rev-parse --short HEAD 2^>nul') do set "GH=%%h"
set "DIRTY="
for /f %%d in ('git -C "%~dp0Greco2026DashBoard" status --porcelain 2^>nul') do set "DIRTY= +uncommitted"
> "%~dp0show\VERSION.txt" echo Greco2026DashBoard %GH%%DIRTY% built %DATE% %TIME%
type "%~dp0show\VERSION.txt"
echo [show] xong: %~dp0show\index.html
echo [show] nho commit thu muc show\ de may trien lam pull duoc ban moi
goto done

:fail_pop
popd
:fail
echo [show] that bai
if "%DOPAUSE%"=="1" pause
exit /b 1

:done
if "%DOPAUSE%"=="1" pause
exit /b 0
