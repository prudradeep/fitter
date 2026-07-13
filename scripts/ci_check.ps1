param(
    [switch]$SkipRuff
)

$ErrorActionPreference = "Stop"
$python = Join-Path (Resolve-Path ".") ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

$ciTemp = Join-Path (Resolve-Path ".") ".tmp\ci"
New-Item -ItemType Directory -Force -Path $ciTemp | Out-Null
$env:TEMP = $ciTemp
$env:TMP = $ciTemp

function Invoke-CiCommand {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Write-Host "== Python compile =="
Invoke-CiCommand { & $python -m compileall -q app tests }
Invoke-CiCommand { & $python -m py_compile scripts\apply_migrations.py }
Invoke-CiCommand { & $python -m py_compile scripts\cleanup_retained_data.py }
Invoke-CiCommand { & $python -m py_compile scripts\repair_legacy_schema.py }

Write-Host "== PowerShell syntax =="
Invoke-CiCommand { [scriptblock]::Create((Get-Content packaging\windows\scripts\increment-version.ps1 -Raw)) | Out-Null }
Invoke-CiCommand { [scriptblock]::Create((Get-Content packaging\windows\scripts\build-release.ps1 -Raw)) | Out-Null }

Write-Host "== Unit tests =="
Invoke-CiCommand { & $python -m unittest discover -s tests }

if (-not $SkipRuff) {
    Write-Host "== Ruff =="
    Invoke-CiCommand { & $python -m ruff check app tests }
}

if (Get-Command node -ErrorAction SilentlyContinue) {
    Write-Host "== Frontend syntax =="
    Invoke-CiCommand { node --check app\static\js\app-utils.js }
    Invoke-CiCommand { node --check app\static\js\app-settings.js }
    Invoke-CiCommand { node --check app\static\js\app-dom-icons.js }
    Invoke-CiCommand { node --check app\static\js\methodology.js }
    Invoke-CiCommand { node --check app\static\js\app.js }
} else {
    Write-Warning "Node.js not found; skipping frontend syntax check."
}

if ($env:DR_TRANSITION_BROWSER_TESTS -eq "1") {
    Write-Host "== Browser smoke tests =="
    Invoke-CiCommand { & $python -m unittest discover -s tests\browser }
} else {
    Write-Host "== Browser smoke tests skipped =="
    Write-Host "Set DR_TRANSITION_BROWSER_TESTS=1 after starting the app to run tests\browser."
}
