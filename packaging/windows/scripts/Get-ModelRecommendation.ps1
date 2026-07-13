param(
    [double]$RamGb,
    [double]$GpuVramGb = 0,
    [string]$GpuName = ""
)

$ErrorActionPreference = "Stop"

# Minimum requirement: 8 GB RAM and 2 GB GPU VRAM

# Minimum requirement: 8 GB RAM and 2 GB GPU VRAM

if ($RamGb -lt 8 -or $GpuVramGb -lt 2) {
    $model = "none"
    $reason = "This system does not meet the minimum requirement of 8 GB RAM and 2 GB GPU VRAM."
}
elseif ($RamGb -ge 48 -and $GpuVramGb -ge 12) {
    $model = "ministral:8b"
    $reason = "Excellent hardware detected. Ministral 8B is recommended for maximum quality and performance."
}
elseif ($RamGb -ge 32 -and $GpuVramGb -ge 8) {
    $model = "mistral-nemo"
    $reason = "High-end hardware detected. Mistral Nemo provides excellent reasoning and RAG performance."
}
elseif ($RamGb -ge 24 -and $GpuVramGb -ge 6) {
    $model = "qwen2.5:7b"
    $reason = "Upper mid-range hardware detected. Qwen 2.5 7B provides strong reasoning and structured output."
}
elseif ($RamGb -ge 16 -and $GpuVramGb -ge 4) {
    $model = "ministral:3b"
    $reason = "Mid-range hardware detected. Ministral 3B offers excellent performance while remaining efficient."
}
elseif ($RamGb -ge 8 -and $GpuVramGb -ge 2) {
    $model = "qwen2.5:3b"
    $reason = "Entry-level hardware detected. Qwen 2.5 3B is optimized for limited resources."
}
else {
    # Fallback (should rarely be reached)
    $model = "qwen2.5:3b"
    $reason = "Using the smallest supported model due to hardware limitations."
}

[ordered]@{
    recommendedModel = $model
    reason = $reason
    ramGb = $RamGb
    gpuVramGb = $GpuVramGb
    gpuName = $GpuName
} | ConvertTo-Json -Depth 3
