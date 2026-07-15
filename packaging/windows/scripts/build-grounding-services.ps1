$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$specDir = Join-Path $root "packaging\windows\pyinstaller"
$modelCache = Join-Path $root "build\windows-installer\model-cache\huggingface"
$downloadScript = @'
import os

from sentence_transformers import CrossEncoder

models = [
    os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
    os.getenv("NLI_MODEL", "cross-encoder/nli-deberta-v3-small"),
]

for model in models:
    CrossEncoder(model, max_length=512)
    print(f"Cached grounding model: {model}")
'@

New-Item -ItemType Directory -Force -Path $modelCache | Out-Null

Push-Location $root
try {
    uv sync --extra grounding
    $env:HF_HOME = $modelCache
    $env:TRANSFORMERS_CACHE = $modelCache
    uv run --extra grounding python -c $downloadScript
    uv run --extra grounding --with pyinstaller pyinstaller --clean --noconfirm (Join-Path $specDir "drtransition-reranker.spec")
    uv run --extra grounding --with pyinstaller pyinstaller --clean --noconfirm (Join-Path $specDir "drtransition-nli.spec")
}
finally {
    Pop-Location
}
