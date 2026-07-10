# Manual re-export for Instagram when Meta returns ProcessingFailedError after upload.
# Matches meta/video_normalizer.py re-encode path: H.264 (high/4.1, yuv420p, CRF 18) + AAC 192k + faststart.
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

    [string]$Crf,

    [string]$Preset,

    [string]$AudioBitrate,

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
if (-not $Crf) {
    $Crf = $env:IG_VIDEO_CRF
}
if (-not $Crf) {
    $Crf = "18"
}
if (-not $Preset) {
    $Preset = $env:IG_VIDEO_ENCODE_PRESET
}
if (-not $Preset) {
    $Preset = "medium"
}
if (-not $AudioBitrate) {
    $AudioBitrate = $env:IG_VIDEO_AUDIO_BITRATE
}
if (-not $AudioBitrate) {
    $AudioBitrate = "192k"
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
    Write-Host "Re-encode: libx264 crf=$Crf preset=$Preset + aac $AudioBitrate +faststart -> $outFull"
    & $FfmpegBin -y -i $inFull `
        -c:v libx264 -crf $Crf -preset $Preset -pix_fmt yuv420p -profile:v high -level 4.1 `
        -c:a aac -b:a $AudioBitrate -movflags +faststart $outFull
}

if ($LASTEXITCODE -ne 0) {
    Write-Error "ffmpeg failed with exit code $LASTEXITCODE"
}
Write-Host "Done." -ForegroundColor Green
