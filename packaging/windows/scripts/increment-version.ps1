param(
    [ValidateSet("Patch", "Minor", "Major")]
    [string]$Part = "Patch",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")

function Set-Utf8NoBomContent {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Value, $encoding)
}

function Get-ProjectVersion {
    $pyprojectPath = Join-Path $root "pyproject.toml"
    $content = Get-Content -LiteralPath $pyprojectPath -Raw
    $match = [regex]::Match($content, '(?m)^version\s*=\s*"(?<version>\d+\.\d+\.\d+)"')
    if (-not $match.Success) {
        throw "Could not find a semantic version in $pyprojectPath."
    }
    return $match.Groups["version"].Value
}

function Get-NextVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Current,
        [Parameter(Mandatory = $true)]
        [string]$Part
    )

    if ($Current -notmatch '^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)$') {
        throw "Version '$Current' is not in major.minor.patch format."
    }

    $major = [int]$Matches["major"]
    $minor = [int]$Matches["minor"]
    $patch = [int]$Matches["patch"]

    switch ($Part) {
        "Major" {
            $major += 1
            $minor = 0
            $patch = 0
        }
        "Minor" {
            $minor += 1
            $patch = 0
        }
        default {
            $patch += 1
        }
    }

    return "$major.$minor.$patch"
}

function Update-TextFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RelativePath,
        [Parameter(Mandatory = $true)]
        [string]$Pattern,
        [Parameter(Mandatory = $true)]
        [string]$Replacement
    )

    $path = Join-Path $root $RelativePath
    if (-not (Test-Path -LiteralPath $path)) {
        return
    }

    $content = Get-Content -LiteralPath $path -Raw
    if (-not [regex]::IsMatch($content, $Pattern)) {
        throw "No version value was found in $RelativePath."
    }
    $updated = [regex]::Replace($content, $Pattern, $Replacement)
    Set-Utf8NoBomContent -Path $path -Value $updated
}

function Update-JsonVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RelativePath,
        [Parameter(Mandatory = $true)]
        [string]$PropertyName,
        [Parameter(Mandatory = $true)]
        [string]$NewVersion
    )

    $path = Join-Path $root $RelativePath
    if (-not (Test-Path -LiteralPath $path)) {
        return
    }

    $content = Get-Content -LiteralPath $path -Raw
    $pattern = '("' + [regex]::Escape($PropertyName) + '"\s*:\s*")\d+\.\d+\.\d+(")'
    if (-not [regex]::IsMatch($content, $pattern)) {
        throw "Property '$PropertyName' was not found in $RelativePath."
    }
    $updated = [regex]::Replace($content, $pattern, "`${1}$NewVersion`${2}", 1)
    Set-Utf8NoBomContent -Path $path -Value $updated
}

function Update-PackageLockVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$NewVersion
    )

    $path = Join-Path $root "desktop\tauri\package-lock.json"
    if (-not (Test-Path -LiteralPath $path)) {
        return
    }

    $content = Get-Content -LiteralPath $path -Raw
    $topLevelPattern = [regex]'(?m)^  "version": "\d+\.\d+\.\d+"'
    $rootPackagePattern = [regex]'(?ms)("packages":\s*\{\s*"":\s*\{\s*"name":\s*"drtransition-desktop",\s*"version":\s*)"\d+\.\d+\.\d+"'
    if (-not $topLevelPattern.IsMatch($content) -or -not $rootPackagePattern.IsMatch($content)) {
        throw "Package-lock version values were not found."
    }
    $updated = $topLevelPattern.Replace(
        $content,
        "  `"version`": `"$NewVersion`"",
        1
    )
    $updated = $rootPackagePattern.Replace(
        $updated,
        "`${1}`"$NewVersion`"",
        1
    )
    Set-Utf8NoBomContent -Path $path -Value $updated
}

$currentVersion = Get-ProjectVersion
$newVersion = if ($Version.Trim()) {
    if ($Version -notmatch '^\d+\.\d+\.\d+$') {
        throw "-Version must be in major.minor.patch format."
    }
    $Version.Trim()
} else {
    Get-NextVersion -Current $currentVersion -Part $Part
}

Update-TextFile `
    -RelativePath "pyproject.toml" `
    -Pattern '(?m)^(version\s*=\s*")\d+\.\d+\.\d+(")' `
    -Replacement "`${1}$newVersion`${2}"

Update-JsonVersion `
    -RelativePath "desktop\tauri\src-tauri\tauri.conf.json" `
    -PropertyName "version" `
    -NewVersion $newVersion

Update-JsonVersion `
    -RelativePath "desktop\tauri\package.json" `
    -PropertyName "version" `
    -NewVersion $newVersion

Update-PackageLockVersion -NewVersion $newVersion

Update-TextFile `
    -RelativePath "desktop\tauri\src-tauri\Cargo.toml" `
    -Pattern '(?m)^(version\s*=\s*")\d+\.\d+\.\d+(")' `
    -Replacement "`${1}$newVersion`${2}"

Update-TextFile `
    -RelativePath "desktop\tauri\src-tauri\Cargo.lock" `
    -Pattern '(?ms)(\[\[package\]\]\s+name\s*=\s*"drtransition"\s+version\s*=\s*")\d+\.\d+\.\d+(")' `
    -Replacement "`${1}$newVersion`${2}"

Update-TextFile `
    -RelativePath "packaging\windows\DrTransition.iss" `
    -Pattern '(?m)^(#define\s+MyAppVersion\s+")\d+\.\d+\.\d+(")' `
    -Replacement "`${1}$newVersion`${2}"

Update-JsonVersion `
    -RelativePath "packaging\windows\config\default.config.json" `
    -PropertyName "appVersion" `
    -NewVersion $newVersion

Update-TextFile `
    -RelativePath "docs\WINDOWS_DESKTOP_INSTALLER.md" `
    -Pattern 'DrTransitionOnlineSetup-\d+\.\d+\.\d+\.exe' `
    -Replacement "DrTransitionOnlineSetup-$newVersion.exe"

Write-Host "Windows build version: $currentVersion -> $newVersion"
