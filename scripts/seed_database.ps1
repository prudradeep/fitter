param(
    [switch]$SkipSchema
)

$ErrorActionPreference = "Stop"

$arguments = @("run", "python", "-m", "app.seed_data")
if ($SkipSchema) {
    $arguments += "--skip-schema"
}

uv @arguments
