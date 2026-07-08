$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$payload = Join-Path $root "build\windows-installer\payload"
$backendPayload = Join-Path $payload "backend"
$configPayload = Join-Path $payload "config"
$scriptsPayload = Join-Path $payload "scripts"

New-Item -ItemType Directory -Force -Path $backendPayload | Out-Null
New-Item -ItemType Directory -Force -Path $configPayload | Out-Null
New-Item -ItemType Directory -Force -Path $scriptsPayload | Out-Null

$serviceNames = @("drtransition-backend", "drtransition-reranker", "drtransition-nli")
foreach ($serviceName in $serviceNames) {
    $source = Join-Path $root "dist\$serviceName"
    if (-not (Test-Path $source)) {
        throw "Missing PyInstaller output: $source. Run build-python-services.ps1 first."
    }
    Copy-Item -Path $source -Destination $backendPayload -Recurse -Force
}

$tauriExe = Join-Path $root "desktop\tauri\src-tauri\target\release\drtransition.exe"
if (-not (Test-Path $tauriExe)) {
    throw "Missing Tauri executable: $tauriExe. Run npm install and npm run tauri:build in desktop\tauri first."
}
Copy-Item -LiteralPath $tauriExe -Destination (Join-Path $payload "DrTransition.exe") -Force

Copy-Item -LiteralPath (Join-Path $root "packaging\windows\config\default.config.json") -Destination $configPayload -Force
Copy-Item -LiteralPath (Join-Path $root "packaging\windows\scripts\Install-DrTransitionDependencies.ps1") -Destination $scriptsPayload -Force
Copy-Item -LiteralPath (Join-Path $root "packaging\windows\scripts\Test-SystemCompatibility.ps1") -Destination $scriptsPayload -Force
Copy-Item -LiteralPath (Join-Path $root "packaging\windows\scripts\Get-ModelRecommendation.ps1") -Destination $scriptsPayload -Force
Copy-Item -LiteralPath (Join-Path $root "schema.sql") -Destination $payload -Force

Write-Host "Installer payload assembled at $payload"
