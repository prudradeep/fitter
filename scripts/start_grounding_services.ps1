$ErrorActionPreference = "Stop"

$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Virtual environment not found. Run 'uv sync --extra grounding' first."
}

$logDir = Join-Path $workspace "data\grounding-logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$services = @(
    @{
        Name = "reranker"
        App = "app.grounding_servers.reranker:app"
        Port = "8081"
    },
    @{
        Name = "nli"
        App = "app.grounding_servers.nli:app"
        Port = "8082"
    }
)

foreach ($service in $services) {
    $stdout = Join-Path $logDir "$($service.Name).out.log"
    $stderr = Join-Path $logDir "$($service.Name).err.log"
    Start-Process `
        -FilePath $python `
        -ArgumentList "-m", "uvicorn", $service.App, "--host", "127.0.0.1", "--port", $service.Port `
        -WorkingDirectory $workspace `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr
}

Write-Host "Grounding services started:"
Write-Host "  Reranker: http://localhost:8081/health"
Write-Host "  NLI:      http://localhost:8082/health"
