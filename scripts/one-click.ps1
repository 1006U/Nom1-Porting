$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Jar = Join-Path $Root "game\nom1.jar"
if (-not (Test-Path $Jar)) {
    throw "game\nom1.jar not found. Put your NOM 1 JAR there first."
}

Write-Host "Preparing NOM 1 Android app..."
& (Join-Path $PSScriptRoot "setup.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "NOM setup failed."
}

Push-Location (Join-Path $Root "engine")
try {
    Write-Host "Building self-contained NOM 1 APK..."
    & .\gradlew.bat :app:assembleOpenDebug
    if ($LASTEXITCODE -ne 0) {
        throw "APK build failed."
    }

    $apk = Get-ChildItem ".\app\build\outputs\apk\open\debug\*.apk" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if ($null -eq $apk) {
        throw "Built APK was not found."
    }

    $dist = Join-Path $Root "dist"
    New-Item -ItemType Directory -Force -Path $dist | Out-Null
    $finalApk = Join-Path $dist "NOM1-debug.apk"
    Copy-Item $apk.FullName $finalApk -Force

    Write-Host ""
    Write-Host "APK ready:"
    Write-Host "  $finalApk"

    Write-Host ""
    Write-Host "APK generation complete. No device installation was attempted."
} finally {
    Pop-Location
}
