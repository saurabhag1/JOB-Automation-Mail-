param(
    [switch]$Send,
    [switch]$DryRun,
    [switch]$DryRunSend,
    [switch]$Force,
    [switch]$Fast,
    [string]$Skills = "",
    [string]$Locations = "",
    [int]$TargetCount = 50,
    [int]$MaxAgeDays = 7
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

python -m pip install -r requirements.txt

$collectorArgs = @(
    ".\daily_job_lead_collector.py",
    "--target-count", "$TargetCount",
    "--max-age-days", "$MaxAgeDays"
)

if ($Skills.Trim()) {
    $collectorArgs += @("--skills", $Skills)
}

if ($Locations.Trim()) {
    $collectorArgs += @("--locations", $Locations)
}

if ($Send) {
    $collectorArgs += "--send"
}

if ($DryRun) {
    $collectorArgs += "--dry-run"
}

if ($DryRunSend) {
    $collectorArgs += "--dry-run-send"
}

if ($Fast) {
    $collectorArgs += "--skip-live-search"
}

if ($Force) {
    $collectorArgs += "--force"
}

python @collectorArgs
