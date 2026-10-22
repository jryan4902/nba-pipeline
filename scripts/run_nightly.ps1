<#
.SYNOPSIS
    Runs the nightly NBA ingest (last 3 Eastern game dates) and logs to logs\nightly_<yyyy-MM-dd>.log.

.DESCRIPTION
    Called every morning by the nba-nightly-ingest scheduled task (see register_nightly_task.ps1),
    but can also be run by hand from any folder:

        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_nightly.ps1

    Exits with the ingest's exit code (0 = no failures), so Task Scheduler's
    "Last Run Result" shows 0x1 when a run had failures.
    Works in Windows PowerShell 5.1 and PowerShell 7.
#>

$ErrorActionPreference = 'Stop'

# Run from the repo root so load_dotenv() finds .env and relative paths resolve, wherever this is started from.
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$logDir = Join-Path $repoRoot 'logs'
$logFile = Join-Path $logDir ('nightly_{0}.log' -f (Get-Date -Format 'yyyy-MM-dd'))

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Prune before running, so old logs are cleaned up even when the ingest fails.
Get-ChildItem -Path $logDir -Filter 'nightly_*.log' -File |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force

if (-not (Test-Path $python)) {
    # Python can't write this one, so record it ourselves; otherwise an unattended failure leaves no trace.
    Add-Content -Path $logFile -Encoding UTF8 -Value ('{0} ERROR venv python not found at {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $python)
    exit 1
}

# No redirection on purpose: in PowerShell 5.1, redirecting a native command's stderr turns every
# log line into an error record. --log-file writes the full log to disk instead.
& $python -m ingestion.ingest --nightly --log-file $logFile
exit $LASTEXITCODE
