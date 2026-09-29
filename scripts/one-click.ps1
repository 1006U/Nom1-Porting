param(
    [switch]$BuildOnly
)

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

    if (-not $BuildOnly) {
        Write-Host ""
        Write-Host "Installing NOM 1 on the connected Android device..."
        & .\gradlew.bat :app:installOpenDebug
        if ($LASTEXITCODE -ne 0) {
            throw "Device installation failed. Check USB debugging and the connected device."
        }

        $adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
        if (Test-Path $adb) {
            Write-Host "Launching NOM 1..."
            & $adb shell am start -n "com.u1006.nom1.debug/ru.woesss.j2me.installer.Nom1LauncherActivity"
        }

        Write-Host ""
        Write-Host "Installed. From now on, tap the NOM 1 app icon on the phone."
    }
} finally {
    Pop-Location
}
