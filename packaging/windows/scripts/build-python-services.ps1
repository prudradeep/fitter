$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$specDir = Join-Path $root "packaging\windows\pyinstaller"

Push-Location $root
try {
    uv sync --extra grounding
    uv run --with pyinstaller pyinstaller (Join-Path $specDir "drtransition-backend.spec") --noconfirm --clean
    uv run --with pyinstaller pyinstaller (Join-Path $specDir "drtransition-reranker.spec") --noconfirm --clean
    uv run --with pyinstaller pyinstaller (Join-Path $specDir "drtransition-nli.spec") --noconfirm --clean
}
finally {
    Pop-Location
}
