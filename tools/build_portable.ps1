<#
Build a self-contained copy of the app in dist\VisionExpo (embedded Python + all packages + models + config).
The target PC only needs: Windows 10/11 x64 and, for GPU, an NVIDIA driver supporting CUDA 12.6.

Usage (from Exhibition folder):
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_portable.ps1 [-Zip] [-Clean] [-Cpu] [-NoShow]
  -Zip    also create dist\VisionExpo.zip
  -Clean  rebuild the embedded Python from scratch (otherwise only app/web/models/config are refreshed)
  -Cpu    install CPU-only PyTorch (smaller package, no GPU)
  -NoShow leave the 3D show dashboard (/show/, start_show.bat) out of the package
#>
param(
    [switch]$Zip,
    [switch]$Clean,
    [switch]$Cpu,
    [switch]$NoShow,
    [string]$PythonVersion = "3.11.9",
    [string]$TorchIndex = "https://download.pytorch.org/whl/cu126"
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $PSScriptRoot
$DistRoot = Join-Path $Root "dist"
$Dist = Join-Path $DistRoot "VisionExpo"
$Cache = Join-Path $DistRoot "_cache"
$PyDir = Join-Path $Dist "python"
$PyExe = Join-Path $PyDir "python.exe"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

function Run([string]$exe, [string[]]$argv) {
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { throw "command failed ($LASTEXITCODE): $exe $($argv -join ' ')" }
}

function Download([string]$url, [string]$dest) {
    if (Test-Path $dest) { return }
    Write-Host "download $url"
    $tmp = "$dest.part"
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
    Move-Item $tmp $dest
}

New-Item -ItemType Directory -Force -Path $Cache | Out-Null

# ---------------------------------------------------------------- embedded Python
if ($Clean -and (Test-Path $PyDir)) {
    Step "Removing old embedded Python"
    Remove-Item -Recurse -Force $PyDir
}

if (-not (Test-Path $PyExe)) {
    Step "Embedded Python $PythonVersion"
    $zipName = "python-$PythonVersion-embed-amd64.zip"
    $zipPath = Join-Path $Cache $zipName
    Download "https://www.python.org/ftp/python/$PythonVersion/$zipName" $zipPath
    New-Item -ItemType Directory -Force -Path $PyDir | Out-Null
    Expand-Archive -Path $zipPath -DestinationPath $PyDir -Force

    # ._pth replaces sys.path entirely: stdlib zip, site-packages, and the app root (..) for `python -m app`.
    $pth = Get-ChildItem $PyDir -Filter "python3*._pth" | Select-Object -First 1
    $stdZip = (Get-ChildItem $PyDir -Filter "python3*.zip" | Select-Object -First 1).Name
    Set-Content -Path $pth.FullName -Encoding ASCII -Value @($stdZip, ".", "Lib\site-packages", "..", "import site")

    Step "pip"
    $getPip = Join-Path $Cache "get-pip.py"
    Download "https://bootstrap.pypa.io/get-pip.py" $getPip
    Run $PyExe @($getPip, "--no-warn-script-location")

    Step "PyTorch"
    if ($Cpu) {
        Run $PyExe @("-m", "pip", "install", "--no-warn-script-location", "torch", "torchvision")
    } else {
        Run $PyExe @("-m", "pip", "install", "--no-warn-script-location", "torch", "torchvision", "--index-url", $TorchIndex)
    }

    Step "Python packages"
    Run $PyExe @("-m", "pip", "install", "--no-warn-script-location", "-r", (Join-Path $Root "requirements.txt"))

    # App-local VC++ runtime so the target PC does not need the redistributable installed.
    foreach ($dll in "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll") {
        $src = Join-Path $env:WINDIR "System32\$dll"
        if ((Test-Path $src) -and -not (Test-Path (Join-Path $PyDir $dll))) { Copy-Item $src $PyDir }
    }
} else {
    Write-Host "Embedded Python already present (use -Clean to rebuild it)."
    Step "Sync Python packages"
    Run $PyExe @("-m", "pip", "install", "--no-warn-script-location", "-q", "-r", (Join-Path $Root "requirements.txt"))
}

# ---------------------------------------------------------------- 3D show dashboard
# Greco2026DashBoard (Vite) is built into show\ here; the package only carries that static build (served at
# /show/ by the same server), never the source or node_modules, so the exhibition PC needs no Node.
$ShowIndex = Join-Path $Root "show\index.html"
if (-not $NoShow) {
    $hasNpm = $null -ne (Get-Command npm -ErrorAction SilentlyContinue)
    if ($hasNpm -and (Test-Path (Join-Path $Root "Greco2026DashBoard\package.json"))) {
        Step "Build 3D show dashboard"
        & cmd.exe /c "`"$(Join-Path $Root 'build_show.bat')`" nopause"
        if ($LASTEXITCODE -ne 0) {
            if (-not (Test-Path $ShowIndex)) { throw "build_show.bat failed and there is no previous show\ build" }
            Write-Warning "build_show.bat failed; packaging the previous show\ build"
        }
    } elseif (Test-Path $ShowIndex) {
        Write-Host "npm or Greco2026DashBoard not found: packaging the existing show\ build."
    } else {
        Write-Warning "No 3D show build (show\index.html) and no Node to build it: /show/ will be missing from the package."
    }
}

# ---------------------------------------------------------------- app files
Step "Copy app, web, models, config"
$dirs = @("app", "web", "tools", "models", "config", "docs")
if (-not $NoShow) { $dirs += "show" }
foreach ($d in $dirs) {
    $src = Join-Path $Root $d
    $dst = Join-Path $Dist $d
    if (-not (Test-Path $src)) { continue }
    & robocopy $src $dst /MIR /NFL /NDL /NJH /NJS /NP /XD __pycache__ /XF *.tmp *.part | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $d ($LASTEXITCODE)" }
}
$files = @("README.md", "requirements.txt", "start.bat", "start_kiosk.bat")
if (-not $NoShow) { $files += "start_show.bat" }
foreach ($f in $files) {
    $src = Join-Path $Root $f
    if (Test-Path $src) { Copy-Item $src $Dist -Force }
}
if ($NoShow) {  # an earlier build may have left them
    foreach ($p in (Join-Path $Dist "show"), (Join-Path $Dist "start_show.bat")) {
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }
}

$pose = Join-Path $Dist "models\yolo26n-pose.pt"
$seg = Join-Path $Dist "models\yolo26n-seg.pt"
if (-not (Test-Path $pose) -or -not (Test-Path $seg)) {
    Step "Preparing models inside the package"
    Push-Location $Dist
    Run $PyExe @("tools\prepare_models.py", "--device", $(if ($Cpu) { "cpu" } else { "0" }))
    Pop-Location
}

# ---------------------------------------------------------------- smoke test
Step "Smoke test"
Push-Location $Dist
Run $PyExe @("-c", "import torch, torchvision, cv2, ultralytics, fastapi, uvicorn, lap, websockets; import app.main; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), '| ultralytics', ultralytics.__version__)")
Run $PyExe @("-c", "from app.paths import SHOW_DIRS; d = next((x for x in SHOW_DIRS if (x / 'index.html').is_file()), None); print('show:', d or 'none (/show/ off)')")
Pop-Location
Get-ChildItem -Path $Dist -Recurse -Directory -Filter __pycache__ | Where-Object { $_.FullName -notlike "*\python\*" } | Remove-Item -Recurse -Force

$sizeGb = [math]::Round(((Get-ChildItem $Dist -Recurse -File | Measure-Object Length -Sum).Sum / 1GB), 2)
Write-Host "`nPackage: $Dist ($sizeGb GB)" -ForegroundColor Green

# ---------------------------------------------------------------- zip
if ($Zip) {
    Step "Zip"
    $zipOut = Join-Path $DistRoot "VisionExpo.zip"
    if (Test-Path $zipOut) { Remove-Item $zipOut -Force }
    $sevenZip = @("$env:ProgramFiles\7-Zip\7z.exe", "${env:ProgramFiles(x86)}\7-Zip\7z.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    Push-Location $DistRoot
    if ($sevenZip) {
        Run $sevenZip @("a", "-tzip", "-mx=5", $zipOut, "VisionExpo")
    } else {
        Run "tar.exe" @("-a", "-c", "-f", $zipOut, "VisionExpo")
    }
    Pop-Location
    $zipGb = [math]::Round((Get-Item $zipOut).Length / 1GB, 2)
    Write-Host "Zip: $zipOut ($zipGb GB)" -ForegroundColor Green
}
