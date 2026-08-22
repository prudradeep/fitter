param(
    [string]$InstallDir,

    [string]$ConfigPath = "",

    [string]$DbName = "dr_transition",

    [string]$MySqlAdminUser = "root",
    [string]$MySqlAdminPassword = "",
    [string]$AppDbUser = "dr_transition",
    [string]$AppDbPassword = "",
    [string]$DefaultAppUserEmail = "admin@drtransition.local",
    [string]$DefaultAppUserPassword = "",
    [string]$DefaultAppUserName = "Dr Transition Admin",
    [string]$DefaultAppUserDesignation = "Administrator",
    [string]$DefaultAppUserOrganisationType = "Local",
    [string]$DefaultAppUserOrganisationName = "Dr Transition",
    [string]$DefaultAppUserRole = "auto",
    [string]$DefaultAppUserCredentialsPath = "",
    [string]$OllamaModel = "",
    [string]$OllamaEmbeddingModel = "nomic-embed-text",
    [string]$OllamaBaseUrl = "http://127.0.0.1:11434",
    [switch]$InstallMySql,
    [switch]$InstallOllama,
    [switch]$PullModels,
    [switch]$DisableSync,
    [switch]$IncludeBasicData,
    [switch]$SeedPromptsFromFiles,
    [switch]$ReindexSectorPrompts,
    [switch]$SeedMainKbFromFiles,
    [switch]$SkipDefaultAppUser,
    [switch]$SkipReferenceData,
    [switch]$SkipDatabaseSeed,
    [switch]$DependenciesOnly
)

$ErrorActionPreference = "Stop"

$programData = Join-Path $env:ProgramData "DrTransition"
$logDir = Join-Path $env:LOCALAPPDATA "DrTransition\logs"
$runtimeDataDir = Join-Path $programData "data"
$runtimeLogDir = Join-Path $runtimeDataDir "logs"
$envPath = Join-Path $programData ".env"
$runtimeTemplate = Join-Path $InstallDir "config\.env"
$backendExe = Join-Path $InstallDir "backend\drtransition-backend\drtransition-backend.exe"
$setupLog = Join-Path $logDir "installer-setup.log"
$script:MySqlInitializationOutput = ""

New-Item -ItemType Directory -Force -Path $programData | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path $runtimeLogDir | Out-Null

if (-not [string]::IsNullOrWhiteSpace($ConfigPath) -and (Test-Path -LiteralPath $ConfigPath)) {
    $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    foreach ($property in $config.PSObject.Properties) {
        switch ($property.Name) {
            "DbName" { $DbName = [string]$property.Value }
            "MySqlAdminUser" { $MySqlAdminUser = [string]$property.Value }
            "MySqlAdminPassword" { $MySqlAdminPassword = [string]$property.Value }
            "AppDbUser" { $AppDbUser = [string]$property.Value }
            "AppDbPassword" { $AppDbPassword = [string]$property.Value }
            "DefaultAppUserEmail" { $DefaultAppUserEmail = [string]$property.Value }
            "DefaultAppUserPassword" { $DefaultAppUserPassword = [string]$property.Value }
            "DefaultAppUserName" { $DefaultAppUserName = [string]$property.Value }
            "DefaultAppUserDesignation" { $DefaultAppUserDesignation = [string]$property.Value }
            "DefaultAppUserOrganisationType" { $DefaultAppUserOrganisationType = [string]$property.Value }
            "DefaultAppUserOrganisationName" { $DefaultAppUserOrganisationName = [string]$property.Value }
            "DefaultAppUserRole" { $DefaultAppUserRole = [string]$property.Value }
            "DefaultAppUserCredentialsPath" { $DefaultAppUserCredentialsPath = [string]$property.Value }
            "OllamaModel" { $OllamaModel = [string]$property.Value }
            "OllamaEmbeddingModel" { $OllamaEmbeddingModel = [string]$property.Value }
            "OllamaBaseUrl" { $OllamaBaseUrl = [string]$property.Value }
            "InstallMySql" { if ([bool]$property.Value) { $InstallMySql = $true } }
            "InstallOllama" { if ([bool]$property.Value) { $InstallOllama = $true } }
            "PullModels" { if ([bool]$property.Value) { $PullModels = $true } }
            "DisableSync" { if ([bool]$property.Value) { $DisableSync = $true } }
            "IncludeBasicData" { if ([bool]$property.Value) { $IncludeBasicData = $true } }
            "SeedPromptsFromFiles" { if ([bool]$property.Value) { $SeedPromptsFromFiles = $true } }
            "ReindexSectorPrompts" { if ([bool]$property.Value) { $ReindexSectorPrompts = $true } }
            "SeedMainKbFromFiles" { if ([bool]$property.Value) { $SeedMainKbFromFiles = $true } }
            "SkipDefaultAppUser" { if ([bool]$property.Value) { $SkipDefaultAppUser = $true } }
            "SkipReferenceData" { if ([bool]$property.Value) { $SkipReferenceData = $true } }
            "SkipDatabaseSeed" { if ([bool]$property.Value) { $SkipDatabaseSeed = $true } }
            "DependenciesOnly" { if ([bool]$property.Value) { $DependenciesOnly = $true } }
        }
    }
}

function Write-SetupLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $setupLog -Value $line -Encoding UTF8
    Write-Host $line
}

function Wait-SetupProcess {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$Activity,
        [int]$HeartbeatSeconds = 15
    )

    $started = Get-Date
    while (-not $Process.HasExited) {
        Start-Sleep -Seconds $HeartbeatSeconds
        if (-not $Process.HasExited) {
            $elapsed = [int]((Get-Date) - $started).TotalSeconds
            Write-SetupLog "$Activity is still running (${elapsed}s elapsed)"
        }
    }
    $Process.WaitForExit()
    $Process.Refresh()
    return $Process.ExitCode
}

function Write-SetupProgress {
    param(
        [string]$Activity,
        [int]$Percent,
        [string]$Status = ""
    )

    $Percent = [math]::Max(0, [math]::Min(100, $Percent))
    $message = if ([string]::IsNullOrWhiteSpace($Status)) {
        "[PROGRESS] $Activity|$Percent"
    } else {
        "[PROGRESS] $Activity|$Percent|$Status"
    }

    # Keep machine-readable progress in the setup log without echoing every
    # update into the visible console. Frequent progress log lines otherwise
    # collide with native carriage-return progress bars during large downloads.
    Add-Content -LiteralPath $setupLog -Value ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $message) -Encoding UTF8
    Write-Progress -Activity $Activity -Status $Status -PercentComplete $Percent
}

