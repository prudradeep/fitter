param(
    [switch]$PrepackageDependencies
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$iss = Join-Path $root "packaging\windows\DrTransitionMySqlOllama.iss"
$fullInstallerIss = Join-Path $root "packaging\windows\DrTransition.iss"
$installerBuildRoot = Join-Path $root "build\windows-dependencies-installer"
$payload = Join-Path $installerBuildRoot "payload"
$backendPayload = Join-Path $payload "backend"
$scriptsPayload = Join-Path $payload "scripts"
$installersPayload = Join-Path $payload "installers"
$backendBuild = Join-Path $root "dist\drtransition-backend"

if (Test-Path -LiteralPath $payload) {
    $resolvedPayload = Resolve-Path -LiteralPath $payload
    if (-not $resolvedPayload.Path.StartsWith((Join-Path $installerBuildRoot ""), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside build\windows-dependencies-installer: $($resolvedPayload.Path)"
    }
    Remove-Item -LiteralPath $resolvedPayload.Path -Recurse -Force
}

if (-not (Test-Path -LiteralPath $backendBuild)) {
    Write-Host "Missing Python backend build: $backendBuild"
    Write-Host "Running build-python-services.ps1 first..."
    & (Join-Path $PSScriptRoot "build-python-services.ps1")
}

if (-not (Test-Path -LiteralPath $backendBuild)) {
    throw "Missing PyInstaller backend output: $backendBuild. Run build-python-services.ps1 first."
}

New-Item -ItemType Directory -Force -Path $backendPayload | Out-Null
New-Item -ItemType Directory -Force -Path $scriptsPayload | Out-Null
Copy-Item -Path $backendBuild -Destination $backendPayload -Recurse -Force
Copy-Item -LiteralPath (Join-Path $root "packaging\windows\scripts\Install-DrTransitionDependencies.ps1") -Destination $scriptsPayload -Force
Copy-Item -LiteralPath (Join-Path $root "packaging\windows\scripts\Get-ModelRecommendation.ps1") -Destination $scriptsPayload -Force
Copy-Item -LiteralPath (Join-Path $root "schema.sql") -Destination $payload -Force

if ($PrepackageDependencies) {
    $offlineRoot = Join-Path $root "packaging\windows\offline"
    $ollamaOffline = Join-Path $offlineRoot "ollama"
    $ollamaPayload = Join-Path $installersPayload "ollama"

    $ollamaInstaller = Get-ChildItem -LiteralPath $ollamaOffline -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ieq "OllamaSetup.exe" -or $_.Extension -eq ".exe" } |
        Sort-Object Name |
        Select-Object -First 1
    if (-not $ollamaInstaller) {
        throw "Missing offline Ollama installer. Place OllamaSetup.exe in $ollamaOffline."
    }

    New-Item -ItemType Directory -Force -Path $ollamaPayload | Out-Null
    Copy-Item -LiteralPath $ollamaInstaller.FullName -Destination $ollamaPayload -Force
    Write-Host "Prepackaged dependency installer:"
    Write-Host "  Ollama: $($ollamaInstaller.Name)"
}


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

$version = "0.1.0"
$fullInstallerText = Get-Content -LiteralPath $fullInstallerIss -Raw
if ($fullInstallerText -match '#define\s+MyAppVersion\s+"([^"]+)"') {
    $version = $Matches[1]
}

Write-Host "Dependency installer payload assembled at $payload"
Write-Host "Building SQLite/Ollama offline dependency installer version $version"
$isccArgs = @($iss, "/DMyAppVersion=$version")
if ($PrepackageDependencies) {
    $isccArgs += "/DPrepackageDependenciesInstaller"
    Write-Host "Building SQLite/Ollama dependency installer with prepackaged Ollama."
} else {
    Write-Host "Building SQLite/Ollama dependency installer with online Ollama setup."
}
& $iscc @isccArgs
