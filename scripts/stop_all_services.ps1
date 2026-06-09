$ErrorActionPreference = "Stop"

$workspace = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $workspace "data\service-runtime"
$serviceNames = @("app", "reranker", "nli")

foreach ($serviceName in $serviceNames) {
    $pidFile = Join-Path $runtimeDir "$serviceName.pid"
    if (-not (Test-Path $pidFile)) {
        Write-Host "$serviceName has no tracked PID file."
        continue
    }

    $servicePid = [int](Get-Content $pidFile -ErrorAction SilentlyContinue)
    $process = Get-Process -Id $servicePid -ErrorAction SilentlyContinue
    if ($process) {
        & taskkill.exe /PID $servicePid /T /F | Out-Null
        Write-Host "Stopped $serviceName process tree (PID $servicePid)."
    } else {
        Write-Host "$serviceName was not running."
    }
    Remove-Item -LiteralPath $pidFile -Force
}
