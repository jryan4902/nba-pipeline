<#
.SYNOPSIS
    Registers (or replaces) the nba-nightly-ingest scheduled task that runs scripts\run_nightly.ps1 daily.

.DESCRIPTION
    Run once from an admin PowerShell (5.1 or 7). Re-running replaces the existing task.

.PARAMETER At
    Time of day to run (default 6:00 AM). Eastern-date "yesterday" is computed by the ingest itself,
    so this only needs to be after the last West Coast game is final.

.PARAMETER Unregister
    Remove the task instead of registering it.

.EXAMPLE
    .\scripts\register_nightly_task.ps1
.EXAMPLE
    .\scripts\register_nightly_task.ps1 -At 7:30AM
.EXAMPLE
    .\scripts\register_nightly_task.ps1 -Unregister
#>
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [DateTime]$At = '6:00AM',
    [switch]$Unregister
)

$ErrorActionPreference = 'Stop'
$taskName = 'nba-nightly-ingest'

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Removed task $taskName"
    } else {
        Write-Host "Task $taskName is not registered; nothing to remove"
    }
    exit 0
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runScript = Join-Path $repoRoot 'scripts\run_nightly.ps1'

# Always Windows PowerShell 5.1 (present on every Windows install); Bypass so execution policy can't block the run.
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`"" `
    -WorkingDirectory $repoRoot

$trigger = New-ScheduledTaskTrigger -Daily -At $At

# StartWhenAvailable: if the PC was off or asleep at the trigger time, run as soon as it can.
# WakeToRun only works if "Allow wake timers" is enabled in the power plan.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 15) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

# S4U: runs whether or not you're logged on, without storing your password. It gets no Windows
# network credentials (file shares etc.), which this job doesn't need: S3 auth uses the keys in .env.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName $taskName `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description 'Nightly NBA ingest: python -m ingestion.ingest --nightly (nba-pipeline repo)' `
    -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $taskName
# ${taskName} braces: "$taskName:" would be parsed as a drive-qualified variable.
Write-Host "Registered ${taskName}: daily at $($At.ToString('h:mm tt')) as $env:USERDOMAIN\$env:USERNAME"
Write-Host "Runs:     powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$runScript`""
Write-Host "Next run: $($info.NextRunTime)"
