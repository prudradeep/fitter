param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(0, 1024)]
    [double]$RamGb,

    [ValidateRange(0, 256)]
    [double]$GpuVramGb = 0,

    [AllowEmptyString()]
    [string]$GpuName = ""
)

$ErrorActionPreference = "Stop"

# -------------------------------------------------------
# Hardware requirements
# -------------------------------------------------------

$MinimumRamGb = 8
$MinimumGpuVramGb = 2

# -------------------------------------------------------
# GPU detection patterns
# -------------------------------------------------------

$GpuSupportPatterns = @(
    "nvidia\s+geforce",
    "nvidia\s+(rtx|gtx)",
    "nvidia\s+quadro",
    "nvidia\s+tesla",
    "nvidia\s+(a\d{3,5}|a-series)",
    "amd\s+radeon",
    "amd\s+rx",
    "amd\s+firepro",
    "amd\s+instinct",
    "radeon\s+rx",
    "intel\s+arc"
)

$UnsupportedGpuPatterns = @(
    "intel\s+uhd",
    "intel\s+iris",
    "intel\s+hd\s+graphics",
    "microsoft\s+basic\s+display",
    "microsoft\s+remote\s+display",
    "vmware",
    "hyper-v",
    "virtualbox",
    "virtual",
    "remote\s+display",
    "standard\s+vga"
)

# -------------------------------------------------------
# Model tiers
#
# Keep tiers ordered from highest to lowest.
# Both RAM and dedicated GPU VRAM requirements must pass.
#
# Multiple tiers may use the same RAM requirement with
# different VRAM requirements. The highest compatible
# tier is selected automatically.
# -------------------------------------------------------

$ModelTiers = @(
    [ordered]@{
        Tier = "enthusiast"
        MinRamGb = 64
        MinGpuVramGb = 24
        RecommendedModel = "qwen3.5:27b"
        ContextLength = 16384
        Reason = "Enthusiast-class hardware detected. Qwen 3.5 27B is recommended for the strongest reasoning, multilingual processing, tool use, and structured generation supported by this hardware."
    },
    [ordered]@{
        Tier = "professional"
        MinRamGb = 64
        MinGpuVramGb = 16
        RecommendedModel = "mistral-small3.2:24b"
        ContextLength = 16384
        Reason = "Professional workstation hardware detected. Mistral Small 3.2 24B is recommended for high-quality instruction following, function calling, RAG, and conversational responses."
    },
    [ordered]@{
        Tier = "workstation"
        MinRamGb = 48
        MinGpuVramGb = 12
        RecommendedModel = "ministral-3:14b"
        ContextLength = 16384
        Reason = "Workstation-class hardware detected. Ministral 3 14B provides strong reasoning, multimodal support, structured generation, and efficient local deployment."
    },
    [ordered]@{
        Tier = "high"
        MinRamGb = 32
        MinGpuVramGb = 8
        RecommendedModel = "qwen3.5:9b"
        ContextLength = 12288
        Reason = "High-end local hardware detected. Qwen 3.5 9B provides strong reasoning, RAG, tool use, and structured-output performance."
    },
    [ordered]@{
        Tier = "upper-mid"
        MinRamGb = 24
        MinGpuVramGb = 6
        RecommendedModel = "ministral-3:8b"
        ContextLength = 8192
        Reason = "Upper mid-range hardware detected. Ministral 3 8B provides efficient multilingual reasoning and strong local response quality."
    },
    [ordered]@{
        Tier = "mid"
        MinRamGb = 16
        MinGpuVramGb = 4
        RecommendedModel = "qwen3.5:4b"
        ContextLength = 8192
        Reason = "Mid-range dedicated GPU hardware detected. Qwen 3.5 4B provides reliable instruction following, structured output, and efficient local inference."
    },
    [ordered]@{
        Tier = "entry"
        MinRamGb = 8
        MinGpuVramGb = 2
        RecommendedModel = "qwen3.5:2b"
        ContextLength = 4096
        Reason = "Entry-level supported hardware detected. Qwen 3.5 2B is selected to minimize memory pressure while maintaining acceptable local performance."
    }
)

# -------------------------------------------------------
# Functions
# -------------------------------------------------------

