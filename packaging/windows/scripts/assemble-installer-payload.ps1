param(
    [switch]$PrepackageDependencies
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$installerBuildRoot = Join-Path $root "build\windows-installer"
$payload = Join-Path $root "build\windows-installer\payload"
$backendPayload = Join-Path $payload "backend"
$configPayload = Join-Path $payload "config"
$scriptsPayload = Join-Path $payload "scripts"
$installersPayload = Join-Path $payload "installers"

if (Test-Path -LiteralPath $payload) {
    $resolvedPayload = Resolve-Path -LiteralPath $payload
    if (-not $resolvedPayload.Path.StartsWith((Join-Path $installerBuildRoot ""), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside build\windows-installer: $($resolvedPayload.Path)"
    }
    Remove-Item -LiteralPath $resolvedPayload.Path -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $backendPayload | Out-Null
New-Item -ItemType Directory -Force -Path $configPayload | Out-Null
New-Item -ItemType Directory -Force -Path $scriptsPayload | Out-Null

$serviceNames = @("drtransition-backend", "drtransition-grounding")
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

if ($PrepackageDependencies) {
    $offlineRoot = Join-Path $root "packaging\windows\offline"
    $mysqlOffline = Join-Path $offlineRoot "mysql"
    $ollamaOffline = Join-Path $offlineRoot "ollama"
    $mysqlPayload = Join-Path $installersPayload "mysql"
    $ollamaPayload = Join-Path $installersPayload "ollama"

    $preferredMySqlInstaller = Join-Path $mysqlOffline "mysql-8.4.11-winx64.msi"
    $mysqlInstaller = if (Test-Path -LiteralPath $preferredMySqlInstaller -PathType Leaf) {
        Get-Item -LiteralPath $preferredMySqlInstaller
    } else {
        Get-ChildItem -LiteralPath $mysqlOffline -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in @(".msi", ".exe") } |
            Sort-Object Name |
            Select-Object -First 1
    }
    if (-not $mysqlInstaller) {
        throw "Missing offline MySQL installer. Place a MySQL Server .msi/.exe in $mysqlOffline."
    }

    $ollamaInstaller = Get-ChildItem -LiteralPath $ollamaOffline -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ieq "OllamaSetup.exe" -or $_.Extension -eq ".exe" } |
        Select-Object -First 1
    if (-not $ollamaInstaller) {
        throw "Missing offline Ollama installer. Place OllamaSetup.exe in $ollamaOffline."
    }

    New-Item -ItemType Directory -Force -Path $mysqlPayload | Out-Null
    New-Item -ItemType Directory -Force -Path $ollamaPayload | Out-Null
    Copy-Item -LiteralPath $mysqlInstaller.FullName -Destination $mysqlPayload -Force
    Copy-Item -LiteralPath $ollamaInstaller.FullName -Destination $ollamaPayload -Force
    Write-Host "Prepackaged dependency installers:"
    Write-Host "  MySQL: $($mysqlInstaller.Name)"
    Write-Host "  Ollama: $($ollamaInstaller.Name)"
}

Write-Host "Installer payload assembled at $payload"
