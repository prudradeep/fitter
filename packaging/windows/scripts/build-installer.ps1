$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$iss = Join-Path $root "packaging\windows\DrTransition.iss"

& (Join-Path $PSScriptRoot "assemble-installer-payload.ps1")

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

& $iscc $iss
