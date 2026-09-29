param(
    [string]$Jar = "game/nom1.jar",
    [string]$Engine = "engine"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Upstream = "https://github.com/nikita36078/J2ME-Loader.git"
$Tag = "1.8.2"

if (-not (Test-Path $Jar)) {
    throw ("NOM JAR not found: " + $Jar + [Environment]::NewLine + "Place your legally obtained game at game/nom1.jar.")
}

if (-not (Test-Path (Join-Path $Engine ".git"))) {
    Write-Host "Cloning J2ME Loader $Tag..."
    git clone --depth 1 --branch $Tag $Upstream $Engine
} else {
    Write-Host "Using existing engine checkout: $Engine"
}

python tools/prepare_engine.py --engine $Engine --jar $Jar

Write-Host ""
Write-Host "NOM 1 port workspace is ready."
Write-Host "Open '$Engine' in Android Studio, or run:"
Write-Host "  cd $Engine"
Write-Host "  .\gradlew.bat :app:assembleOpenDebug"
