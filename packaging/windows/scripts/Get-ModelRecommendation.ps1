param(
    [double]$RamGb,
    [double]$GpuVramGb = 0,
    [string]$GpuName = ""
)

$ErrorActionPreference = "Stop"

if ($RamGb -lt 8) {
    $model = "none"
    $reason = "Less than 8 GB RAM is available, so a local LLM is not recommended."
} elseif ($GpuVramGb -ge 12 -and $RamGb -ge 32) {
    $model = "qwen2.5:14b"
    $reason = "This PC has at least 32 GB RAM and high GPU VRAM, so a larger local model is reasonable."
} elseif ($RamGb -ge 32) {
    $model = "mistral-nemo"
    $reason = "This PC has at least 32 GB RAM, which is suitable for the default higher-quality model."
} elseif ($RamGb -ge 16) {
    $model = "mistral"
    $reason = "This PC has at least 16 GB RAM, which is suitable for a balanced local model."
} else {
    $model = "llama3.2:3b"
    $reason = "This PC has 8-15 GB RAM, so a smaller local model is recommended."
}

[ordered]@{
    recommendedModel = $model
    reason = $reason
    ramGb = $RamGb
    gpuVramGb = $GpuVramGb
    gpuName = $GpuName
} | ConvertTo-Json -Depth 3
