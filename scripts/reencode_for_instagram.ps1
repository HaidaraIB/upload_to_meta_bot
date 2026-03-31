# Manual re-export for Instagram when Meta returns ProcessingFailedError after upload.
# Matches meta/video_normalizer.py re-encode path: H.264 (high/4.1, yuv420p) + AAC 128k + faststart.
# Usage:
#   .\scripts\reencode_for_instagram.ps1 -InputPath .\in.mp4 -OutputPath .\out_ig.mp4
# Remux only (no re-encode), if copy + faststart is enough:
#   .\scripts\reencode_for_instagram.ps1 -InputPath .\in.mp4 -OutputPath .\out.mp4 -RemuxFaststartOnly

param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [string]$FfmpegBin,

    [switch]$RemuxFaststartOnly
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not $FfmpegBin) {
    $FfmpegBin = $env:FFMPEG_BIN
}
if (-not $FfmpegBin) {
    $FfmpegBin = "ffmpeg"
}

$inFull = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($InputPath)
$outFull = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)

if (-not (Test-Path -LiteralPath $inFull)) {
    Write-Error "Input not found: $inFull"
}

if ($RemuxFaststartOnly) {
    Write-Host "Remux: copy streams + movflags +faststart -> $outFull"
    & $FfmpegBin -y -i $inFull -c copy -movflags +faststart $outFull
} else {
    Write-Host "Re-encode: libx264 + aac +faststart -> $outFull"
    & $FfmpegBin -y -i $inFull `
        -c:v libx264 -preset veryfast -pix_fmt yuv420p -profile:v high -level 4.1 `
        -c:a aac -b:a 128k -movflags +faststart $outFull
}

if ($LASTEXITCODE -ne 0) {
    Write-Error "ffmpeg failed with exit code $LASTEXITCODE"
}
Write-Host "Done." -ForegroundColor Green