function Invoke-VisibleSetupCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$Activity,
        [int]$StartPercent = 0,
        [int]$EndPercent = 100
    )

    Write-SetupLog "Starting $Activity"
    Write-SetupProgress -Activity $Activity -Percent $StartPercent -Status "Starting..."

    $stdoutPath = Join-Path $env:TEMP ("drtransition-native-out-" + [guid]::NewGuid() + ".log")
    $stderrPath = Join-Path $env:TEMP ("drtransition-native-err-" + [guid]::NewGuid() + ".log")
    $progressState = @{
        LastPercent = $StartPercent
        LastProgressAt = Get-Date
        LastConsolePercent = -1
        LastConsoleStatus = ""
        StdoutLength = 0
        StderrLength = 0
    }
    $heartbeatSeconds = 10

    function Write-NativeSetupLine {
        param([string]$Line)

        if ([string]::IsNullOrWhiteSpace($Line)) {
            return
        }

        $line = ([string]$Line) -replace "`e\[[0-9;?]*[ -/]*[@-~]", ""
        $line = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($line)) {
            return
        }

        # winget and ollama commonly report progress as NN% or NN.N%.
        if ($line -match '(?<!\d)(100|[1-9]?\d)(?:\.\d+)?%') {
            $nativePercent = [int][double]$Matches[1]
            $mappedPercent = $StartPercent + [int](($EndPercent - $StartPercent) * ($nativePercent / 100.0))
            $progressState.LastPercent = [math]::Max($progressState.LastPercent, $mappedPercent)
            $progressState.LastProgressAt = Get-Date
            Write-SetupProgress -Activity $Activity -Percent $mappedPercent -Status $line.Trim()
            if (
                $nativePercent -eq 100 -or
                $progressState.LastConsolePercent -lt 0 -or
                $nativePercent -ge ($progressState.LastConsolePercent + 5)
            ) {
                Write-Host $line
                $progressState.LastConsolePercent = $nativePercent
                $progressState.LastConsoleStatus = $line
            }
            return
        }

        # Ollama redraws spinner/progress rows with carriage returns. Once its
        # output is redirected, those frames can appear as many repeated lines.
        if ($line -match '^pulling manifest\b') {
            if ($progressState.LastConsoleStatus -ne "pulling manifest") {
                Write-Host "pulling manifest..."
                $progressState.LastConsoleStatus = "pulling manifest"
            }
            return
        }

        if ($line -ne $progressState.LastConsoleStatus) {
            Write-Host $line
            $progressState.LastConsoleStatus = $line
        }
    }

    function Read-NativeSetupFile {
        param(
            [string]$Path,
            [string]$LengthKey
        )

        if (-not (Test-Path -LiteralPath $Path)) {
            return
        }

        $file = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
        if (-not $file -or $file.Length -le $progressState[$LengthKey]) {
            return
        }

        $reader = $null
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try {
            $stream.Seek([int64]$progressState[$LengthKey], [System.IO.SeekOrigin]::Begin) | Out-Null
            $reader = [System.IO.StreamReader]::new($stream)
            $text = $reader.ReadToEnd()
            $progressState[$LengthKey] = $stream.Position
        } finally {
            if ($reader) {
                $reader.Dispose()
            } else {
                $stream.Dispose()
            }
        }

        foreach ($line in ($text -split "`r`n|`n|`r")) {
            Write-NativeSetupLine -Line $line
        }
    }

    $process = Start-Process -FilePath $FilePath `
        -ArgumentList (Join-ProcessArguments $Arguments) `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru `
        -WindowStyle Hidden
    # Touch the handle immediately; without this, PowerShell can leave ExitCode
    # unset for some redirected native processes even after WaitForExit().
    $null = $process.Handle

    try {
        while (-not $process.HasExited) {
            Read-NativeSetupFile -Path $stdoutPath -LengthKey "StdoutLength"
            Read-NativeSetupFile -Path $stderrPath -LengthKey "StderrLength"

            if (((Get-Date) - $progressState.LastProgressAt).TotalSeconds -ge $heartbeatSeconds -and $progressState.LastPercent -lt ($EndPercent - 1)) {
                $progressState.LastPercent += 1
                $progressState.LastProgressAt = Get-Date
                Write-SetupProgress -Activity $Activity -Percent $progressState.LastPercent -Status "Still running..."
            }

            Start-Sleep -Milliseconds 500
        }

        $process.WaitForExit()
        $process.Refresh()
        Read-NativeSetupFile -Path $stdoutPath -LengthKey "StdoutLength"
        Read-NativeSetupFile -Path $stderrPath -LengthKey "StderrLength"
    } finally {
        Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    }

    $exitCode = $process.ExitCode
    if ($null -eq $exitCode) {
        if ($progressState.LastPercent -ge $EndPercent) {
            Write-SetupLog "$Activity completed its native progress output but did not report an exit code; treating it as success."
            $exitCode = 0
        } else {
            Write-SetupLog "$Activity did not report an exit code and only reached $($progressState.LastPercent)% progress."
            $exitCode = -1
        }
    }

    if ($exitCode -eq 0 -or $exitCode -eq 3010) {
        Write-SetupProgress -Activity $Activity -Percent $EndPercent -Status "Completed"
    } else {
        Write-SetupProgress -Activity $Activity -Percent $StartPercent -Status "Failed (exit code $exitCode)"
    }
    Write-Progress -Activity $Activity -Completed
    Write-SetupLog "$Activity exited with code $exitCode"
    return $exitCode
}

function Quote-ProcessArgument {
    param([string]$Value)

    if ($null -eq $Value) {
        return '""'
    }

    return '"' + ($Value -replace '"', '\"') + '"'
}

function Join-ProcessArguments {
    param([string[]]$Arguments)

    return ($Arguments | ForEach-Object { Quote-ProcessArgument $_ }) -join " "
}

function Normalize-OllamaModelName {
    param([string]$Model)

    if ($null -eq $Model) {
        return ""
    }

    return ([string]$Model -replace '^(?:\uFEFF|\u00EF\u00BB\u00BF)+', '').Trim()
}

function Convert-DisplayRegistryValueToUInt64 {
    param([object]$Value)

    if ($null -eq $Value) {
        return [uint64]0
    }
    if ($Value -is [byte[]]) {
        if ($Value.Length -ge 8) {
            return [BitConverter]::ToUInt64($Value, 0)
        }
        if ($Value.Length -ge 4) {
            return [uint64][BitConverter]::ToUInt32($Value, 0)
        }
        return [uint64]0
    }
    try {
        return [uint64]$Value
    } catch {
        return [uint64]0
    }
}

function Convert-DisplayRegistryString {
    param([object]$Value)

    if ($null -eq $Value) {
        return ""
    }
    if ($Value -is [byte[]]) {
        return ([Text.Encoding]::Unicode.GetString($Value)).Trim([char]0)
    }
    return [string]$Value
}

function Get-VideoControllerDedicatedVramGb {
    param([object]$Gpu)

    if ($null -eq $Gpu) {
        return 0
    }

    $adapterRamBytes = Convert-DisplayRegistryValueToUInt64 -Value $Gpu.AdapterRAM
    $registryBytes = [uint64]0
    $gpuName = [string]$Gpu.Name
    $displayClassPath = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"

    if (Test-Path -LiteralPath $displayClassPath) {
        foreach ($key in Get-ChildItem -LiteralPath $displayClassPath -ErrorAction SilentlyContinue) {
            $props = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
            if ($null -eq $props) {
                continue
            }
            $driverDesc = [string]$props.DriverDesc
            $adapterString = Convert-DisplayRegistryString -Value $props.'HardwareInformation.AdapterString'
            if ($driverDesc -ne $gpuName -and $adapterString -ne $gpuName) {
                continue
            }

            $candidateBytes = Convert-DisplayRegistryValueToUInt64 -Value $props.'HardwareInformation.qwMemorySize'
            if ($candidateBytes -eq 0) {
                $candidateBytes = Convert-DisplayRegistryValueToUInt64 -Value $props.'HardwareInformation.MemorySize'
            }
            if ($candidateBytes -gt $registryBytes) {
                $registryBytes = $candidateBytes
            }
        }
    }

    $dedicatedBytes = if ($registryBytes -gt $adapterRamBytes) { $registryBytes } else { $adapterRamBytes }
    if ($dedicatedBytes -eq 0) {
        return 0
    }
    return [math]::Round($dedicatedBytes / 1GB, 1)
}

function Assert-Identifier {
    param([string]$Name, [string]$Value)
    if ($Value -notmatch '^[A-Za-z0-9_]+$') {
        throw "$Name may only contain letters, numbers, and underscores."
    }
}

function Quote-SqlString {
    param([string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function ConvertTo-DatabaseUrlComponent {
    param([string]$Value)
    return [System.Uri]::EscapeDataString([string]$Value)
}

function Format-EnvAssignment {
    param([string]$Key, [string]$Value)

    $escaped = [string]$Value
    $escaped = $escaped.Replace("\", "\\")
    $escaped = $escaped.Replace('"', '\"')
    $escaped = $escaped.Replace("`r", "\r")
    $escaped = $escaped.Replace("`n", "\n")
    return "$Key=""$escaped"""
}

function Ensure-RuntimeEnvFile {
    if (-not (Test-Path -LiteralPath $envPath)) {
        if (Test-Path -LiteralPath $runtimeTemplate) {
            Copy-Item -LiteralPath $runtimeTemplate -Destination $envPath -Force
        } else {
            New-Item -ItemType File -Path $envPath -Force | Out-Null
        }
    }
}

function Get-EnvAssignmentValue {
    param(
        [string[]]$Lines,
        [string]$Key
    )

    foreach ($line in $Lines) {
        if ($line -match ('^\s*' + [regex]::Escape($Key) + '\s*=\s*(.*)\s*$')) {
            $value = [string]$Matches[1]
            $value = $value.Trim()
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            return $value.Trim()
        }
    }
    return ""
}

function Get-OrCreate-SecretKey {
    Ensure-RuntimeEnvFile
    $lines = Get-Content -LiteralPath $envPath
    $existing = Get-EnvAssignmentValue -Lines $lines -Key "SECRET_KEY"
    if (-not [string]::IsNullOrWhiteSpace($existing) -and $existing.Length -ge 64) {
        Write-SetupLog "Preserving existing SECRET_KEY"
        return $existing
    }

    $bytes = New-Object byte[] 64
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    $secret = -join ($bytes | ForEach-Object { $_.ToString("x2") })
    Write-SetupLog "Generated a unique SECRET_KEY for this installation"
    return $secret
}

function Get-OrCreate-SyncDeviceId {
    Ensure-RuntimeEnvFile
    $lines = Get-Content -LiteralPath $envPath
    $existing = Get-EnvAssignmentValue -Lines $lines -Key "SYNC_DEVICE_ID"
    if (
        -not [string]::IsNullOrWhiteSpace($existing) -and
        $existing -ne "localhost-8000-001" -and
        $existing -ne "client-specific-stable-uuid"
    ) {
        Write-SetupLog "Preserving existing SYNC_DEVICE_ID"
        return $existing
    }
    $deviceId = [guid]::NewGuid().ToString()
    Write-SetupLog "Generated SYNC_DEVICE_ID for this installation: $deviceId"
    return $deviceId
}

function Find-CommandPath {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    return $null
}

function Find-BundledDependencyInstaller {
    param([string]$DependencyName)

    if ([string]::IsNullOrWhiteSpace($InstallDir)) {
        return $null
    }

    $dependencyDir = Join-Path $InstallDir "installers\$DependencyName"
    if (-not (Test-Path -LiteralPath $dependencyDir)) {
        return $null
    }

    $installer = Get-ChildItem -LiteralPath $dependencyDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in @(".msi", ".exe") } |
        Sort-Object Name |
        Select-Object -First 1
    if ($installer) {
        return $installer.FullName
    }
    return $null
}

