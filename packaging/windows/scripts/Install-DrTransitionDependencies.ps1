param(
    [string]$InstallDir,

    [string]$ConfigPath = "",

    [string]$DbName = "dr_transition",

    [string]$MySqlAdminUser = "root",
    [string]$MySqlAdminPassword = "",
    [string]$AppDbUser = "dr_transition",
    [string]$AppDbPassword = "dr_transition_password",
    [string]$DefaultAppUserEmail = "admin@drtransition.local",
    [string]$DefaultAppUserPassword = "DrTransition@123",
    [string]$DefaultAppUserName = "Dr Transition Admin",
    [string]$DefaultAppUserDesignation = "Administrator",
    [string]$DefaultAppUserOrganisationType = "Local",
    [string]$DefaultAppUserOrganisationName = "Dr Transition",
    [string]$OllamaModel = "",
    [string]$OllamaEmbeddingModel = "nomic-embed-text",
    [string]$OllamaBaseUrl = "http://127.0.0.1:11434",
    [switch]$InstallMySql,
    [switch]$InstallOllama,
    [switch]$PullModels,
    [switch]$SkipDatabaseSeed
)

$ErrorActionPreference = "Stop"

$programData = Join-Path $env:ProgramData "DrTransition"
$logDir = Join-Path $env:LOCALAPPDATA "DrTransition\logs"
$envPath = Join-Path $programData ".env"
$runtimeTemplate = Join-Path $InstallDir "config\runtime.env"
$backendExe = Join-Path $InstallDir "backend\drtransition-backend\drtransition-backend.exe"
$setupLog = Join-Path $logDir "installer-setup.log"

New-Item -ItemType Directory -Force -Path $programData | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

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
            "OllamaModel" { $OllamaModel = [string]$property.Value }
            "OllamaEmbeddingModel" { $OllamaEmbeddingModel = [string]$property.Value }
            "OllamaBaseUrl" { $OllamaBaseUrl = [string]$property.Value }
            "InstallMySql" { if ([bool]$property.Value) { $InstallMySql = $true } }
            "InstallOllama" { if ([bool]$property.Value) { $InstallOllama = $true } }
            "PullModels" { if ([bool]$property.Value) { $PullModels = $true } }
            "SkipDatabaseSeed" { if ([bool]$property.Value) { $SkipDatabaseSeed = $true } }
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
    return $Process.ExitCode
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

