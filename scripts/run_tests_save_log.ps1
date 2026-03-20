# Save pytest console output + JUnit XML under test-results/ (project root).
# Usage: .\scripts\run_tests_save_log.ps1
# Optional: .\scripts\run_tests_save_log.ps1 -Path tests/unit

param(
    [string]$Path = "tests"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$outDir = Join-Path $root "test-results"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$pytest = Join-Path $root ".venv\Scripts\pytest.exe"
if (-not (Test-Path $pytest)) {
    $pytest = "pytest"
}

$junit = Join-Path $outDir "junit.xml"
$log = Join-Path $outDir "last-run.txt"

Write-Host "Writing: $log"
Write-Host "Writing: $junit"
& $pytest $Path -v --tb=short --junitxml=$junit 2>&1 | Tee-Object -FilePath $log
exit $LASTEXITCODE