function Test-GpuSupported {
    [CmdletBinding()]
    param(
        [AllowEmptyString()]
        [string]$Name
    )

    if ([string]::IsNullOrWhiteSpace($Name)) {
        return $false
    }

    $normalizedName = $Name.Trim().ToLowerInvariant()

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

function Get-CompatibleModelTiers {
    [CmdletBinding()]
    param(
        [double]$RamGb,
        [double]$GpuVramGb
    )

    return @(
        $ModelTiers |
            Where-Object {
                $RamGb -ge $_["MinRamGb"] -and
                $GpuVramGb -ge $_["MinGpuVramGb"]
            }
    )
}

function New-Recommendation {
    [CmdletBinding()]
    param(
        [string]$RecommendedModel,
        [object[]]$CompatibleTiers = @(),
        [string]$Tier,
        [string]$Reason,
        [int]$ContextLength = 0,
        [double]$RamGb,
        [double]$GpuVramGb,
        [string]$GpuName,
        [bool]$GpuSupported
    )

    $compatibleModels = @(
        $CompatibleTiers |
            ForEach-Object {
                $_["RecommendedModel"]
            }
    )

    $compatibleModelDetails = @(
        $CompatibleTiers |
            ForEach-Object {
                [ordered]@{
                    model = $_["RecommendedModel"]
                    tier = $_["Tier"]
                    contextLength = $_["ContextLength"]
                    minimumRamGb = $_["MinRamGb"]
                    minimumGpuVramGb = $_["MinGpuVramGb"]
                }
            }
    )

    return [ordered]@{
        recommendedModel = $RecommendedModel
        compatibleModels = $compatibleModels
        compatibleModelDetails = $compatibleModelDetails
        tier = $Tier
        reason = $Reason
        contextLength = $ContextLength
        ramGb = [Math]::Round($RamGb, 2)
        gpuVramGb = [Math]::Round($GpuVramGb, 2)
        gpuName = $GpuName.Trim()
        gpuSupported = $GpuSupported
        minimumRamGb = $MinimumRamGb
        minimumGpuVramGb = $MinimumGpuVramGb
    }
}

# -------------------------------------------------------
# Hardware validation
# -------------------------------------------------------

$GpuSupported = Test-GpuSupported -Name $GpuName

if ($RamGb -lt $MinimumRamGb) {
    $recommendation = New-Recommendation `
        -RecommendedModel "none" `
        -Tier "unsupported" `
        -Reason "The system has $RamGb GB RAM. At least $MinimumRamGb GB RAM is required for local Dr. Transition model inference." `
        -RamGb $RamGb `
        -GpuVramGb $GpuVramGb `
        -GpuName $GpuName `
        -GpuSupported $GpuSupported
}
elseif ([string]::IsNullOrWhiteSpace($GpuName)) {
    $recommendation = New-Recommendation `
        -RecommendedModel "none" `
        -Tier "unsupported" `
        -Reason "GPU information was not provided. A supported dedicated GPU with at least $MinimumGpuVramGb GB VRAM is required." `
        -RamGb $RamGb `
        -GpuVramGb $GpuVramGb `
        -GpuName $GpuName `
        -GpuSupported $false
}
elseif (-not $GpuSupported) {
    $recommendation = New-Recommendation `
        -RecommendedModel "none" `
        -Tier "unsupported" `
        -Reason "The detected GPU '$($GpuName.Trim())' is not recognized as a supported dedicated GPU. A supported NVIDIA, AMD Radeon, or Intel Arc GPU is required." `
        -RamGb $RamGb `
        -GpuVramGb $GpuVramGb `
        -GpuName $GpuName `
        -GpuSupported $GpuSupported
}
elseif ($GpuVramGb -lt $MinimumGpuVramGb) {
    $recommendation = New-Recommendation `
        -RecommendedModel "none" `
        -Tier "unsupported" `
        -Reason "The detected GPU has $GpuVramGb GB VRAM. At least $MinimumGpuVramGb GB dedicated GPU VRAM is required for local Dr. Transition model inference." `
        -RamGb $RamGb `
        -GpuVramGb $GpuVramGb `
        -GpuName $GpuName `
        -GpuSupported $GpuSupported
}
else {
    $compatibleTiers = Get-CompatibleModelTiers `
        -RamGb $RamGb `
        -GpuVramGb $GpuVramGb

    $selectedTier = $compatibleTiers | Select-Object -First 1

    if ($null -eq $selectedTier) {
        $recommendation = New-Recommendation `
            -RecommendedModel "none" `
            -Tier "unsupported" `
            -Reason "The hardware does not match a supported local model tier after strict RAM and dedicated GPU VRAM validation." `
            -RamGb $RamGb `
            -GpuVramGb $GpuVramGb `
            -GpuName $GpuName `
            -GpuSupported $GpuSupported
    }
    else {
        $recommendation = New-Recommendation `
            -RecommendedModel $selectedTier["RecommendedModel"] `
            -CompatibleTiers $compatibleTiers `
            -Tier $selectedTier["Tier"] `
            -Reason $selectedTier["Reason"] `
            -ContextLength $selectedTier["ContextLength"] `
            -RamGb $RamGb `
            -GpuVramGb $GpuVramGb `
            -GpuName $GpuName `
            -GpuSupported $GpuSupported
    }
}

# -------------------------------------------------------
# JSON output
# -------------------------------------------------------

$recommendation | ConvertTo-Json -Depth 6
