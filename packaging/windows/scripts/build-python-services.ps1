$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$specDir = Join-Path $root "packaging\windows\pyinstaller"
$distRoot = Join-Path $root "dist"
$legacyGroundingBuilds = @(
    (Join-Path $distRoot "drtransition-reranker"),
    (Join-Path $distRoot "drtransition-nli")
)

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

Push-Location $root
try {
    foreach ($legacyBuild in $legacyGroundingBuilds) {
        if (Test-Path -LiteralPath $legacyBuild) {
            $resolvedLegacyBuild = Resolve-Path -LiteralPath $legacyBuild
            if (-not $resolvedLegacyBuild.Path.StartsWith((Join-Path $distRoot ""), [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "Refusing to remove path outside dist: $($resolvedLegacyBuild.Path)"
            }
            Remove-Item -LiteralPath $resolvedLegacyBuild.Path -Recurse -Force
        }
    }
    Invoke-CheckedCommand uv sync --extra grounding
    Invoke-CheckedCommand uv run --with pyinstaller pyinstaller (Join-Path $specDir "drtransition-backend.spec") --noconfirm --clean
    Invoke-CheckedCommand uv run --with pyinstaller pyinstaller (Join-Path $specDir "drtransition-grounding.spec") --noconfirm --clean
}
finally {
    Pop-Location
}