function Find-MySqlExe {
    $commandPath = Find-CommandPath "mysql.exe"
    if ($commandPath) {
        return $commandPath
    }

    $candidates = @(
        "$env:ProgramFiles\MySQL\MySQL Server 8.4\bin\mysql.exe",
        "$env:ProgramFiles\MySQL\MySQL Server 8.3\bin\mysql.exe",
        "$env:ProgramFiles\MySQL\MySQL Server 8.2\bin\mysql.exe",
        "$env:ProgramFiles\MySQL\MySQL Server 8.1\bin\mysql.exe",
        "$env:ProgramFiles\MySQL\MySQL Server 8.0\bin\mysql.exe",
        "${env:ProgramFiles(x86)}\MySQL\MySQL Server 8.0\bin\mysql.exe",
        "$env:ProgramFiles\MariaDB 11.4\bin\mysql.exe",
        "$env:ProgramFiles\MariaDB 11.3\bin\mysql.exe",
        "$env:ProgramFiles\MariaDB 11.2\bin\mysql.exe",
        "$env:ProgramFiles\MariaDB 11.1\bin\mysql.exe",
        "$env:ProgramFiles\MariaDB 11.0\bin\mysql.exe",
        "$env:ProgramFiles\MariaDB 10.11\bin\mysql.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function Find-MySqlDExe {
    $commandPath = Find-CommandPath "mysqld.exe"
    if ($commandPath) {
        return $commandPath
    }

    $candidates = @(
        "$env:ProgramFiles\MySQL\MySQL Server 8.4\bin\mysqld.exe",
        "$env:ProgramFiles\MySQL\MySQL Server 8.3\bin\mysqld.exe",
        "$env:ProgramFiles\MySQL\MySQL Server 8.2\bin\mysqld.exe",
        "$env:ProgramFiles\MySQL\MySQL Server 8.1\bin\mysqld.exe",
        "$env:ProgramFiles\MySQL\MySQL Server 8.0\bin\mysqld.exe",
        "${env:ProgramFiles(x86)}\MySQL\MySQL Server 8.0\bin\mysqld.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function Test-TcpPort {
    param([string]$HostName, [int]$Port)
    try {
        $client = [Net.Sockets.TcpClient]::new()
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(2000)) {
            $client.Close()
            return $false
        }
        $client.EndConnect($async)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

function Get-MySqlServiceDetails {
    return @(Get-CimInstance Win32_Service -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(MySQL|MySQL80|MySQL84)$' -or $_.DisplayName -match 'MySQL' })
}

function Find-MySqlDefaultsFile {
    $candidates = @(
        "$env:ProgramData\MySQL\MySQL Server 8.4\my.ini",
        "$env:ProgramData\MySQL\MySQL Server 8.3\my.ini",
        "$env:ProgramData\MySQL\MySQL Server 8.2\my.ini",
        "$env:ProgramData\MySQL\MySQL Server 8.1\my.ini",
        "$env:ProgramData\MySQL\MySQL Server 8.0\my.ini",
        "$env:ProgramFiles\MySQL\MySQL Server 8.4\my.ini",
        "$env:ProgramFiles\MySQL\MySQL Server 8.3\my.ini",
        "$env:ProgramFiles\MySQL\MySQL Server 8.2\my.ini",
        "$env:ProgramFiles\MySQL\MySQL Server 8.1\my.ini",
        "$env:ProgramFiles\MySQL\MySQL Server 8.0\my.ini"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function New-MySqlDefaultsFile {
    param([string]$MySqlDExe)

    $existing = Find-MySqlDefaultsFile
    if ($existing) {
        return $existing
    }

    $serverRoot = Split-Path -Parent (Split-Path -Parent $MySqlDExe)
    $programDataRoot = Join-Path $env:ProgramData "MySQL"
    $serverDirName = Split-Path -Leaf $serverRoot
    $configDir = Join-Path $programDataRoot $serverDirName
    if ([string]::IsNullOrWhiteSpace($serverDirName) -or $serverDirName -notmatch '^MySQL Server ') {
        $configDir = Join-Path $programDataRoot "MySQL Server 8.0"
    }

    $dataDir = Join-Path $configDir "Data"
    $errorLog = Join-Path $dataDir "error.log"
    $defaultsFile = Join-Path $configDir "my.ini"
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

    $basedir = ($serverRoot -replace '\\', '/')
    $datadir = ($dataDir -replace '\\', '/')
    $content = @"
[client]
port=3306

[mysql]
no-beep

[mysqld]
port=3306
basedir="$basedir"
datadir="$datadir"
default-storage-engine=INNODB
sql-mode="NO_ENGINE_SUBSTITUTION"
log-output=FILE
log-error="$($errorLog -replace '\\', '/')"
"@
    Set-Content -LiteralPath $defaultsFile -Value $content -Encoding ASCII
    Write-SetupLog "Created MySQL defaults file: $defaultsFile"
    return $defaultsFile
}

function Get-MySqlServerConfigValue {
    param(
        [string]$DefaultsFile,
        [string]$Name
    )

    if (-not (Test-Path -LiteralPath $DefaultsFile)) {
        return $null
    }

    $inServerSection = $false
    $escapedName = [regex]::Escape($Name)
    foreach ($line in Get-Content -LiteralPath $DefaultsFile) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^\[(.+)\]$') {
            $inServerSection = ($Matches[1] -ieq "mysqld")
            continue
        }
        if (-not $inServerSection) {
            continue
        }
        if ($trimmed -match "^$escapedName\s*=\s*(.+)$") {
            return $Matches[1].Trim().Trim('"')
        }
    }
    return $null
}

function Convert-MySqlConfigPath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $null
    }
    return ($Path.Trim().Trim('"') -replace '/', '\')
}

function Get-MySqlDataDirFromDefaultsFile {
    param([string]$DefaultsFile)

    return Convert-MySqlConfigPath (Get-MySqlServerConfigValue -DefaultsFile $DefaultsFile -Name "datadir")
}

function Test-MySqlDataDirectoryInitialized {
    param([string]$DefaultsFile)

    $dataDir = Get-MySqlDataDirFromDefaultsFile -DefaultsFile $DefaultsFile
    if ([string]::IsNullOrWhiteSpace($dataDir)) {
        return $false
    }
    return (Test-Path -LiteralPath (Join-Path $dataDir "mysql"))
}

function Initialize-MySqlDataDirectory {
    param([string]$MySqlDExe, [string]$DefaultsFile)

    if (Test-MySqlDataDirectoryInitialized -DefaultsFile $DefaultsFile) {
        return $false
    }

    Write-SetupLog "Initializing MySQL data directory"
    $stdoutPath = Join-Path $env:TEMP ("drtransition-mysql-init-out-" + [guid]::NewGuid() + ".log")
    $stderrPath = Join-Path $env:TEMP ("drtransition-mysql-init-err-" + [guid]::NewGuid() + ".log")
    try {
        $process = Start-Process -FilePath $MySqlDExe -ArgumentList (Join-ProcessArguments @(
            "--defaults-file=$DefaultsFile",
            "--initialize",
            "--console"
        )) -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -Wait -PassThru -WindowStyle Hidden

        $stdout = ""
        $stderr = ""
        if (Test-Path -LiteralPath $stdoutPath) {
            $stdout = Get-Content -LiteralPath $stdoutPath -Raw
        }
        if (Test-Path -LiteralPath $stderrPath) {
            $stderr = Get-Content -LiteralPath $stderrPath -Raw
        }
        $script:MySqlInitializationOutput = @($stdout, $stderr) -join "`n"

        if ($process.ExitCode -ne 0) {
            throw "MySQL data directory initialization failed with exit code $($process.ExitCode)."
        }
    } finally {
        Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    }
    return $true
}

function Find-MySqlTemporaryRootPasswordInText {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $null
    }
    $matches = [regex]::Matches($Text, 'temporary password.*root@localhost:\s*(.+)', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if ($matches.Count -eq 0) {
        return $null
    }
    return $matches[$matches.Count - 1].Groups[1].Value.Trim()
}

function Get-MySqlTemporaryRootPassword {
    param([string]$DefaultsFile)

    $dataDir = Get-MySqlDataDirFromDefaultsFile -DefaultsFile $DefaultsFile
    if ([string]::IsNullOrWhiteSpace($dataDir)) {
        throw "Could not determine MySQL data directory for temporary password lookup."
    }

    $password = Find-MySqlTemporaryRootPasswordInText -Text $script:MySqlInitializationOutput
    if ($password) {
        return $password
    }

    $logCandidates = [System.Collections.Generic.List[string]]::new()
    @(
        (Join-Path $dataDir "error.log"),
        (Join-Path $dataDir "$env:COMPUTERNAME.err")
    ) | ForEach-Object { $logCandidates.Add($_) }

    $configuredLog = Convert-MySqlConfigPath (Get-MySqlServerConfigValue -DefaultsFile $DefaultsFile -Name "log-error")
    if (-not [string]::IsNullOrWhiteSpace($configuredLog)) {
        if ([System.IO.Path]::IsPathRooted($configuredLog)) {
            $logCandidates.Add($configuredLog)
        } else {
            $logCandidates.Add((Join-Path $dataDir $configuredLog))
        }
    }

    Get-ChildItem -LiteralPath $dataDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in @(".err", ".log") } |
        Sort-Object LastWriteTime |
        ForEach-Object { $logCandidates.Add($_.FullName) }

    $checkedLogs = [System.Collections.Generic.List[string]]::new()
    foreach ($log in $logCandidates) {
        if ([string]::IsNullOrWhiteSpace($log) -or $checkedLogs.Contains($log)) {
            continue
        }
        $checkedLogs.Add($log)
        if (-not (Test-Path -LiteralPath $log)) {
            continue
        }
        $password = Find-MySqlTemporaryRootPasswordInText -Text (Get-Content -LiteralPath $log -Raw)
        if ($password) {
            return $password
        }
    }

    Write-SetupLog "Checked MySQL initialization output and $($checkedLogs.Count) possible MySQL log file path(s) for the temporary root password."
    throw "Could not find MySQL temporary root password in the data directory logs."
}

function Repair-MySqlServiceDefaultsFile {
    param([object]$Service)

    $pathName = [string]$Service.PathName
    if ([string]::IsNullOrWhiteSpace($pathName)) {
        return
    }
    if ($pathName -notmatch 'mysqld\.exe') {
        return
    }
    if ($pathName -notmatch "--defaults-file=(?:''|`"`"|'`"|`"')" -and $pathName -notmatch '--defaults-file=\s*$') {
        return
    }

    $defaultsFile = Find-MySqlDefaultsFile
    if (-not $defaultsFile) {
        Write-SetupLog "MySQL service $($Service.Name) has an empty defaults-file, but no my.ini was found to repair it"
        return
    }

    if ($pathName -match '^"([^"]*mysqld\.exe)"') {
        $mysqld = $Matches[1]
    } elseif ($pathName -match '^([^"]*mysqld\.exe)') {
        $mysqld = $Matches[1]
    } else {
        Write-SetupLog "Could not parse mysqld.exe path for service $($Service.Name)"
        return
    }

    Write-SetupLog "Repairing MySQL service $($Service.Name) defaults-file path"
    Stop-Service -Name $Service.Name -ErrorAction SilentlyContinue
    $binPath = '"{0}" --defaults-file="{1}" {2}' -f $mysqld, $defaultsFile, $Service.Name
    $process = Start-Process -FilePath "sc.exe" -ArgumentList @("config", $Service.Name, "binPath=", $binPath) -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "Could not repair MySQL service $($Service.Name) defaults-file path; sc.exe exited with code $($process.ExitCode)."
    }
}

function Install-MySqlService {
    param([string]$MySqlDExe, [string]$DefaultsFile)

    $services = Get-MySqlServiceDetails
    if ($services.Count -gt 0) {
        return
    }

    Write-SetupLog "Installing MySQL Windows service"
    $process = Start-Process -FilePath $MySqlDExe -ArgumentList (Join-ProcessArguments @(
        "--install",
        "MySQL",
        "--defaults-file=$DefaultsFile"
    )) -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "MySQL service installation failed with exit code $($process.ExitCode)."
    }
}


