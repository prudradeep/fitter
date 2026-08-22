param(
    [switch]$OfflineAdmin,
    [switch]$PrepackageDependencies
)

$ErrorActionPreference = "Stop"

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
& (Join-Path $PSScriptRoot "assemble-installer-payload.ps1") @assembleArgs

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

$isccArgs = @($iss)
if ($OfflineAdmin) {
    $isccArgs += "/DOfflineAdminInstaller"
    Write-Host "Building offline admin installer with local seeding enabled."
}
if ($PrepackageDependencies) {
    $isccArgs += "/DPrepackageDependenciesInstaller"
    Write-Host "Building installer with prepackaged dependency installers."
} else {
    Write-Host "Building installer with online dependency setup."
}

& $iscc @isccArgs
