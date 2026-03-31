# Verify ffmpeg availability and effective Instagram video-related settings.
# Reads only FFMPEG_BIN / IG_VIDEO_* from .env in project root (no other secrets printed).
# Usage: .\scripts\verify_meta_video_env.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

function Get-DotenvValue {
    param(
        [string]$Path,
        [string]$Key
    )
    if (-not (Test-Path $Path)) { return $null }
    foreach ($line in Get-Content -Path $Path -Encoding UTF8) {
        $t = $line.Trim()
        if ($t -eq "" -or $t.StartsWith("#")) { continue }
        if ($t -match "^$([regex]::Escape($Key))\s*=\s*(.*)$") {
            $v = $Matches[1].Trim().Trim('"').Trim("'")
            return $v
        }
    }
    return $null
}

$envFile = Join-Path $root ".env"
$ffmpegFromEnv = Get-DotenvValue -Path $envFile -Key "FFMPEG_BIN"
$igFix = Get-DotenvValue -Path $envFile -Key "IG_VIDEO_AUTOFIX_ENABLED"
$igRe = Get-DotenvValue -Path $envFile -Key "IG_VIDEO_AUTOFIX_REENCODE_FALLBACK"
$igInc = Get-DotenvValue -Path $envFile -Key "IG_VIDEO_REENCODE_IF_INCOMPATIBLE"
$igForce = Get-DotenvValue -Path $envFile -Key "IG_VIDEO_FORCE_REENCODE"

if (-not $ffmpegFromEnv) { $ffmpegFromEnv = "ffmpeg" }
if (-not $igFix) { $igFix = "true (default if unset)" }
if (-not $igRe) { $igRe = "true (default if unset)" }
if (-not $igInc) { $igInc = "true (default if unset)" }
if (-not $igForce) { $igForce = "false (default if unset)" }

Write-Host "=== Meta / Instagram video environment ===" -ForegroundColor Cyan
Write-Host "Project root: $root"
Write-Host ".env present: $(Test-Path $envFile)"
Write-Host "FFMPEG_BIN (from .env or default): $ffmpegFromEnv"
Write-Host "IG_VIDEO_AUTOFIX_ENABLED (effective from .env): $igFix"
Write-Host "IG_VIDEO_AUTOFIX_REENCODE_FALLBACK (effective from .env): $igRe"
Write-Host "IG_VIDEO_REENCODE_IF_INCOMPATIBLE (effective from .env): $igInc"
Write-Host "IG_VIDEO_FORCE_REENCODE (effective from .env): $igForce"
Write-Host ""

$bin = $ffmpegFromEnv
$which = Get-Command $bin -ErrorAction SilentlyContinue
if (-not $which) {
    Write-Host "FAIL: ffmpeg not found for FFMPEG_BIN='$bin'. Install ffmpeg or set FFMPEG_BIN in .env." -ForegroundColor Red
    exit 1
}

Write-Host "ffmpeg resolve: $($which.Source)" -ForegroundColor Green
& $bin -version | Select-Object -First 2
Write-Host ""

# ffprobe next to ffmpeg (same convention as meta/video_normalizer.py)
$ffprobeBin = "ffprobe"
try {
    $fb = [System.IO.Path]::GetFileName($which.Source)
    $dir = [System.IO.Path]::GetDirectoryName($which.Source)
    if ($fb -ieq "ffmpeg.exe") {
        $ffprobeBin = Join-Path $dir "ffprobe.exe"
    } elseif ($fb -ieq "ffmpeg") {
        $ffprobeBin = Join-Path $dir "ffprobe"
    }
} catch {}

if (Test-Path -LiteralPath $ffprobeBin) {
    Write-Host "ffprobe resolve: $ffprobeBin" -ForegroundColor Green
    & $ffprobeBin -version | Select-Object -First 1
} else {
    $fbCmd = Get-Command "ffprobe" -ErrorAction SilentlyContinue
    if ($fbCmd) {
        Write-Host "ffprobe resolve: $($fbCmd.Source)" -ForegroundColor Green
        & "ffprobe" -version | Select-Object -First 1
    } else {
        Write-Host "WARN: ffprobe not found next to ffmpeg and not on PATH. Codec-based auto re-encode may be skipped." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "OK: ffmpeg is callable." -ForegroundColor Green
exit 0