function Invoke-DownloadWithProgress {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [Parameter(Mandatory = $true)][string]$Activity,
        [int]$StartPercent = 0,
        [int]$EndPercent = 80
    )

    Write-SetupLog "Downloading: $Url"
    Write-SetupProgress -Activity $Activity -Percent $StartPercent -Status "Connecting..."

    $request = [System.Net.HttpWebRequest]::Create($Url)
    $request.AllowAutoRedirect = $true
    $request.UserAgent = "DrTransition-Installer/1.0"
    $response = $null
    $stream = $null
    $fileStream = $null

    try {
        $response = $request.GetResponse()
        $totalBytes = [int64]$response.ContentLength
        $stream = $response.GetResponseStream()
        $fileStream = [System.IO.FileStream]::new($OutFile, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $buffer = [byte[]]::new(65536)
        [int64]$totalRead = 0
        $lastUpdate = [DateTime]::MinValue
        $barWidth = 40

        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $fileStream.Write($buffer, 0, $read)
            $totalRead += $read

            $now = [DateTime]::UtcNow
            if (($now - $lastUpdate).TotalMilliseconds -ge 200) {
                if ($totalBytes -gt 0) {
                    $downloadPct = [math]::Min(100.0, ($totalRead / $totalBytes) * 100.0)
                    $filled = [math]::Floor($barWidth * $downloadPct / 100.0)
                    $empty = $barWidth - $filled
                    $bar = ('#' * $filled) + (' ' * $empty)
                    $downloadedMb = [math]::Round($totalRead / 1MB, 1)
                    $totalMb = [math]::Round($totalBytes / 1MB, 1)
                    $status = "{0:N1} MB / {1:N1} MB ({2:N1}%)" -f $downloadedMb, $totalMb, $downloadPct
                    Write-Host -NoNewline ("`r[{0}] {1}" -f $bar, $status)

                    $mapped = $StartPercent + [int](($EndPercent - $StartPercent) * ($downloadPct / 100.0))
                    Write-SetupProgress -Activity $Activity -Percent $mapped -Status $status
                } else {
                    $downloadedMb = [math]::Round($totalRead / 1MB, 1)
                    $status = "$downloadedMb MB downloaded..."
                    Write-Host -NoNewline "`r$status"
                    Write-SetupProgress -Activity $Activity -Percent $StartPercent -Status $status
                }
                $lastUpdate = $now
            }
        }

        Write-Host ""
        if ($totalBytes -gt 0) {
            Write-Host ("[{0}] 100.0%" -f ('#' * $barWidth))
        }
        Write-SetupProgress -Activity $Activity -Percent $EndPercent -Status "Download completed"
    }
    finally {
        if ($null -ne $fileStream) { $fileStream.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $response) { $response.Dispose() }
    }
}

function Test-OllamaInstallerSignature {
    param([Parameter(Mandatory = $true)][string]$FilePath)

    Write-SetupProgress -Activity "Installing Ollama" -Percent 82 -Status "Verifying digital signature..."
    $sig = Get-AuthenticodeSignature -FilePath $FilePath
    if ($sig.Status -ne "Valid") {
        throw "Ollama installer signature verification failed: $($sig.Status)"
    }

    $subject = [string]$sig.SignerCertificate.Subject
    if ($subject -notmatch '(^|, )O=Ollama Inc\.(,|$)') {
        throw "Ollama installer has an unexpected signer: $subject"
    }

    Write-SetupLog "Ollama installer signature verified: $subject"
}

function Install-OllamaFromInstaller {
    param(
        [Parameter(Mandatory = $true)][string]$InstallerPath,
        [switch]$Interactive,
        [switch]$RemoveInstallerWhenDone
    )

    $activity = "Installing Ollama"

    try {
        Write-SetupLog "Installing Ollama from installer: $InstallerPath"
        Test-OllamaInstallerSignature -FilePath $InstallerPath

        # This marker is used by Ollama during upgrade/install so the app can start quietly.
        $markerDir = Join-Path $env:LOCALAPPDATA "Ollama"
        $markerFile = Join-Path $markerDir "upgraded"
        New-Item -ItemType Directory -Force -Path $markerDir | Out-Null
        New-Item -ItemType File -Force -Path $markerFile | Out-Null

        if ($Interactive) {
            Write-SetupProgress -Activity $activity -Percent 85 -Status "Complete the Ollama installer window..."
            Write-SetupLog "Starting interactive Ollama installer"
            $process = Start-Process -FilePath $InstallerPath -PassThru
        } else {
            Write-SetupProgress -Activity $activity -Percent 85 -Status "Installing Ollama..."
            Write-SetupLog "Starting silent Ollama installer"
            $process = Start-Process -FilePath $InstallerPath `
                -ArgumentList "/VERYSILENT /NORESTART /SUPPRESSMSGBOXES" `
                -PassThru `
                -WindowStyle Hidden
        }

        # The Ollama/Inno installer does not expose a trustworthy numeric install percentage.
        # Keep the user informed with stage-based progress while the installer is running.
        $installStage = 85
        while (-not $process.HasExited) {
            Start-Sleep -Seconds 1
            $process.Refresh()
            if (-not $process.HasExited -and $installStage -lt 98) {
                $installStage++
                if ($Interactive) {
                    Write-SetupProgress -Activity $activity -Percent $installStage -Status "Waiting for Ollama installer..."
                } else {
                    Write-SetupProgress -Activity $activity -Percent $installStage -Status "Installing Ollama..."
                }
            }
        }
        $process.WaitForExit()
        $process.Refresh()

        if ($process.ExitCode -ne 0 -and $process.ExitCode -ne 3010) {
            throw "Ollama installation failed with exit code $($process.ExitCode)."
        }

        Write-SetupProgress -Activity $activity -Percent 100 -Status "Ollama installation completed"
        Write-Progress -Activity $activity -Completed
        Write-SetupLog "Ollama installed successfully (exit code $($process.ExitCode))"
    }
    finally {
        if ($RemoveInstallerWhenDone) {
            Remove-Item -LiteralPath $InstallerPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Install-OllamaDirect {
    $activity = "Installing Ollama"
    $downloadUrl = "https://ollama.com/download/OllamaSetup.exe"
    $tempInstaller = Join-Path $env:TEMP "DrTransition-OllamaSetup.exe"

    Write-SetupLog "Installing Ollama from official download"
    Invoke-DownloadWithProgress -Url $downloadUrl -OutFile $tempInstaller -Activity $activity -StartPercent 0 -EndPercent 80
    Install-OllamaFromInstaller -InstallerPath $tempInstaller -RemoveInstallerWhenDone
}

function Invoke-WingetInstall {
    param([string[]]$PackageIds)

    $winget = Find-CommandPath "winget.exe"
    if (-not $winget) {
        throw "winget.exe was not found. Install App Installer from Microsoft Store, or install the dependency manually."
    }

    foreach ($packageId in $PackageIds) {
        $activity = "Installing $packageId"
        Write-SetupLog "Trying winget install $packageId"
        Write-SetupProgress -Activity $activity -Percent 0 -Status "Locating package..."

        # IMPORTANT: Do not run winget through redirected stdout/stderr here. Winget can
        # lose its process exit code in that mode during installer handoff. Start-Process
        # keeps winget isolated from PowerShell's success stream so its output cannot
        # become part of Ensure-MySql's returned mysql.exe path.
        Write-SetupProgress -Activity $activity -Percent 5 -Status "Downloading package..."
        Write-Host ""
        Write-Host "========== winget progress: $packageId ==========" -ForegroundColor Cyan

        $wingetArgs = @(
            "install",
            "--id",
            $packageId,
            "--exact",
            "--source",
            "winget",
            "--silent",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--disable-interactivity"
        )

        Write-SetupLog "Starting $activity"
        $process = Start-Process -FilePath $winget `
            -ArgumentList (Join-ProcessArguments $wingetArgs) `
            -NoNewWindow `
            -PassThru

        $installPercent = 5
        while (-not $process.HasExited) {
            Start-Sleep -Seconds 10
            if (-not $process.HasExited -and $installPercent -lt 95) {
                $installPercent += 5
                Write-SetupProgress -Activity $activity -Percent $installPercent -Status "Still running..."
            }
        }
        $process.WaitForExit()
        $process.Refresh()
        $exitCode = $process.ExitCode
        if ($null -eq $exitCode) {
            $exitCode = -1
        }

        Write-Host "========== end winget progress: $packageId ==========" -ForegroundColor Cyan
        Write-Host ""

        if ($exitCode -eq 0 -or $exitCode -eq 3010) {
            # Winget does not expose a reliable numeric MSI/EXE installation percentage
            # to callers. Its native console UI is therefore the source of truth while
            # downloading/installing; mark the structured installer progress complete
            # only after winget returns successfully.
            Write-SetupProgress -Activity $activity -Percent 100 -Status "Download and installation completed"
            Write-Progress -Activity $activity -Completed
            Write-SetupLog "winget installed $packageId (exit code $exitCode)"
            return
        }

        Write-SetupProgress -Activity $activity -Percent 0 -Status "Failed (exit code $exitCode)"
        Write-Progress -Activity $activity -Completed
        Write-SetupLog "winget failed for $packageId with exit code $exitCode"
    }

    throw "Could not install any package from: $($PackageIds -join ', ')"
}

function Install-MySqlBundled {
    param([Parameter(Mandatory = $true)][string]$InstallerPath)

    $activity = "Installing MySQL"
    Write-SetupLog "Installing MySQL from bundled installer: $InstallerPath"
    Write-SetupProgress -Activity $activity -Percent 5 -Status "Installing MySQL in the background..."

    if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
        throw "Bundled MySQL installer was not found: $InstallerPath"
    }

    $installerItem = Get-Item -LiteralPath $InstallerPath
    if ($installerItem.Length -le 0) {
        throw "Bundled MySQL installer is empty: $InstallerPath"
    }

    $extension = [System.IO.Path]::GetExtension($InstallerPath).ToLowerInvariant()
    if ($extension -eq ".msi") {
        $msiLogPath = Join-Path $logDir "mysql-bundled-msiexec.log"
        $msiArguments = @(
            "/i",
            (Quote-ProcessArgument $InstallerPath),
            "/quiet",
            "/norestart",
            "/l*v",
            (Quote-ProcessArgument $msiLogPath)
        ) -join " "
        Write-SetupLog "Starting msiexec.exe $msiArguments"
        $process = Start-Process -FilePath "msiexec.exe" -ArgumentList $msiArguments -PassThru -WindowStyle Hidden
    } elseif ($extension -eq ".exe") {
        $exeArguments = "/quiet /norestart"
        Write-SetupLog "Starting bundled MySQL executable installer silently: $InstallerPath $exeArguments"
        $process = Start-Process -FilePath $InstallerPath -ArgumentList $exeArguments -PassThru -WindowStyle Hidden
    } else {
        throw "Unsupported bundled MySQL installer type: $InstallerPath"
    }

    $installPercent = 5
    while (-not $process.HasExited) {
        Start-Sleep -Seconds 10
        if (-not $process.HasExited -and $installPercent -lt 95) {
            $installPercent += 5
            Write-SetupProgress -Activity $activity -Percent $installPercent -Status "Installing MySQL in the background..."
        }
    }
    $process.WaitForExit()
    $process.Refresh()

    if ($process.ExitCode -ne 0 -and $process.ExitCode -ne 3010) {
        Write-SetupProgress -Activity $activity -Percent 5 -Status "Failed (exit code $($process.ExitCode))"
        throw "Bundled MySQL installer failed with exit code $($process.ExitCode)."
    }

    Write-SetupProgress -Activity $activity -Percent 100 -Status "Bundled MySQL installer completed"
    Write-Progress -Activity $activity -Completed
    Write-SetupLog "Bundled MySQL installer completed successfully (exit code $($process.ExitCode))"
}

