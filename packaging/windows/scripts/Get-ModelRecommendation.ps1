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
$MinimumReportedRamGb = 7
$CpuFallbackModel = "qwen3.5:2b"
$CpuFallbackContextLength = 4096

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
# GPU-accelerated model tiers
#
# Keep tiers ordered from highest to lowest.
# These tiers are considered only when a supported GPU is
# detected. Both RAM and GPU VRAM requirements must pass.
#
# If no supported GPU is available, the script falls back
# to qwen3.5:2b in CPU mode.
# -------------------------------------------------------

$ModelTiers = @(
    [ordered]@{
        Tier = "enthusiast"
        MinRamGb = 64
        MinGpuVramGb = 24
        RecommendedModel = "qwen3.5:27b"
        ContextLength = 32768
        Reason = "Enthusiast-class hardware detected. Qwen 3.5 27B delivers the best local reasoning, multilingual capability, structured generation, RAG, and tool use."
    },
    [ordered]@{
        Tier = "professional"
        MinRamGb = 64
        MinGpuVramGb = 16
        RecommendedModel = "mistral-small3.2:24b"
        ContextLength = 32768
        Reason = "Professional workstation hardware detected. Mistral Small 3.2 24B provides excellent instruction following, function calling, coding, and conversational performance."
    },
    [ordered]@{
        Tier = "workstation"
        MinRamGb = 48
        MinGpuVramGb = 12
        RecommendedModel = "ministral-3:14b"
        ContextLength = 24576
        Reason = "Workstation-class hardware detected. Ministral 3 14B offers strong reasoning, fast inference, structured output, and efficient local deployment."
    },
    [ordered]@{
        Tier = "high"
        MinRamGb = 32
        MinGpuVramGb = 8
        RecommendedModel = "qwen3.5:9b"
        ContextLength = 16384
        Reason = "High-end hardware detected. Qwen 3.5 9B provides an excellent balance of reasoning quality, multilingual support, coding, RAG, and tool use."
    },
    [ordered]@{
        Tier = "upper-mid"
        MinRamGb = 24
        MinGpuVramGb = 6
        RecommendedModel = "mistral-nemo"
        ContextLength = 12288
        Reason = "Upper mid-range hardware detected. Mistral NeMo delivers fast inference, long-context support, multilingual capability, and strong instruction following."
    },
    [ordered]@{
        Tier = "mid"
        MinRamGb = 16
        MinGpuVramGb = 4
        RecommendedModel = "qwen3.5:4b"
        ContextLength = 8192
        Reason = "Mid-range dedicated GPU detected. Qwen 3.5 4B provides reliable reasoning, structured output, coding assistance, and efficient local inference."
    },
    [ordered]@{
        Tier = "entry-gpu"
        MinRamGb = 7
        MinGpuVramGb = 2
        RecommendedModel = "qwen3.5:2b"
        ContextLength = 4096
        Reason = "Entry-level dedicated GPU detected. Qwen 3.5 2B offers the best balance of speed, memory efficiency, and instruction following for low-end systems."
    },
    [ordered]@{
        Tier = "cpu"
        MinRamGb = 7
        MinGpuVramGb = 0
        RecommendedModel = "qwen3.5:2b"
        ContextLength = 2048
        Reason = "No supported dedicated GPU detected. Qwen 3.5 2B is recommended for CPU-only inference."
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
        [double]$GpuVramGb,
        [bool]$GpuSupported
    )

    if (-not $GpuSupported) {
        return @()
    }

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
        [string]$InferenceMode,
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

    if (
        $RecommendedModel -ne "none" -and
        $CompatibleModels -notcontains $RecommendedModel
    ) {
        $compatibleModels = @($RecommendedModel) + $compatibleModels

        $compatibleModelDetails = @(
            [ordered]@{
                model = $RecommendedModel
                tier = $Tier
                contextLength = $ContextLength
                minimumRamGb = $MinimumRamGb
                minimumGpuVramGb = if ($InferenceMode -eq "cpu") { 0 } else { $GpuVramGb }
            }
        ) + $compatibleModelDetails
    }

    return [ordered]@{
        recommendedModel = $RecommendedModel
        compatibleModels = $compatibleModels
        compatibleModelDetails = $compatibleModelDetails
        tier = $Tier
        reason = $Reason
        inferenceMode = $InferenceMode
        contextLength = $ContextLength
        ramGb = [Math]::Round($RamGb, 2)
        gpuVramGb = [Math]::Round($GpuVramGb, 2)
        gpuName = $GpuName.Trim()
        gpuSupported = $GpuSupported
        minimumRamGb = $MinimumRamGb
        gpuRequired = $false
    }
}

# -------------------------------------------------------
# Hardware detection
# -------------------------------------------------------

$GpuSupported = Test-GpuSupported -Name $GpuName

# -------------------------------------------------------
# Model selection
# -------------------------------------------------------

if ($RamGb -lt $MinimumReportedRamGb) {
    $recommendation = New-Recommendation `
        -RecommendedModel "none" `
        -Tier "unsupported" `
        -Reason "The system reports $RamGb GB usable RAM. An 8 GB-class system must report at least $MinimumReportedRamGb GB usable RAM for local Dr. Transition model inference." `
        -InferenceMode "unsupported" `
        -RamGb $RamGb `
        -GpuVramGb $GpuVramGb `
        -GpuName $GpuName `
        -GpuSupported $GpuSupported
}
else {
    $compatibleTiers = Get-CompatibleModelTiers `
        -RamGb $RamGb `
        -GpuVramGb $GpuVramGb `
        -GpuSupported $GpuSupported

    $selectedTier = $compatibleTiers | Select-Object -First 1

    if ($null -ne $selectedTier) {
        $recommendation = New-Recommendation `
            -RecommendedModel $selectedTier["RecommendedModel"] `
            -CompatibleTiers $compatibleTiers `
            -Tier $selectedTier["Tier"] `
            -Reason $selectedTier["Reason"] `
            -InferenceMode "gpu" `
            -ContextLength $selectedTier["ContextLength"] `
            -RamGb $RamGb `
            -GpuVramGb $GpuVramGb `
            -GpuName $GpuName `
            -GpuSupported $GpuSupported
    }
    else {
        if ([string]::IsNullOrWhiteSpace($GpuName)) {
            $fallbackReason = "No GPU was detected. Qwen 3.5 2B is selected for CPU-only inference."
        }
        elseif (-not $GpuSupported) {
            $fallbackReason = "The detected GPU '$($GpuName.Trim())' is not supported for GPU acceleration. Qwen 3.5 2B is selected for CPU-only inference."
        }
        elseif ($GpuVramGb -lt 2) {
            $fallbackReason = "The supported GPU has only $GpuVramGb GB VRAM, which is insufficient for the GPU model tiers. Qwen 3.5 2B is selected for CPU-only inference."
        }
        else {
            $fallbackReason = "The available RAM and GPU VRAM do not satisfy a larger model tier. Qwen 3.5 2B is selected as the safe fallback model."
        }

        $recommendation = New-Recommendation `
            -RecommendedModel $CpuFallbackModel `
            -Tier "cpu-fallback" `
            -Reason $fallbackReason `
            -InferenceMode "cpu" `
            -ContextLength $CpuFallbackContextLength `
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