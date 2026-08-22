$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$specDir = Join-Path $root "packaging\windows\pyinstaller"
$distRoot = Join-Path $root "dist"
$legacyGroundingBuilds = @(
    (Join-Path $distRoot "drtransition-reranker"),
    (Join-Path $distRoot "drtransition-nli")
)

# These files/directories are consumed by --seed-database for a fresh SQLite client.
# Fail the build instead of shipping an installer that can only create an empty schema.
$requiredSeedAssets = @(
    (Join-Path $root "app\prompts"),
    (Join-Path $root "kb"),
    (Join-Path $root "mm.csv"),
    (Join-Path $root "MM Target group.xlsx"),
    (Join-Path $root "sectoral_challenges.xlsx"),
    (Join-Path $root "hazards.xlsx"),
    (Join-Path $root "additionalHazards.csv"),
    (Join-Path $root "additionalHazardProfiles.csv")
)

foreach ($seedAsset in $requiredSeedAssets) {
    if (-not (Test-Path -LiteralPath $seedAsset)) {
        throw "Required SQLite seed asset is missing: $seedAsset"
    }
}

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
    Invoke-CheckedCommand uv sync --extra grounding --extra client
    Invoke-CheckedCommand uv run --with pyinstaller pyinstaller (Join-Path $specDir "drtransition-backend.spec") --noconfirm --clean
    Invoke-CheckedCommand uv run --with pyinstaller pyinstaller (Join-Path $specDir "drtransition-grounding.spec") --noconfirm --clean
}
finally {
    Pop-Location
}