function Start-MySqlService {
    $serviceDetails = Get-MySqlServiceDetails
    foreach ($service in $serviceDetails) {
        Repair-MySqlServiceDefaultsFile -Service $service
    }

    $services = @(Get-Service -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(MySQL|MySQL80|MySQL84)$' -or $_.DisplayName -match 'MySQL' })
    foreach ($service in $services) {
        $service.Refresh()
        if ($service.Status -ne "Running") {
            Write-SetupLog "Starting service $($service.Name)"
            try {
                Start-Service -Name $service.Name -ErrorAction Stop
            } catch {
                Write-SetupLog "Could not start service $($service.Name): $($_.Exception.Message)"
            }
        }
    }
}

function Wait-MySqlReachable {
    $mysqlReachable = $false
    for ($attempt = 1; $attempt -le 45; $attempt++) {
        if (Test-TcpPort -HostName "127.0.0.1" -Port 3306) {
            $mysqlReachable = $true
            break
        }
        if ($attempt -eq 5 -or $attempt -eq 15 -or $attempt -eq 30) {
            Start-MySqlService
        }
        Start-Sleep -Seconds 2
    }
    return $mysqlReachable
}

function Invoke-MySqlSqlWithPassword {
    param(
        [string]$MySqlExe,
        [string]$Sql,
        [string]$User,
        [string]$Password,
        [switch]$ConnectExpiredPassword
    )

    $tempSql = Join-Path $env:TEMP ("drtransition-mysql-bootstrap-" + [guid]::NewGuid() + ".sql")
    Set-Content -LiteralPath $tempSql -Value $Sql -Encoding UTF8
    $previous = $env:MYSQL_PWD
    try {
        if ([string]::IsNullOrEmpty($Password)) {
            Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue
        } else {
            $env:MYSQL_PWD = $Password
        }
        $mysqlArgs = @(
            "--protocol=tcp",
            "-h", "127.0.0.1",
            "-P", "3306",
            "-u", $User,
            "--default-character-set=utf8mb4"
        )
        if ($ConnectExpiredPassword) {
            $mysqlArgs += "--connect-expired-password"
        }
        $mysqlArgs += @("-e", "source $tempSql")
        $mysqlOutput = & $MySqlExe @mysqlArgs 2>&1
        foreach ($line in $mysqlOutput) {
            Write-Host $line
        }
        if ($LASTEXITCODE -ne 0) {
            throw "mysql.exe exited with code $LASTEXITCODE"
        }
    } finally {
        if ($null -eq $previous) {
            Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue
        } else {
            $env:MYSQL_PWD = $previous
        }
        Remove-Item -LiteralPath $tempSql -Force -ErrorAction SilentlyContinue
    }
}

function Test-MySqlAdminPassword {
    param([string]$MySqlExe)

    try {
        Invoke-MySqlSqlWithPassword -MySqlExe $MySqlExe -Sql "SELECT 1;" -User $MySqlAdminUser -Password $MySqlAdminPassword
        return $true
    } catch {
        return $false
    }
}

function Set-FreshMySqlRootPassword {
    param([string]$MySqlExe, [string]$DefaultsFile)

    if ([string]::IsNullOrWhiteSpace($MySqlAdminPassword)) {
        throw "MySQL administrator password is required to finish fresh MySQL setup."
    }

    Write-SetupLog "Setting MySQL root password from temporary initialization password"
    $temporaryPassword = Get-MySqlTemporaryRootPassword -DefaultsFile $DefaultsFile
    $quotedPassword = Quote-SqlString $MySqlAdminPassword
    $sql = @"
ALTER USER IF EXISTS 'root'@'localhost' IDENTIFIED BY $quotedPassword;
CREATE USER IF NOT EXISTS 'root'@'127.0.0.1' IDENTIFIED BY $quotedPassword;
ALTER USER 'root'@'127.0.0.1' IDENTIFIED BY $quotedPassword;
GRANT ALL PRIVILEGES ON *.* TO 'root'@'127.0.0.1' WITH GRANT OPTION;
CREATE USER IF NOT EXISTS '$MySqlAdminUser'@'127.0.0.1' IDENTIFIED BY $quotedPassword;
ALTER USER '$MySqlAdminUser'@'127.0.0.1' IDENTIFIED BY $quotedPassword;
GRANT ALL PRIVILEGES ON *.* TO '$MySqlAdminUser'@'127.0.0.1' WITH GRANT OPTION;
FLUSH PRIVILEGES;
"@
    Invoke-MySqlSqlWithPassword -MySqlExe $MySqlExe -Sql $sql -User "root" -Password $temporaryPassword -ConnectExpiredPassword
}

function Repair-MySqlRootPasswordFromTemporaryIfNeeded {
    param([string]$MySqlExe, [string]$DefaultsFile)

    if ([string]::IsNullOrWhiteSpace($MySqlAdminPassword)) {
        return
    }
    if (Test-MySqlAdminPassword -MySqlExe $MySqlExe) {
        return
    }

    Write-SetupLog "MySQL administrator password was not accepted; checking for a temporary initialization password"
    try {
        Set-FreshMySqlRootPassword -MySqlExe $MySqlExe -DefaultsFile $DefaultsFile
    } catch {
        throw "MySQL administrator password was not accepted, and setup could not repair it with the temporary initialization password. $($_.Exception.Message)"
    }
}

function Ensure-MySql {
    Write-SetupLog "Checking MySQL installation"
    $mysql = Find-MySqlExe

    if (-not $mysql -and $InstallMySql) {
        $bundledMySqlInstaller = Find-BundledDependencyInstaller -DependencyName "mysql"
        if ($bundledMySqlInstaller) {
            Write-SetupLog "MySQL executable was not found; using bundled installer"
            Install-MySqlBundled -InstallerPath $bundledMySqlInstaller
        } else {
            Write-SetupLog "MySQL executable was not found; attempting online installation"
            try {
                Invoke-WingetInstall -PackageIds @("Oracle.MySQL")
            } catch {
                Write-SetupLog "winget reported a MySQL installation failure: $($_.Exception.Message)"
                Write-SetupLog "Checking whether MySQL was installed despite the winget status"
                Start-Sleep -Seconds 5
                if (-not (Find-MySqlExe) -or -not (Find-MySqlDExe)) {
                    throw
                }
                Write-SetupLog "MySQL binaries were found after winget failure; continuing setup"
            }
        }
        Start-Sleep -Seconds 5
        $mysql = Find-MySqlExe
    } else {
        Write-SetupLog "MySQL executable already exists; installation will be skipped"
    }

    if (-not $mysql) {
        throw "mysql.exe was not found. Install MySQL Server and rerun setup."
    }

    $mysqld = Find-MySqlDExe
    if (-not $mysqld) {
        throw "mysqld.exe was not found after MySQL installation. Use a bundled MySQL Server package that installs server binaries, or install MySQL Server manually and rerun setup."
    }

    $defaultsFile = New-MySqlDefaultsFile -MySqlDExe $mysqld
    $freshDataDirectory = Initialize-MySqlDataDirectory -MySqlDExe $mysqld -DefaultsFile $defaultsFile
    Install-MySqlService -MySqlDExe $mysqld -DefaultsFile $defaultsFile

    Write-SetupLog "Checking MySQL service state"
    Start-MySqlService
    if (-not (Wait-MySqlReachable)) {
        throw "MySQL is installed but not reachable on 127.0.0.1:3306."
    }

    if ($freshDataDirectory) {
        Set-FreshMySqlRootPassword -MySqlExe $mysql -DefaultsFile $defaultsFile
    } else {
        Repair-MySqlRootPasswordFromTemporaryIfNeeded -MySqlExe $mysql -DefaultsFile $defaultsFile
    }

    Write-SetupLog "MySQL is reachable"
    return $mysql
}

function Find-OllamaExe {
    $commandPath = Find-CommandPath "ollama.exe"
    if ($commandPath) {
        return $commandPath
    }

    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        "$env:ProgramFiles\Ollama\ollama.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function Find-JsonPropertyValue {
    param(
        [object]$InputObject,
        [string[]]$Names
    )

    if ($null -eq $InputObject) {
        return $null
    }
    if ($InputObject -is [string] -or $InputObject.GetType().IsValueType) {
        return $null
    }

    if ($InputObject -is [System.Array]) {
        foreach ($item in $InputObject) {
            $value = Find-JsonPropertyValue -InputObject $item -Names $Names
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                return $value
            }
        }
        return $null
    }

    if ($InputObject.PSObject -and $InputObject.PSObject.Properties) {
        foreach ($property in $InputObject.PSObject.Properties) {
            if ($Names -contains $property.Name -and -not [string]::IsNullOrWhiteSpace([string]$property.Value)) {
                return [string]$property.Value
            }
        }
        foreach ($property in $InputObject.PSObject.Properties) {
            $value = Find-JsonPropertyValue -InputObject $property.Value -Names $Names
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                return $value
            }
        }
    }

    return $null
}

