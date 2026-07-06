$ErrorActionPreference = "Stop"

$os = Get-CimInstance Win32_OperatingSystem
$computer = Get-CimInstance Win32_ComputerSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$gpu = Get-CimInstance Win32_VideoController | Sort-Object AdapterRAM -Descending | Select-Object -First 1
$systemDrive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($env:SystemDrive)'"

$ramGb = [math]::Round($computer.TotalPhysicalMemory / 1GB, 1)
$diskFreeGb = [math]::Round($systemDrive.FreeSpace / 1GB, 1)
$gpuVramGb = if ($gpu.AdapterRAM) { [math]::Round($gpu.AdapterRAM / 1GB, 1) } else { 0 }
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
