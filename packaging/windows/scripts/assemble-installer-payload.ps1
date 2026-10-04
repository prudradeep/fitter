param(
    [switch]$PrepackageDependencies,
    [string]$SeedIndexBundlePath = ""
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$installerBuildRoot = Join-Path $root "build\windows-installer"
$payload = Join-Path $root "build\windows-installer\payload"
$backendPayload = Join-Path $payload "backend"
$configPayload = Join-Path $payload "config"
$scriptsPayload = Join-Path $payload "scripts"
$installersPayload = Join-Path $payload "installers"

if ($SeedIndexBundlePath) {
    $seedSource = (Resolve-Path -LiteralPath $SeedIndexBundlePath -ErrorAction Stop).Path
    $payloadFull = [System.IO.Path]::GetFullPath($payload)
    if ($seedSource.Equals($payloadFull, [System.StringComparison]::OrdinalIgnoreCase) -or
        $seedSource.StartsWith(($payloadFull.TrimEnd('\') + '\'), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Seed index bundle must be outside the generated payload directory."
    }
    $manifestPath = Join-Path $seedSource "manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Seed index bundle is missing manifest.json: $seedSource"
    }
    $seedManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($seedManifest.format -notin @("dr-transition-seed-indexes-v1", "dr-transition-offline-seed-v1") -or -not $seedManifest.indexes) {
        throw "Seed index bundle manifest is invalid: $manifestPath"
    }
    if ([string]::IsNullOrWhiteSpace($seedManifest.embedding_model)) {
        throw "Seed index bundle must declare its embedding model."
    }
    if ($seedManifest.format -eq "dr-transition-seed-indexes-v1" -and [string]::IsNullOrWhiteSpace($seedManifest.sync_server_url)) {
        throw "Sync seed index bundle must declare its sync server."
    }
    if ($seedManifest.format -eq "dr-transition-offline-seed-v1") {
        if ($seedManifest.database_file -ne "seed.db") {
            throw "Offline seed bundle must include seed.db."
        }
        $seedDatabase = Join-Path $seedSource "seed.db"
        if (-not (Test-Path -LiteralPath $seedDatabase -PathType Leaf)) {
            throw "Offline seed database is missing: $seedDatabase"
        }
        $actualDatabaseHash = (Get-FileHash -LiteralPath $seedDatabase -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualDatabaseHash -ne [string]$seedManifest.database_sha256) {
            throw "Offline seed database checksum mismatch: $seedDatabase"
        }
    }
    $seenScopes = @{}
    foreach ($entry in $seedManifest.indexes) {
        if ($seenScopes.ContainsKey([string]$entry.scope)) {
            throw "Duplicate seed index scope: $($entry.scope)"
        }
        $seenScopes[[string]$entry.scope] = $true
        $suffix = switch ($entry.scope) {
            "main" { "main" }
            "sector_prompt" { "sector_prompts" }
            "policy_document" { "policy_reference" }
            default { throw "Unsupported seed index scope: $($entry.scope)" }
        }
        if ($entry.file -ne "knowledge.$suffix.faiss") {
            throw "Unexpected seed index filename: $($entry.file)"
        }
        $sourceFile = Join-Path $seedSource $entry.file
        if (-not (Test-Path -LiteralPath $sourceFile -PathType Leaf)) {
            throw "Seed index file is missing: $sourceFile"
        }
        $actualHash = (Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne [string]$entry.sha256) {
            throw "Seed index checksum mismatch: $sourceFile"
        }
    }
}

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

if ($SeedIndexBundlePath) {
    $seedPayload = Join-Path $payload "seed-indexes"
    New-Item -ItemType Directory -Force -Path $seedPayload | Out-Null
    Copy-Item -LiteralPath $manifestPath -Destination $seedPayload -Force
    foreach ($entry in $seedManifest.indexes) {
        Copy-Item -LiteralPath (Join-Path $seedSource $entry.file) -Destination $seedPayload -Force
    }
    if ($seedManifest.format -eq "dr-transition-offline-seed-v1") {
        Copy-Item -LiteralPath $seedDatabase -Destination $seedPayload -Force
    }
    Write-Host "Packaged seed FAISS indexes from $seedSource"
}

if ($PrepackageDependencies) {
    $offlineRoot = Join-Path $root "packaging\windows\offline"
    $ollamaOffline = Join-Path $offlineRoot "ollama"
    $ollamaPayload = Join-Path $installersPayload "ollama"

    $ollamaInstaller = Get-ChildItem -LiteralPath $ollamaOffline -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ieq "OllamaSetup.exe" -or $_.Extension -eq ".exe" } |
        Select-Object -First 1
    if (-not $ollamaInstaller) {
        throw "Missing offline Ollama installer. Place OllamaSetup.exe in $ollamaOffline."
    }

    New-Item -ItemType Directory -Force -Path $ollamaPayload | Out-Null
    Copy-Item -LiteralPath $ollamaInstaller.FullName -Destination $ollamaPayload -Force
    Write-Host "Prepackaged dependency installer:"
    Write-Host "  Ollama: $($ollamaInstaller.Name)"
}


Write-Host "Installer payload assembled at $payload"