function Get-ConfiguredOllamaModelsPath {
    $envCandidates = @(
        $env:OLLAMA_MODELS,
        [Environment]::GetEnvironmentVariable("OLLAMA_MODELS", "User"),
        [Environment]::GetEnvironmentVariable("OLLAMA_MODELS", "Machine")
    )
    foreach ($candidate in $envCandidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate)) {
            return $candidate
        }
    }

    $serverJsonCandidates = @(
        "$env:USERPROFILE\.ollama\server.json",
        "$env:LOCALAPPDATA\Ollama\server.json",
        "$env:APPDATA\Ollama\server.json",
        "$env:ProgramData\Ollama\server.json"
    )
    $propertyNames = @("OLLAMA_MODELS", "models", "model_dir", "model_path", "modelsPath", "ModelsPath")
    foreach ($path in $serverJsonCandidates) {
        if (Test-Path -LiteralPath $path) {
            try {
                $json = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
                $value = Find-JsonPropertyValue -InputObject $json -Names $propertyNames
                if (-not [string]::IsNullOrWhiteSpace($value)) {
                    return $value
                }
            } catch {
                Write-SetupLog "Could not read Ollama server config: $path"
            }
        }
    }

    return $null
}

function Use-ConfiguredOllamaModelsPath {
    $modelsPath = Get-ConfiguredOllamaModelsPath
    if ([string]::IsNullOrWhiteSpace($modelsPath)) {
        Write-SetupLog "No custom Ollama model directory was detected"
        return
    }

    $env:OLLAMA_MODELS = $modelsPath
    Write-SetupLog "Using configured Ollama model directory: $modelsPath"
}

function Test-Ollama {
    try {
        $response = Invoke-RestMethod -Uri "$($OllamaBaseUrl.TrimEnd('/'))/api/tags" -TimeoutSec 5
        return $null -ne $response
    } catch {
        return $false
    }
}

function Get-OllamaHost {
    try {
        $uri = [Uri]$OllamaBaseUrl
        return $uri.Authority
    } catch {
        return "127.0.0.1:11434"
    }
}

function Start-OllamaServer {
    param([string]$OllamaExe)

    $ollamaOut = Join-Path $logDir "ollama-serve.out.log"
    $ollamaErr = Join-Path $logDir "ollama-serve.err.log"
    $env:OLLAMA_HOST = Get-OllamaHost
    Write-SetupLog "Starting Ollama server on $env:OLLAMA_HOST"
    return Start-Process -FilePath $OllamaExe -ArgumentList "serve" -PassThru -WindowStyle Hidden -RedirectStandardOutput $ollamaOut -RedirectStandardError $ollamaErr
}

function Wait-OllamaReachable {
    param([System.Diagnostics.Process]$Process = $null)

    for ($attempt = 1; $attempt -le 45; $attempt++) {
        if (Test-Ollama) {
            return $true
        }

        if ($null -ne $Process) {
            $Process.Refresh()
            if ($Process.HasExited) {
                $exitCode = $Process.ExitCode
                Write-SetupLog "Ollama server process exited with code $exitCode"

                # Ollama's desktop/background process can start automatically after
                # installation. In that race, our second 'ollama serve' may exit because
                # port 11434 is already bound. Check the API again before treating that
                # process exit as a failure.
                Start-Sleep -Seconds 2
                if (Test-Ollama) {
                    Write-SetupLog "Ollama API is reachable through an existing instance"
                    return $true
                }

                break
            }
        }

        if ($attempt -eq 10 -or $attempt -eq 25 -or $attempt -eq 40) {
            Write-SetupLog "Waiting for Ollama API at $OllamaBaseUrl"
        }
        Start-Sleep -Seconds 2
    }

    $ollamaErr = Join-Path $logDir "ollama-serve.err.log"
    if (Test-Path -LiteralPath $ollamaErr) {
        Write-SetupLog "Ollama stderr tail:"
        Get-Content -LiteralPath $ollamaErr -Tail 20 | ForEach-Object { Write-SetupLog "  $_" }
    }
    return $false
}

function Ensure-Ollama {
    Write-SetupLog "Checking Ollama installation"
    $ollama = Find-OllamaExe

    if (-not $ollama -and $InstallOllama) {
        $bundledOllamaInstaller = Find-BundledDependencyInstaller -DependencyName "ollama"
        if ($bundledOllamaInstaller) {
            Write-SetupLog "Ollama is missing; using bundled installer"
            Install-OllamaFromInstaller -InstallerPath $bundledOllamaInstaller
        } else {
            Write-SetupLog "Ollama is missing; downloading and installing from ollama.com"
            Install-OllamaDirect
        }
        Start-Sleep -Seconds 2
        $ollama = Find-OllamaExe
    }

    if (-not $ollama) {
        throw "ollama.exe was not found. Install Ollama and rerun setup."
    }

    # The Ollama installer can start the server automatically. Give it time to
    # finish its background startup before deciding whether we need to launch it.
    $startupWaitSeconds = 10
    Write-SetupLog "Waiting $startupWaitSeconds seconds before checking Ollama server state"
    Start-Sleep -Seconds $startupWaitSeconds

    if (Test-Ollama) {
        Write-SetupLog "Ollama is already running; manual server start will be skipped"
    } else {
        Write-SetupLog "Ollama is not running after the startup wait; starting Ollama server"
        $process = Start-OllamaServer -OllamaExe $ollama

        if (-not (Wait-OllamaReachable -Process $process)) {
            # Another Ollama instance may have started during the race window.
            # Re-check the API before treating the manual start as a failure.
            Start-Sleep -Seconds 2
            if (-not (Test-Ollama)) {
                throw "Ollama is installed but not reachable at $OllamaBaseUrl."
            }
            Write-SetupLog "Ollama API is reachable through an existing instance"
        }
    }

    Write-SetupLog "Checking whether Ollama API is reachable"
    if (-not (Wait-OllamaReachable)) {
        throw "Ollama is installed but not reachable at $OllamaBaseUrl."
    }

    Write-SetupLog "Ollama is reachable"
    return $ollama
}

