$ErrorActionPreference = "Stop"

$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Virtual environment not found. Run 'uv sync --extra grounding' first."
}

$runtimeDir = Join-Path $workspace "data\service-runtime"
$logDir = Join-Path $runtimeDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$services = @(
   <# @{
        Name = "app"
        Arguments = @(
            "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1",
            "--port", "8000",
            "--reload",
            "--reload-dir", "app"
        )
        Health = "http://localhost:8000/health"
    }, #>
    @{
        Name = "reranker"
        Arguments = @(
            "-m", "uvicorn", "app.grounding_servers.reranker:app",
            "--host", "127.0.0.1",
            "--port", "8081"
        )
        Health = "http://localhost:8081/health"
    },
    @{
        Name = "nli"
        Arguments = @(
            "-m", "uvicorn", "app.grounding_servers.nli:app",
            "--host", "127.0.0.1",
            "--port", "8082"
        )
        Health = "http://localhost:8082/health"
    }
)

foreach ($service in $services) {
    $pidFile = Join-Path $runtimeDir "$($service.Name).pid"
    if (Test-Path $pidFile) {
        $existingPid = [int](Get-Content $pidFile -ErrorAction SilentlyContinue)
        if ($existingPid -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
            Write-Host "$($service.Name) is already running with PID $existingPid."
            continue
        }
        Remove-Item -LiteralPath $pidFile -Force
    }

    $stdout = Join-Path $logDir "$($service.Name).out.log"
    $stderr = Join-Path $logDir "$($service.Name).err.log"
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList $service.Arguments `
        -WorkingDirectory $workspace `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru
    Set-Content -LiteralPath $pidFile -Value $process.Id
    Write-Host "Started $($service.Name) with PID $($process.Id)."
}

Write-Host ""
Write-Host "Services:"
foreach ($service in $services) {
    Write-Host "  $($service.Name): $($service.Health)"
}
Write-Host ""
Write-Host "Stop all services with: .\scripts\stop_all_services.ps1"
