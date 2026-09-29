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

Write-Host "Checking Korean font patch dependencies..."
python -c "import PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing Pillow for Korean bitmap font generation..."
    python -m pip install --user Pillow
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install Pillow. Run: python -m pip install Pillow"
    }
}

$PatchedJar = "game/generated/nom1-ko.jar"
Write-Host "Creating Korean NOM 1 JAR..."
python tools/patch_korean.py --input $Jar --output $PatchedJar
if ($LASTEXITCODE -ne 0) {
    throw "Korean JAR patch failed."
}

python tools/prepare_engine.py --engine $Engine --jar $PatchedJar

Write-Host ""
Write-Host "NOM 1 port workspace is ready."
Write-Host "Open '$Engine' in Android Studio, or run:"
Write-Host "  cd $Engine"
Write-Host "  .\gradlew.bat :app:assembleOpenDebug"