function Get-OllamaModelRecommendation {
    try {
        try {
            $computer = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
            $gpu = Get-CimInstance Win32_VideoController -ErrorAction Stop | Sort-Object AdapterRAM -Descending | Select-Object -First 1
        } catch {
            Write-SetupLog "Could not inspect RAM/GPU for automatic model selection; using safe CPU fallback qwen3.5:2b. $($_.Exception.Message)"
            return [pscustomobject]@{
                recommendedModel = "qwen3.5:2b"
                compatibleModels = @("qwen3.5:2b")
                tier = "cpu-fallback"
                reason = "Hardware inspection failed during setup, so the safe CPU fallback model was selected."
                inferenceMode = "cpu"
            }
        }

        $ramGb = [math]::Round($computer.TotalPhysicalMemory / 1GB, 1)
        $gpuVramGb = Get-VideoControllerDedicatedVramGb -Gpu $gpu
        $gpuName = if ($gpu.Name) { [string]$gpu.Name } else { "" }
        $recommendationScript = Join-Path $InstallDir "scripts\Get-ModelRecommendation.ps1"
        if (-not (Test-Path -LiteralPath $recommendationScript)) {
            throw "Model recommendation script was not found: $recommendationScript"
        }

        $global:LASTEXITCODE = $null
        $json = & $recommendationScript -RamGb $ramGb -GpuVramGb $gpuVramGb -GpuName $gpuName
        if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) {
            throw "Model recommendation script exited with code $LASTEXITCODE"
        }
        if ([string]::IsNullOrWhiteSpace(($json | Out-String))) {
            throw "Model recommendation script returned no JSON output."
        }
        $recommendation = $json | ConvertFrom-Json
        if ([string]::IsNullOrWhiteSpace([string]$recommendation.recommendedModel)) {
            throw "Model recommendation script returned JSON without a recommendedModel value."
        }
        return $recommendation
    } catch {
        throw "Could not determine a supported local LLM model. $($_.Exception.Message)"
    }
}

function Update-RuntimeEnv {
    param([hashtable]$Updates)

    Write-SetupLog "Updating runtime environment file"
    Ensure-RuntimeEnvFile

    $lines = if (Test-Path -LiteralPath $envPath) {
        Get-Content -LiteralPath $envPath
    } else {
        @()
    }

    $seen = @{}
    $updated = foreach ($line in $lines) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
            $key = $Matches[1]
            if ($Updates.ContainsKey($key)) {
                $seen[$key] = $true
                Format-EnvAssignment -Key $key -Value ([string]$Updates[$key])
            } else {
                $line
            }
        } else {
            $line
        }
    }

    foreach ($key in $Updates.Keys) {
        if (-not $seen.ContainsKey($key)) {
            $updated += Format-EnvAssignment -Key $key -Value ([string]$Updates[$key])
        }
    }

    Set-Content -LiteralPath $envPath -Value $updated -Encoding UTF8
    Write-SetupLog "Runtime environment file updated"
}

function Invoke-MySqlSql {
    param([string]$MySqlExe, [string]$Sql)

    Write-SetupLog "Running MySQL command"
    $tempSql = Join-Path $env:TEMP ("drtransition-mysql-" + [guid]::NewGuid() + ".sql")
    Set-Content -LiteralPath $tempSql -Value $Sql -Encoding UTF8
    $previous = $env:MYSQL_PWD
    try {
        $env:MYSQL_PWD = $MySqlAdminPassword
        $mysqlOutput = & $MySqlExe --protocol=tcp -h 127.0.0.1 -P 3306 -u $MySqlAdminUser --default-character-set=utf8mb4 -e "source $tempSql" 2>&1
        foreach ($line in $mysqlOutput) {
            Write-Host $line
        }
        if ($LASTEXITCODE -ne 0) {
            throw "mysql.exe exited with code $LASTEXITCODE"
        }
    } finally {
        if ($null -eq $previous) {
            Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue
        } else {
            $env:MYSQL_PWD = $previous
        }
        Remove-Item -LiteralPath $tempSql -Force -ErrorAction SilentlyContinue
    }
}

function Ensure-Database {
    param([string]$MySqlExe)

    Write-SetupLog "Creating/updating database '$DbName' and application user '$AppDbUser'"
    Assert-Identifier -Name "Database name" -Value $DbName
    Assert-Identifier -Name "Application DB user" -Value $AppDbUser
    if ([string]::IsNullOrWhiteSpace($AppDbPassword)) {
        throw "Application DB password is required. Choose a strong local password during setup."
    }
    if ($AppDbPassword -eq "dr_transition_password" -or $AppDbPassword -eq "drtransition_password") {
        throw "Application DB password must not use the sample local-only password from older documentation."
    }

    $quotedPassword = Quote-SqlString $AppDbPassword
    $sql = @"
CREATE DATABASE IF NOT EXISTS ``$DbName`` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$AppDbUser'@'localhost' IDENTIFIED BY $quotedPassword;
CREATE USER IF NOT EXISTS '$AppDbUser'@'127.0.0.1' IDENTIFIED BY $quotedPassword;
ALTER USER '$AppDbUser'@'localhost' IDENTIFIED BY $quotedPassword;
ALTER USER '$AppDbUser'@'127.0.0.1' IDENTIFIED BY $quotedPassword;
GRANT ALL PRIVILEGES ON ``$DbName``.* TO '$AppDbUser'@'localhost';
GRANT ALL PRIVILEGES ON ``$DbName``.* TO '$AppDbUser'@'127.0.0.1';
FLUSH PRIVILEGES;
"@
    Invoke-MySqlSql -MySqlExe $MySqlExe -Sql $sql
    Write-SetupLog "Database and application user are ready"
}

function Invoke-DatabaseSeed {
    if ($SkipDatabaseSeed) {
        Write-SetupLog "Skipping database seed"
        return
    }
    if (-not (Test-Path -LiteralPath $backendExe)) {
        throw "Bundled backend executable not found: $backendExe"
    }

    $seedOut = Join-Path $logDir "seed-database.out.log"
    $seedErr = Join-Path $logDir "seed-database.err.log"
    Write-SetupLog "Seeding SQLite database through bundled backend"
    Write-SetupLog "Foreground seed options: IncludeBasicData=$IncludeBasicData, SeedPromptsFromFiles=$SeedPromptsFromFiles, SkipReferenceData=$SkipReferenceData, SkipDefaultAppUser=$SkipDefaultAppUser"
    Write-SetupLog "Background KB options: ReindexSectorPrompts=$ReindexSectorPrompts, SeedMainKbFromFiles=$SeedMainKbFromFiles"
    $seedArgs = @(
        "--seed-database"
    )
    if ($IncludeBasicData) {
        $seedArgs += "--include-basic-data"
    }
    if ($SeedPromptsFromFiles) {
        $seedArgs += "--seed-prompts-from-files"
    }
    if ($SkipDefaultAppUser) {
        $seedArgs += "--skip-default-user"
    } else {
        $seedArgs += @(
            "--default-user-email", $DefaultAppUserEmail,
            "--default-user-password", $DefaultAppUserPassword,
            "--default-user-name", $DefaultAppUserName,
            "--default-user-designation", $DefaultAppUserDesignation,
            "--default-user-organisation-type", $DefaultAppUserOrganisationType,
            "--default-user-organisation-name", $DefaultAppUserOrganisationName,
            "--default-user-role", $DefaultAppUserRole
        )
    }
    # OfflineAdmin now uses the canonical app.seed_data reference-data path for
    # SQLite as well as MySQL. Sync clients skip local reference seeding and
    # receive reference data from the server.
    if ($SkipReferenceData) {
        $seedArgs += "--skip-reference-data"
    }
    $process = Start-Process -FilePath $backendExe -ArgumentList (Join-ProcessArguments $seedArgs) -WorkingDirectory $InstallDir -PassThru -WindowStyle Hidden -RedirectStandardOutput $seedOut -RedirectStandardError $seedErr
    $exitCode = Wait-SetupProcess -Process $process -Activity "Database seed" -HeartbeatSeconds 15
    $seedSucceeded = $false
    if (Test-Path -LiteralPath $seedOut) {
        $successPattern = if ($SkipReferenceData) {
            "Database schema prepared successfully."
        } else {
            "Database reference data seeded successfully."
        }
        $seedSucceeded = [bool](Select-String -LiteralPath $seedOut -Pattern $successPattern -Quiet)
    }
    if (($null -eq $exitCode -and -not $seedSucceeded) -or ($null -ne $exitCode -and $exitCode -ne 0)) {
        if (Test-Path -LiteralPath $seedOut) {
            Write-SetupLog "Seed stdout tail:"
            Get-Content -LiteralPath $seedOut -Tail 20 | ForEach-Object { Write-SetupLog "  $_" }
        }
        if (Test-Path -LiteralPath $seedErr) {
            Write-SetupLog "Seed stderr tail:"
            Get-Content -LiteralPath $seedErr -Tail 20 | ForEach-Object { Write-SetupLog "  $_" }
        }
        $exitText = if ($null -eq $exitCode) { "unknown" } else { [string]$exitCode }
        throw "Database seed failed with exit code $exitText. Check $seedErr"
    }
    if ($null -eq $exitCode -and $seedSucceeded) {
        Write-SetupLog "Database seed process exit code was unavailable; seed success output was detected"
    }
    if ($SkipDefaultAppUser) {
        Write-SetupLog "Default app user creation skipped"
    } else {
        Write-SetupLog "Default app user is ready: $DefaultAppUserEmail"
        Write-DefaultAppUserCredentialsSummary -SeedOutputPath $seedOut
    }
    if ($SkipReferenceData) {
        Write-SetupLog "Reference data seed skipped; schema is ready for startup sync"
    }
    Write-SetupLog "Database setup completed"
}

