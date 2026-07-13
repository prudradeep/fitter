$ErrorActionPreference = "Stop"

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

$os = Get-CimInstance Win32_OperatingSystem
$computer = Get-CimInstance Win32_ComputerSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$gpu = Get-CimInstance Win32_VideoController | Sort-Object AdapterRAM -Descending | Select-Object -First 1
$systemDrive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($env:SystemDrive)'"

$ramGb = [math]::Round($computer.TotalPhysicalMemory / 1GB, 1)
$diskFreeGb = [math]::Round($systemDrive.FreeSpace / 1GB, 1)
$gpuVramGb = Get-VideoControllerDedicatedVramGb -Gpu $gpu
$isX64 = [Environment]::Is64BitOperatingSystem
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

$checks = [ordered]@{
    windowsVersion = $os.Caption
    buildNumber = [int]$os.BuildNumber
    architecture = if ($isX64) { "x64" } else { "unsupported" }
    administrator = $isAdmin
    ramGb = $ramGb
    cpu = $cpu.Name
    cpuCores = $cpu.NumberOfCores
    cpuLogicalProcessors = $cpu.NumberOfLogicalProcessors
    gpu = $gpu.Name
    gpuVramGb = $gpuVramGb
    systemDriveFreeGb = $diskFreeGb
    internetAvailable = Test-NetConnection -ComputerName "ollama.com" -Port 443 -InformationLevel Quiet
    compatible = ($isX64 -and $ramGb -ge 8 -and $diskFreeGb -ge 10)
}

$checks | ConvertTo-Json -Depth 4
