param(
    [string]$SourcePng,
    [string]$DestinationIco
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $SourcePng)) {
    throw "Source PNG not found: $SourcePng"
}

$destinationDir = Split-Path -Parent $DestinationIco
New-Item -ItemType Directory -Force -Path $destinationDir | Out-Null

Add-Type -AssemblyName System.Drawing

$sourceBitmap = [System.Drawing.Bitmap]::FromFile($SourcePng)
try {
    $resizedBitmap = New-Object System.Drawing.Bitmap 256, 256
    $graphics = [System.Drawing.Graphics]::FromImage($resizedBitmap)
    try {
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.DrawImage($sourceBitmap, 0, 0, 256, 256)

        $icon = [System.Drawing.Icon]::FromHandle($resizedBitmap.GetHicon())
        $stream = [System.IO.File]::Create($DestinationIco)
        try {
            $icon.Save($stream)
        }
        finally {
            $stream.Dispose()
            $icon.Dispose()
        }
    }
    finally {
        $graphics.Dispose()
        $resizedBitmap.Dispose()
    }
}
finally {
    $sourceBitmap.Dispose()
}

Write-Host "Created $DestinationIco"
