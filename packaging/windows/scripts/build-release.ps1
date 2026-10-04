param(
    [ValidateSet("Patch", "Minor", "Major")]
    [string]$VersionPart = "Patch",
    [string]$Version = "",
    [switch]$NoVersionBump,
    [switch]$OfflineAdmin,
    [switch]$PrepackageDependencies,
    [string]$SeedIndexBundlePath = ""
)

$ErrorActionPreference = "Stop"

if ($SeedIndexBundlePath) {
    $bundleManifestPath = Join-Path $SeedIndexBundlePath "manifest.json"
    if (-not (Test-Path -LiteralPath $bundleManifestPath -PathType Leaf)) {
        throw "Seed index bundle manifest was not found: $bundleManifestPath. Create it first with create-seed-index-bundle.py --offline --output $SeedIndexBundlePath."
    }
    $bundleManifest = Get-Content -LiteralPath $bundleManifestPath -Raw | ConvertFrom-Json
    $expectedFormat = if ($OfflineAdmin) { "dr-transition-offline-seed-v1" } else { "dr-transition-seed-indexes-v1" }
    if ($bundleManifest.format -ne $expectedFormat) {
        throw "Seed index bundle format $($bundleManifest.format) does not match this installer type ($expectedFormat)."
    }
}

if (-not $NoVersionBump) {
    $versionArgs = @{
        Part = $VersionPart
    }
    if ($Version) {
        $versionArgs.Version = $Version
    }
    & (Join-Path $PSScriptRoot "increment-version.ps1") @versionArgs
} else {
    Write-Host "Windows build version bump skipped."
}

& (Join-Path $PSScriptRoot "build-python-services.ps1")
& (Join-Path $PSScriptRoot "build-desktop-launcher.ps1")
$installerArgs = @{}
if ($OfflineAdmin) {
    $installerArgs.OfflineAdmin = $true
}
if ($PrepackageDependencies) {
    $installerArgs.PrepackageDependencies = $true
}
if ($SeedIndexBundlePath) {
    $installerArgs.SeedIndexBundlePath = $SeedIndexBundlePath
}
& (Join-Path $PSScriptRoot "build-installer.ps1") @installerArgs
