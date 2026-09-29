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
    if ($LASTEXITCODE -ne 0) {
        throw "Could not clone J2ME Loader."
    }
} else {
    Write-Host "Using existing engine checkout: $Engine"

    Write-Host "Refreshing upstream files patched by NOM..."
    $PatchedUpstreamFiles = @(
        "build.gradle",
        "app/build.gradle",
        "app/src/main/AndroidManifest.xml",
        "app/src/main/java/ru/woesss/j2me/installer/AppInstaller.java",
        "app/src/main/java/javax/microedition/lcdui/Canvas.java"
    )
    foreach ($File in $PatchedUpstreamFiles) {
        git -C $Engine checkout -- $File
        if ($LASTEXITCODE -ne 0) {
            throw ("Could not restore upstream engine file: " + $File)
        }
    }
}

Write-Host "Checking Korean font patch dependencies..."
$PillowInstalled = python -c "import importlib.util; print('yes' if importlib.util.find_spec('PIL') else 'no')"
if ($LASTEXITCODE -ne 0) {
    throw "Python could not check for Pillow. Verify that Python is installed and available as 'python'."
}

if ($PillowInstalled.Trim() -ne "yes") {
    Write-Host "Pillow is not installed. Installing it now..."
    python -m pip install --user Pillow
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install Pillow. Run manually: python -m pip install --user Pillow"
    }

    $PillowInstalled = python -c "import importlib.util; print('yes' if importlib.util.find_spec('PIL') else 'no')"
    if ($LASTEXITCODE -ne 0 -or $PillowInstalled.Trim() -ne "yes") {
        throw "Pillow installation completed but Python still cannot import PIL."
    }
}

$PatchedJar = "game/generated/nom1-ko.jar"
Write-Host "Creating Korean NOM 1 JAR..."
python tools/patch_korean.py --input $Jar --output $PatchedJar
if ($LASTEXITCODE -ne 0) {
    throw "Korean JAR patch failed."
}

python tools/prepare_engine.py --engine $Engine --jar $PatchedJar
if ($LASTEXITCODE -ne 0) {
    throw "NOM engine patch failed."
}

Write-Host ""
Write-Host "NOM 1 port workspace is ready."
Write-Host "Open '$Engine' in Android Studio, or run:"
Write-Host "  cd $Engine"
Write-Host "  .\gradlew.bat :app:assembleOpenDebug"
