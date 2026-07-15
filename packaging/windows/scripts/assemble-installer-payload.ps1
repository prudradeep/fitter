$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$payload = Join-Path $root "build\windows-installer\payload"
$configPayload = Join-Path $payload "config"
$scriptsPayload = Join-Path $payload "scripts"
$frontendPayload = Join-Path $payload "frontend"
$referencePayload = Join-Path $payload "reference"
$servicesPayload = Join-Path $payload "services"
$modelsPayload = Join-Path $payload "models"

function Assert-UnderRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )
    $rootPath = [System.IO.Path]::GetFullPath($root.Path).TrimEnd('\')
    $targetPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (-not $targetPath.StartsWith($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside repository root: $targetPath"
    }
}

function Copy-ClientDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Missing required client asset directory: $Source"
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Copy-Item -Path (Join-Path $Source "*") -Destination $Destination -Recurse -Force
}

function Assert-NoServerArtifacts {
    $payloadRoot = [System.IO.Path]::GetFullPath($payload).TrimEnd('\') + '\'
    $forbiddenPatterns = @(
        '(^|[\\/])backend([\\/]|$)',
        '(^|[\\/])drtransition-backend(\.exe|[\\/]|$)',
        '(^|[\\/])app([\\/](routes|db|models|seed|schemas\.py|main\.py))',
        '(^|[\\/])schema\.sql$',
        '(^|[\\/])migrations?([\\/]|$)',
        '(^|[\\/])seeds?([\\/]|$)',
        '(^|[\\/])seed_database\.ps1$',
        '(^|[\\/])apply_migrations\.py$',
        '(^|[\\/])repair_legacy_schema\.py$',
        '(^|[\\/])Install-DrTransitionDependencies\.ps1$',
        'mysql',
        'database'
    )
    $violations = @()
    Get-ChildItem -LiteralPath $payload -Recurse -File | ForEach-Object {
        $fullName = [System.IO.Path]::GetFullPath($_.FullName)
        $relative = if ($fullName.StartsWith($payloadRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            $fullName.Substring($payloadRoot.Length)
        } else {
            $_.Name
        }
        foreach ($pattern in $forbiddenPatterns) {
            if ($relative -match $pattern) {
                $violations += $relative
                break
            }
        }
    }
    if ($violations.Count -gt 0) {
        $items = ($violations | Sort-Object -Unique) -join "`n - "
        throw "Installer payload contains server/database artifacts:`n - $items"
    }
}

Assert-UnderRoot -Path $payload
if (Test-Path -LiteralPath $payload) {
    Remove-Item -LiteralPath $payload -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $configPayload | Out-Null
New-Item -ItemType Directory -Force -Path $scriptsPayload | Out-Null
New-Item -ItemType Directory -Force -Path $frontendPayload | Out-Null
New-Item -ItemType Directory -Force -Path $referencePayload | Out-Null
New-Item -ItemType Directory -Force -Path $servicesPayload | Out-Null
New-Item -ItemType Directory -Force -Path $modelsPayload | Out-Null

$tauriExe = Join-Path $root "desktop\tauri\src-tauri\target\release\drtransition.exe"
if (-not (Test-Path $tauriExe)) {
    throw "Missing Tauri executable: $tauriExe. Run npm install and npm run tauri:build in desktop\tauri first."
}
Copy-Item -LiteralPath $tauriExe -Destination (Join-Path $payload "DrTransition.exe") -Force

Copy-ClientDirectory -Source (Join-Path $root "app\static") -Destination (Join-Path $frontendPayload "static")
Copy-ClientDirectory -Source (Join-Path $root "app\templates") -Destination (Join-Path $frontendPayload "templates")
Copy-ClientDirectory -Source (Join-Path $root "app\prompts") -Destination (Join-Path $referencePayload "prompts")

$rerankerBundle = Join-Path $root "dist\drtransition-reranker"
$nliBundle = Join-Path $root "dist\drtransition-nli"
if (-not (Test-Path (Join-Path $rerankerBundle "drtransition-reranker.exe"))) {
    throw "Missing reranker companion service bundle: $rerankerBundle. Run packaging\windows\scripts\build-grounding-services.ps1 first."
}
if (-not (Test-Path (Join-Path $nliBundle "drtransition-nli.exe"))) {
    throw "Missing NLI companion service bundle: $nliBundle. Run packaging\windows\scripts\build-grounding-services.ps1 first."
}
Copy-ClientDirectory -Source $rerankerBundle -Destination (Join-Path $servicesPayload "drtransition-reranker")
Copy-ClientDirectory -Source $nliBundle -Destination (Join-Path $servicesPayload "drtransition-nli")

$groundingModelCache = Join-Path $root "build\windows-installer\model-cache\huggingface"
if (-not (Test-Path $groundingModelCache)) {
    throw "Missing local grounding model cache: $groundingModelCache. Run packaging\windows\scripts\build-grounding-services.ps1 first."
}
Copy-ClientDirectory -Source $groundingModelCache -Destination (Join-Path $modelsPayload "huggingface")

Copy-Item -LiteralPath (Join-Path $root "packaging\windows\config\default.config.json") -Destination $configPayload -Force
Copy-Item -LiteralPath (Join-Path $root "packaging\windows\scripts\Test-SystemCompatibility.ps1") -Destination $scriptsPayload -Force
Copy-Item -LiteralPath (Join-Path $root "packaging\windows\scripts\Get-ModelRecommendation.ps1") -Destination $scriptsPayload -Force

Assert-NoServerArtifacts
Write-Host "Installer payload assembled at $payload"