function Find-CommandPath {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
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

function Invoke-WingetInstall {
    param([string[]]$PackageIds)
    $winget = Find-CommandPath "winget.exe"
    if (-not $winget) {
        throw "winget.exe was not found. Install App Installer from Microsoft Store, or install the dependency manually."
    }

    foreach ($packageId in $PackageIds) {
        Write-SetupLog "Trying winget install $packageId"
        $process = Start-Process -FilePath $winget -ArgumentList @(
            "install",
            "--id", $packageId,
            "--exact",
            "--silent",
            "--accept-package-agreements",
            "--accept-source-agreements"
        ) -PassThru -WindowStyle Hidden
        $exitCode = Wait-SetupProcess -Process $process -Activity "winget install $packageId" -HeartbeatSeconds 20
        if ($exitCode -eq 0) {
            Write-SetupLog "winget installed $packageId"
            return
        }
        Write-SetupLog "winget failed for $packageId with exit code $exitCode"
    }

    throw "Could not install any package from: $($PackageIds -join ', ')"
}

function Start-MySqlService {
    $services = Get-Service -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(MySQL|MySQL80|MySQL84|MariaDB)' -or $_.DisplayName -match 'MySQL|MariaDB' }
    foreach ($service in $services) {
        if ($service.Status -ne "Running") {
            Write-SetupLog "Starting service $($service.Name)"
            Start-Service -Name $service.Name -ErrorAction SilentlyContinue
        }
    }
}

function Ensure-MySql {
    Write-SetupLog "Checking MySQL installation"
    $mysql = Find-MySqlExe

    if (-not $mysql -and $InstallMySql) {
        Write-SetupLog "MySQL executable was not found; attempting installation"
        Invoke-WingetInstall -PackageIds @("Oracle.MySQL", "Oracle.MySQLInstaller")
        Start-Sleep -Seconds 5
        $mysql = Find-MySqlExe
    } else {
        Write-SetupLog "MySQL executable already exists; installation will be skipped"
    }

    if (-not $mysql) {
        throw "mysql.exe was not found. Install MySQL Server and rerun setup."
    }

    Write-SetupLog "Checking MySQL service state"
    Start-MySqlService
    Start-Sleep -Seconds 5

    if (-not (Test-TcpPort -HostName "127.0.0.1" -Port 3306)) {
        throw "MySQL is installed but not reachable on 127.0.0.1:3306."
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
        $response = Invoke-RestMethod -Uri $OllamaBaseUrl -TimeoutSec 2
        return $null -ne $response
    } catch {
        return $false
    }
}

function Ensure-Ollama {
    Write-SetupLog "Checking Ollama installation"
    $ollama = Find-OllamaExe
    if (-not $ollama -and $InstallOllama) {
        Write-SetupLog "Ollama is missing; attempting installation"
        Invoke-WingetInstall -PackageIds @("Ollama.Ollama")
        Start-Sleep -Seconds 5
        $ollama = Find-OllamaExe
    }

    if (-not $ollama) {
        throw "ollama.exe was not found. Install Ollama and rerun setup."
    }
    Write-SetupLog "Ollama executable is available; installation will be skipped"

    if (-not (Test-Ollama)) {
        Write-SetupLog "Starting Ollama service"
        Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 8
    }

    if (-not (Test-Ollama)) {
        throw "Ollama is installed but not reachable at $OllamaBaseUrl."
    }

    Write-SetupLog "Ollama is reachable"
    return $ollama
}

function Get-RecommendedOllamaModel {
    try {
        $computer = Get-CimInstance Win32_ComputerSystem
        $gpu = Get-CimInstance Win32_VideoController | Sort-Object AdapterRAM -Descending | Select-Object -First 1
        $ramGb = [math]::Round($computer.TotalPhysicalMemory / 1GB, 1)
        $gpuVramGb = if ($gpu.AdapterRAM) { [math]::Round($gpu.AdapterRAM / 1GB, 1) } else { 0 }

        if ($ramGb -lt 8) {
            return "llama3.2:3b"
        }
        if ($gpuVramGb -ge 12 -and $ramGb -ge 32) {
            return "qwen2.5:14b"
        }
        if ($ramGb -ge 32) {
            return "mistral-nemo"
        }
        if ($ramGb -ge 16) {
            return "mistral"
        }
        return "llama3.2:3b"
    } catch {
        return "llama3.2:3b"
    }
}

function Update-RuntimeEnv {
    param([hashtable]$Updates)

    Write-SetupLog "Updating runtime environment file"
    if (-not (Test-Path -LiteralPath $envPath)) {
        if (Test-Path -LiteralPath $runtimeTemplate) {
            Copy-Item -LiteralPath $runtimeTemplate -Destination $envPath -Force
        } else {
            New-Item -ItemType File -Path $envPath -Force | Out-Null
        }
    }

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
                "$key=""$($Updates[$key])"""
            } else {
                $line
            }
        } else {
            $line
        }
    }

    foreach ($key in $Updates.Keys) {
        if (-not $seen.ContainsKey($key)) {
            $updated += "$key=""$($Updates[$key])"""
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
        & $MySqlExe --protocol=tcp -h 127.0.0.1 -P 3306 -u $MySqlAdminUser --default-character-set=utf8mb4 -e "source $tempSql"
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
    Write-SetupLog "Seeding database through bundled backend"
    $seedArgs = @(
        "--seed-database",
        "--default-user-email", $DefaultAppUserEmail,
        "--default-user-password", $DefaultAppUserPassword,
        "--default-user-name", $DefaultAppUserName,
        "--default-user-designation", $DefaultAppUserDesignation,
        "--default-user-organisation-type", $DefaultAppUserOrganisationType,
        "--default-user-organisation-name", $DefaultAppUserOrganisationName
    )
    $process = Start-Process -FilePath $backendExe -ArgumentList (Join-ProcessArguments $seedArgs) -WorkingDirectory $InstallDir -PassThru -WindowStyle Hidden -RedirectStandardOutput $seedOut -RedirectStandardError $seedErr
    $exitCode = Wait-SetupProcess -Process $process -Activity "Database seed" -HeartbeatSeconds 15
    if ($exitCode -ne 0) {
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
    Write-SetupLog "Default app user is ready: $DefaultAppUserEmail"
    Write-SetupLog "Database seed completed"
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
    $process = Start-Process -FilePath $OllamaExe -ArgumentList @("pull", $Model) -PassThru -WindowStyle Hidden
    $exitCode = Wait-SetupProcess -Process $process -Activity "ollama pull $Model" -HeartbeatSeconds 20
    if ($exitCode -ne 0) {
        throw "ollama pull $Model failed with exit code $exitCode"
    }
    Write-SetupLog "Ollama model is ready: $Model"
}

try {
    Write-SetupLog "Starting Dr Transition dependency setup"

    if ([string]::IsNullOrWhiteSpace($OllamaModel) -or $OllamaModel -eq "auto" -or $OllamaModel -eq "none") {
        $OllamaModel = Get-RecommendedOllamaModel
        Write-SetupLog "Recommended Ollama model selected: $OllamaModel"
    }

    $databaseUrl = "mysql+pymysql://$AppDbUser`:$AppDbPassword@localhost:3306/$DbName"
    Update-RuntimeEnv -Updates @{
        DATABASE_URL = $databaseUrl
        OLLAMA_BASE_URL = $OllamaBaseUrl
        OLLAMA_MODEL = $OllamaModel
        OLLAMA_EMBEDDING_MODEL = $OllamaEmbeddingModel
    }

    $mysqlExe = Ensure-MySql
    Ensure-Database -MySqlExe $mysqlExe
    Invoke-DatabaseSeed

    Use-ConfiguredOllamaModelsPath
    $ollamaExe = Ensure-Ollama
    Ensure-OllamaModel -OllamaExe $ollamaExe -Model $OllamaModel
    Ensure-OllamaModel -OllamaExe $ollamaExe -Model $OllamaEmbeddingModel

    Write-SetupLog "Dr Transition dependency setup completed"
} catch {
    Write-SetupLog "ERROR: $($_.Exception.Message)"
    throw
}
