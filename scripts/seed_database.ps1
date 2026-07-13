param(
    [switch]$SkipSchema,
    [switch]$LegacySchemaRepair
)

$ErrorActionPreference = "Stop"

$arguments = @("run", "python", "-m", "app.seed_data")
if ($SkipSchema) {
    $arguments += "--skip-schema"
}
if ($LegacySchemaRepair) {
    $arguments += "--legacy-schema-repair"
}

uv @arguments
