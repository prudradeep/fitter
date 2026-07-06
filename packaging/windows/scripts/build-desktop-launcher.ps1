$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$desktopDir = Join-Path $root "desktop\tauri"
$uiDir = Join-Path $desktopDir "ui"
$iconPath = Join-Path $desktopDir "src-tauri\icons\icon.ico"

if (-not (Test-Path (Join-Path $uiDir "index.html"))) {
    throw "Missing Tauri frontend placeholder at $uiDir\index.html."
}

if (-not (Test-Path $iconPath)) {
    & (Join-Path $PSScriptRoot "New-TauriIcon.ps1") `
        -SourcePng (Join-Path $root "app\static\img\logo.png") `
        -DestinationIco $iconPath
}

Push-Location $desktopDir
try {
    npm install
    npm run tauri:build
}
finally {
    Pop-Location
}
