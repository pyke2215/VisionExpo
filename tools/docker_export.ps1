<#
Build the Docker image and produce a delivery bundle:

  dist\VisionExpo-docker\
    vision-expo-image.tar     (docker save)
    docker-compose.yml        (run-only, GPU)
    config\                   (settings.json, vocabulary.json)
    data\                     (empty, SQLite goes here)
    install.bat / start.bat / stop.bat / logs.bat

Usage:
  powershell -ExecutionPolicy Bypass -File tools\docker_export.ps1 [-NoBuild] [-Cpu] [-Zip]
#>
param(
    [switch]$NoBuild,
    [switch]$Cpu,
    [switch]$Zip,
    [string]$Tag = "vision-expo:latest"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$DistRoot = Join-Path $Root "dist"
$Out = Join-Path $DistRoot "VisionExpo-docker"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Run([string]$exe, [string[]]$argv) {
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { throw "command failed ($LASTEXITCODE): $exe $($argv -join ' ')" }
}

& docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker engine is not running. Start Docker Desktop first." }

if (-not $NoBuild) {
    Step "docker build ($Tag)"
    $index = if ($Cpu) { "https://download.pytorch.org/whl/cpu" } else { "https://download.pytorch.org/whl/cu126" }
    Run "docker" @("build", "--build-arg", "TORCH_INDEX=$index", "-t", $Tag, $Root)
}

Step "bundle -> $Out"
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force -Path (Join-Path $Out "config"), (Join-Path $Out "data") | Out-Null
Copy-Item (Join-Path $Root "docker\deploy\*") $Out -Recurse
Copy-Item (Join-Path $Root "config\*.json") (Join-Path $Out "config")
if ($Cpu) {
    $compose = Join-Path $Out "docker-compose.yml"
    $text = Get-Content $compose -Raw
    $text = [regex]::Replace($text, "(?s)\s+deploy:.*$", "`n")
    Set-Content -Path $compose -Value $text -Encoding UTF8
}
if ($Tag -ne "vision-expo:latest") {
    $compose = Join-Path $Out "docker-compose.yml"
    (Get-Content $compose -Raw).Replace("vision-expo:latest", $Tag) | Set-Content -Path $compose -Encoding UTF8
}

Step "docker save"
Run "docker" @("save", "-o", (Join-Path $Out "vision-expo-image.tar"), $Tag)
$gb = [math]::Round((Get-ChildItem $Out -Recurse | Measure-Object Length -Sum).Sum / 1GB, 2)
Write-Host "bundle size: $gb GB"

if ($Zip) {
    Step "zip"
    $zipOut = Join-Path $DistRoot "VisionExpo-docker.zip"
    if (Test-Path $zipOut) { Remove-Item -Force $zipOut }
    $sevenZip = @("$env:ProgramFiles\7-Zip\7z.exe", "${env:ProgramFiles(x86)}\7-Zip\7z.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    Push-Location $DistRoot
    if ($sevenZip) {
        Run $sevenZip @("a", "-tzip", "-mx=5", $zipOut, "VisionExpo-docker")
    } else {
        Run "tar.exe" @("-a", "-c", "-f", $zipOut, "VisionExpo-docker")
    }
    Pop-Location
    Write-Host ("zip: {0} ({1} GB)" -f $zipOut, [math]::Round((Get-Item $zipOut).Length / 1GB, 2))
}

Write-Host "`nDone." -ForegroundColor Green