function Write-DefaultAppUserCredentialsSummary {
    param([string]$SeedOutputPath)

    if ([string]::IsNullOrWhiteSpace($DefaultAppUserCredentialsPath)) {
        return
    }

    $status = "ready"
    $passwordForDisplay = $DefaultAppUserPassword
    if (Test-Path -LiteralPath $SeedOutputPath) {
        $seedLines = Get-Content -LiteralPath $SeedOutputPath
        if ($seedLines | Where-Object { $_ -match '^Default app user created:' }) {
            $status = "created"
        } elseif ($seedLines | Where-Object { $_ -match '^Default app user ready:' }) {
            $status = "ready"
        }
        $generatedPasswordLine = $seedLines | Where-Object { $_ -match '^Generated default app user password: (.+)$' } | Select-Object -Last 1
        if ($generatedPasswordLine -match '^Generated default app user password: (.+)$') {
            $passwordForDisplay = $Matches[1]
        }
    }

    $summaryLines = @(
        "Email: $($DefaultAppUserEmail.Trim().ToLowerInvariant())",
        "Role: $DefaultAppUserRole",
        "Status: $status"
    )
    if ([string]::IsNullOrWhiteSpace($passwordForDisplay)) {
        $summaryLines += "Password: existing account password was preserved"
    } else {
        $summaryLines += "Password: $passwordForDisplay"
    }

    $credentialsDir = Split-Path -Parent $DefaultAppUserCredentialsPath
    if (-not [string]::IsNullOrWhiteSpace($credentialsDir)) {
        New-Item -ItemType Directory -Force -Path $credentialsDir | Out-Null
    }
    Set-Content -LiteralPath $DefaultAppUserCredentialsPath -Value $summaryLines -Encoding ASCII
    Write-SetupLog "Default app user summary written to $DefaultAppUserCredentialsPath"
}

function Start-BackgroundKnowledgeBaseSeed {
    # Basic/reference data, prompt rows, hazards and mitigation data are seeded
    # synchronously by Invoke-DatabaseSeed. Only KB/indexing/embedding work runs here.
    if (-not $ReindexSectorPrompts -and -not $SeedMainKbFromFiles) {
        Write-SetupLog "Background KB seed skipped; no KB/indexing work is enabled"
        return
    }
    if (-not (Test-Path -LiteralPath $backendExe)) {
        Write-SetupLog "Background KB seed skipped; bundled backend executable was not found: $backendExe"
        return
    }

    $kbSeedOut = Join-Path $logDir "seed-kb-background.out.log"
    $kbSeedErr = Join-Path $logDir "seed-kb-background.err.log"
    $kbSeedArgs = @(
        "--seed-database",
        "--skip-schema",
        "--skip-reference-data",
        "--skip-default-user"
    )

    if ($ReindexSectorPrompts) {
        # Sector prompt RAG indexing creates KB chunks/embeddings, so do not block setup.
        $kbSeedArgs += "--reindex-sector-prompts"
    }
    if ($SeedMainKbFromFiles) {
        # Bundled PDF ingestion/chunking/embedding is also background-only.
        $kbSeedArgs += "--seed-main-kb-from-files"
    }

    # Embedding calls made by this background worker do not need to write an
    # llm_exchange_logs row for every chunk. Disabling DB LLM logging only for
    # the child process avoids unnecessary SQLite writer contention while the
    # normal application keeps its own configured logging behaviour.
    $previousLlmLogToDb = $env:LLM_LOG_TO_DB
    try {
        $env:LLM_LOG_TO_DB = "false"

        $process = Start-Process `
            -FilePath $backendExe `
            -ArgumentList (Join-ProcessArguments $kbSeedArgs) `
            -WorkingDirectory $InstallDir `
            -PassThru `
            -WindowStyle Hidden `
            -RedirectStandardOutput $kbSeedOut `
            -RedirectStandardError $kbSeedErr

        Write-SetupLog "KB/indexing seed started in the background with process id $($process.Id)"
        Write-SetupLog "Background KB seed logs: $kbSeedOut and $kbSeedErr"
    } catch {
        # KB generation must not block/fail the installer after the relational seed succeeds.
        Write-SetupLog "Background KB seed could not be started; installer will continue. $($_.Exception.Message)"
    } finally {
        if ($null -eq $previousLlmLogToDb) {
            Remove-Item Env:LLM_LOG_TO_DB -ErrorAction SilentlyContinue
        } else {
            $env:LLM_LOG_TO_DB = $previousLlmLogToDb
        }
    }
}

function Get-OllamaModels {
    try {
        $tags = Invoke-RestMethod -Uri "$($OllamaBaseUrl.TrimEnd('/'))/api/tags" -TimeoutSec 5
        return @($tags.models | ForEach-Object { $_.name })
    } catch {
        return @()
    }
}

function Ensure-OllamaModel {
    param([string]$OllamaExe, [string]$Model)
    $Model = Normalize-OllamaModelName -Model $Model
    if ([string]::IsNullOrWhiteSpace($Model)) {
        return
    }
    $existing = Get-OllamaModels
    if ($existing -contains $Model -or $existing -contains "$Model`:latest") {
        Write-SetupLog "Ollama model already present: $Model"
        return
    }
    if (-not $PullModels) {
        Write-SetupLog "Model pull disabled, missing model: $Model"
        return
    }

    Write-SetupLog "Pulling Ollama model: $Model"
    Write-SetupProgress -Activity "Downloading Ollama model $Model" -Percent 1 -Status "Preparing model download..."
    $exitCode = Invoke-VisibleSetupCommand -FilePath $OllamaExe -Arguments @("pull", $Model) -Activity "Downloading Ollama model $Model" -StartPercent 2 -EndPercent 100
    if ($exitCode -ne 0) {
        throw "ollama pull $Model failed with exit code $exitCode"
    }
    Write-SetupLog "Ollama model is ready: $Model"
}

try {
    Write-SetupLog "Starting Dr Transition dependency setup"

    if ($DependenciesOnly) {
        Write-SetupLog "Dependencies-only mode enabled; installing/checking Ollama only"
        Ensure-Ollama | Out-Null
        Write-SetupLog "Dr Transition Ollama dependency setup completed"
        return
    }

    $OllamaModel = Normalize-OllamaModelName -Model $OllamaModel
    $OllamaEmbeddingModel = Normalize-OllamaModelName -Model $OllamaEmbeddingModel

    if ([string]::IsNullOrWhiteSpace($OllamaModel) -or $OllamaModel -eq "auto" -or $OllamaModel -eq "none") {
        $recommendation = Get-OllamaModelRecommendation
        if ([string]$recommendation.recommendedModel -eq "none") {
            throw "Local LLM setup is not supported on this computer. $($recommendation.reason)"
        }
        $OllamaModel = Normalize-OllamaModelName -Model ([string]$recommendation.recommendedModel)
        Write-SetupLog "Recommended Ollama model selected: $OllamaModel ($($recommendation.tier))"
    }

    # Windows offline/client packages are SQLite-only. MySQL is not an installer dependency.
    $useMySqlDatabase = $false
    $sqliteDatabasePath = Join-Path $runtimeDataDir "dr_transition.db"
    New-Item -ItemType Directory -Force -Path $runtimeDataDir | Out-Null
    $databaseUrl = "sqlite:///$($sqliteDatabasePath.Replace('\', '/'))"
    $syncDeviceId = Get-OrCreate-SyncDeviceId
    $secretKey = Get-OrCreate-SecretKey
    $runtimeEnvUpdates = @{
        APP_MODE = "client"
        SECRET_KEY = $secretKey
        DATABASE_URL = $databaseUrl
        SQLITE_DATABASE_PATH = $sqliteDatabasePath
        FAISS_INDEX_PATH = (Join-Path $runtimeDataDir "knowledge.faiss")
        LLM_LOG_PATH = (Join-Path $runtimeLogDir "llm_requests.jsonl")
        OLLAMA_BASE_URL = $OllamaBaseUrl
        OLLAMA_MODEL = $OllamaModel
        OLLAMA_EMBEDDING_MODEL = $OllamaEmbeddingModel
        SYNC_DEVICE_ID = $syncDeviceId
    }
    if ($DisableSync) {
        $runtimeEnvUpdates.SYNC_ENABLED = "false"
        $runtimeEnvUpdates.SYNC_MODE = "client"
        $runtimeEnvUpdates.SYNC_SERVER_URL = ""
        $runtimeEnvUpdates.SYNC_API_TOKEN = ""
        $runtimeEnvUpdates.SYNC_AUTO_ON_STARTUP = "false"
        $runtimeEnvUpdates.SYNC_INTERVAL_SECONDS = "0"
    }
    Update-RuntimeEnv -Updates $runtimeEnvUpdates

    Write-SetupLog "Configured SQLite client database: $sqliteDatabasePath"
    Use-ConfiguredOllamaModelsPath
    $ollamaExe = Ensure-Ollama
    Ensure-OllamaModel -OllamaExe $ollamaExe -Model $OllamaModel
    Ensure-OllamaModel -OllamaExe $ollamaExe -Model $OllamaEmbeddingModel
    # Database seeding is backend-driven and uses DATABASE_URL, so it now targets SQLite.
    Invoke-DatabaseSeed
    Start-BackgroundKnowledgeBaseSeed

    Write-SetupLog "Dr Transition dependency setup completed"
} catch {
    Write-SetupLog "ERROR: $($_.Exception.Message)"
    throw
}
