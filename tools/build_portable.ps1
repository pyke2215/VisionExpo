<#
Build a self-contained copy of the app in dist\VisionExpo (embedded Python + all packages + models + config).
The target PC only needs: Windows 10/11 x64 and, for GPU, an NVIDIA driver supporting CUDA 12.6.

Usage (from Exhibition folder):
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_portable.ps1 [-Zip] [-Clean] [-Cpu]
  -Zip    also create dist\VisionExpo.zip
  -Clean  rebuild the embedded Python from scratch (otherwise only app/web/models/config are refreshed)
  -Cpu    install CPU-only PyTorch (smaller package, no GPU)
#>
param(
    [switch]$Zip,
    [switch]$Clean,
    [switch]$Cpu,
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
    try {
        Run $PyExe @("-m", "pip", "install", "--no-warn-script-location", "git+https://github.com/ultralytics/CLIP.git")
    } catch {
        Write-Warning "CLIP not installed (git missing?). App runs, but adding new object words will not work."
    }

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

# ---------------------------------------------------------------- app files
Step "Copy app, web, models, config"
foreach ($d in "app", "web", "tools", "models", "config", "docs") {
    $src = Join-Path $Root $d
    $dst = Join-Path $Dist $d
    if (-not (Test-Path $src)) { continue }
    & robocopy $src $dst /MIR /NFL /NDL /NJH /NJS /NP /XD __pycache__ /XF *.tmp *.part | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $d ($LASTEXITCODE)" }
}
foreach ($f in "README.md", "requirements.txt", "start.bat", "start_kiosk.bat") {
    Copy-Item (Join-Path $Root $f) $Dist -Force
}

$baked = Join-Path $Dist "models\yoloe_exhibition.pt"
$pose = Join-Path $Dist "models\yolo26n-pose.pt"
if (-not (Test-Path $baked) -or -not (Test-Path $pose)) {
    Step "Preparing models inside the package"
    Push-Location $Dist
    Run $PyExe @("tools\prepare_models.py", "--device", $(if ($Cpu) { "cpu" } else { "0" }))
    Pop-Location
}

# ---------------------------------------------------------------- smoke test
Step "Smoke test"
Push-Location $Dist
Run $PyExe @("-c", "import torch, torchvision, cv2, ultralytics, fastapi, uvicorn, lap, websockets; import app.main; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), '| ultralytics', ultralytics.__version__)")
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
