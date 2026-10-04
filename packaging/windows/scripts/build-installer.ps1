param(
    [switch]$OfflineAdmin,
    [switch]$PrepackageDependencies,
    [string]$SeedIndexBundlePath = ""
)

$ErrorActionPreference = "Stop"

if ($SeedIndexBundlePath) {
    $bundleManifestPath = Join-Path $SeedIndexBundlePath "manifest.json"
    if (-not (Test-Path -LiteralPath $bundleManifestPath -PathType Leaf)) {
        throw "Seed index bundle manifest was not found: $bundleManifestPath"
    }
    $bundleManifest = Get-Content -LiteralPath $bundleManifestPath -Raw | ConvertFrom-Json
    $expectedFormat = if ($OfflineAdmin) { "dr-transition-offline-seed-v1" } else { "dr-transition-seed-indexes-v1" }
    if ($bundleManifest.format -ne $expectedFormat) {
        throw "Seed index bundle format $($bundleManifest.format) does not match this installer type ($expectedFormat)."
    }
}

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$iss = Join-Path $root "packaging\windows\DrTransition.iss"
$requiredServiceBuilds = @(
    (Join-Path $root "dist\drtransition-backend")
    (Join-Path $root "dist\drtransition-grounding")
)

foreach ($requiredServiceBuild in $requiredServiceBuilds) {
    if (-not (Test-Path -LiteralPath $requiredServiceBuild)) {
        Write-Host "Missing Python service build: $requiredServiceBuild"
        Write-Host "Running build-python-services.ps1 first..."
        & (Join-Path $PSScriptRoot "build-python-services.ps1")
        break
    }
}

$assembleArgs = @{}
if ($PrepackageDependencies) {
    $assembleArgs.PrepackageDependencies = $true
}
if ($SeedIndexBundlePath) {
    $assembleArgs.SeedIndexBundlePath = $SeedIndexBundlePath
}
& (Join-Path $PSScriptRoot "assemble-installer-payload.ps1") @assembleArgs
$payloadPath = (Resolve-Path -LiteralPath (Join-Path $root "build\windows-installer\payload")).Path

$isccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
$iscc = if ($isccCommand) {
    $isccCommand.Source
} elseif (Test-Path "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") {
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
} elseif (Test-Path "$env:ProgramFiles\Inno Setup 6\ISCC.exe") {
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
} else {
    $null
}

if (-not $iscc) {
    throw "ISCC.exe was not found. Install Inno Setup 6 and ensure ISCC.exe is on PATH."
}

$isccArgs = @("/DInstallerPayloadPath=$payloadPath")
if ($OfflineAdmin) {
    $isccArgs += "/DOfflineAdminInstaller"
    Write-Host "Building offline admin installer with SQLite and local seeding; MySQL is not packaged."
}
if ($PrepackageDependencies) {
    $isccArgs += "/DPrepackageDependenciesInstaller"
    Write-Host "Building installer with prepackaged Ollama only."
} else {
    Write-Host "Building installer with online dependency setup."
}

$isccArgs += $iss
& $iscc @isccArgs
