$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "build-python-services.ps1")
& (Join-Path $PSScriptRoot "build-desktop-launcher.ps1")
& (Join-Path $PSScriptRoot "build-installer.ps1")
