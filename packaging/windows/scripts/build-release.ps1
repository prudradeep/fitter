param(
    [ValidateSet("Patch", "Minor", "Major")]
    [string]$VersionPart = "Patch",
    [string]$Version = "",
    [switch]$NoVersionBump
)

$ErrorActionPreference = "Stop"

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

& (Join-Path $PSScriptRoot "build-desktop-launcher.ps1")
& (Join-Path $PSScriptRoot "build-installer.ps1")
