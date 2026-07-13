param(
    [double]$RamGb,
    [double]$GpuVramGb = 0,
    [string]$GpuName = ""
)

$ErrorActionPreference = "Stop"

# Hardware requirements:
# - 8 GB RAM minimum
# - Supported dedicated GPU required
# - 2 GB GPU VRAM minimum

$MinimumRamGb = 8
$MinimumGpuVramGb = 2

$GpuSupportPatterns = @(
    "nvidia\s+geforce",
    "nvidia\s+rtx",
    "nvidia\s+gtx",
    "nvidia\s+quadro",
    "nvidia\s+tesla",
    "nvidia\s+(a\d{3,5}|a-series)",
    "amd\s+radeon",
    "amd\s+rx",
    "amd\s+firepro",
    "amd\s+instinct",
    "intel\s+arc"
)

$UnsupportedGpuPatterns = @(
    "intel\s+uhd",
    "intel\s+iris",
    "intel\s+hd\s+graphics",
    "microsoft\s+basic\s+display",
    "vmware",
    "hyper-v",
    "virtualbox",
    "virtual",
    "remote\s+display",
    "standard\s+vga"
)

$ModelTiers = @(
    [ordered]@{
        Tier = "workstation"
        MinRamGb = 48
        MinGpuVramGb = 12
        RecommendedModel = "ministral-3:14b"
        Reason = "Workstation-class hardware detected. Ministral 3 14B is recommended for the highest local reasoning quality."
    },
    [ordered]@{
        Tier = "high"
        MinRamGb = 32
        MinGpuVramGb = 8
        RecommendedModel = "mistral-nemo"
        Reason = "High-end dedicated GPU hardware detected. Mistral Nemo provides strong reasoning and RAG performance."
    },
    [ordered]@{
        Tier = "upper-mid"
        MinRamGb = 24
        MinGpuVramGb = 6
        RecommendedModel = "ministral-3:8b"
        Reason = "Upper mid-range workstation detected. Ministral 3 8B balances quality with local performance."
    },
    [ordered]@{
        Tier = "mid"
        MinRamGb = 16
        MinGpuVramGb = 4
        RecommendedModel = "qwen2.5:7b"
        Reason = "Mid-range dedicated GPU hardware detected. Qwen 2.5 7B is recommended for reliable structured output."
    },
    [ordered]@{
        Tier = "entry"
        MinRamGb = 8
        MinGpuVramGb = 2
        RecommendedModel = "qwen2.5:3b"
        Reason = "Entry-level dedicated GPU hardware detected. Qwen 2.5 3B is recommended for limited local resources."
    }
)

function Test-GpuSupported {
    param(
        [string]$Name
    )

    $normalizedName = [string]$Name
    $normalizedName = $normalizedName.Trim().ToLowerInvariant()
    if (-not $normalizedName) {
        return $false
    }

    foreach ($pattern in $UnsupportedGpuPatterns) {
        if ($normalizedName -match $pattern) {
            return $false
        }
    }

    foreach ($pattern in $GpuSupportPatterns) {
        if ($normalizedName -match $pattern) {
            return $true
        }
    }

    return $false
}

function New-Recommendation {
    param(
        [string]$RecommendedModel,
        [string]$Tier,
        [string]$Reason,
        [double]$RamGb,
        [double]$GpuVramGb,
        [string]$GpuName,
        [bool]$GpuSupported
    )

    [ordered]@{
        recommendedModel = $RecommendedModel
        tier = $Tier
        reason = $Reason
        ramGb = $RamGb
        gpuVramGb = $GpuVramGb
        gpuName = $GpuName
        gpuSupported = $GpuSupported
    }
}

$GpuSupported = Test-GpuSupported -Name $GpuName

if ($RamGb -lt $MinimumRamGb) {
    $recommendation = New-Recommendation `
        -RecommendedModel "none" `
        -Tier "unsupported" `
        -Reason "Minimum 8 GB RAM is required for local Dr Transition model inference." `
        -RamGb $RamGb `
        -GpuVramGb $GpuVramGb `
        -GpuName $GpuName `
        -GpuSupported $GpuSupported
}
elseif (-not $GpuSupported) {
    $recommendation = New-Recommendation `
        -RecommendedModel "none" `
        -Tier "unsupported" `
        -Reason "Dedicated supported GPU not detected. NVIDIA GeForce/RTX/GTX/Quadro/Tesla/A-series, AMD Radeon/RX/FirePro/Instinct, or Intel Arc is required." `
        -RamGb $RamGb `
        -GpuVramGb $GpuVramGb `
        -GpuName $GpuName `
        -GpuSupported $GpuSupported
}
elseif ($GpuVramGb -lt $MinimumGpuVramGb) {
    $recommendation = New-Recommendation `
        -RecommendedModel "none" `
        -Tier "unsupported" `
        -Reason "Minimum 2 GB dedicated GPU VRAM is required for local Dr Transition model inference." `
        -RamGb $RamGb `
        -GpuVramGb $GpuVramGb `
        -GpuName $GpuName `
        -GpuSupported $GpuSupported
}
else {
    $selectedTier = $ModelTiers | Where-Object {
        $RamGb -ge $_["MinRamGb"] -and $GpuVramGb -ge $_["MinGpuVramGb"]
    } | Select-Object -First 1

    if ($null -eq $selectedTier) {
        $recommendation = New-Recommendation `
            -RecommendedModel "none" `
            -Tier "unsupported" `
            -Reason "Hardware does not match a supported local model tier after strict RAM and dedicated GPU VRAM validation." `
            -RamGb $RamGb `
            -GpuVramGb $GpuVramGb `
            -GpuName $GpuName `
            -GpuSupported $GpuSupported
    }
    else {
        $recommendation = New-Recommendation `
            -RecommendedModel $selectedTier["RecommendedModel"] `
            -Tier $selectedTier["Tier"] `
            -Reason $selectedTier["Reason"] `
            -RamGb $RamGb `
            -GpuVramGb $GpuVramGb `
            -GpuName $GpuName `
            -GpuSupported $GpuSupported
    }
}

$recommendation | ConvertTo-Json -Depth 3
