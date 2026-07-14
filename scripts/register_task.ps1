<#
    register_task.ps1 — register the daily signaldesk collect+digest job.

    Creates a Windows Scheduled Task "signaldesk-daily-collect" that runs
    `python -m engine run` in the aggregator directory every day at 08:30 IST.
    -StartWhenAvailable makes a missed start catch up on the next wake — which
    pairs with the engine's watermark model (a missed day self-heals: the next
    run fetches the whole gap since the last success).

    This script only REGISTERS the task. Run it yourself when you're ready:
        pwsh -File scripts\register_task.ps1
    Remove it with:
        Unregister-ScheduledTask -TaskName "signaldesk-daily-collect" -Confirm:$false
#>

$ErrorActionPreference = "Stop"

$TaskName = "signaldesk-daily-collect"
$WorkDir  = Split-Path -Parent $PSScriptRoot   # aggregator repo root
$Python   = (Get-Command python).Source

Write-Host "Task:       $TaskName"
Write-Host "Python:     $Python"
Write-Host "WorkingDir: $WorkDir"

# The machine clock may be any timezone; 08:30 IST is the intent. If this PC
# runs on IST the literal 08:30 is correct. On a non-IST clock, adjust -At.
$action = New-ScheduledTaskAction -Execute $Python -Argument "-m engine run" -WorkingDirectory $WorkDir
$trigger = New-ScheduledTaskTrigger -Daily -At 8:30AM

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "signaldesk engine: daily AI-signal collect + digest (08:30 IST, catch-up on miss)." `
    -Force

Write-Host "Registered. Verify with: Get-ScheduledTask -TaskName $TaskName"
Write-Host "Run once now with: Start-ScheduledTask -TaskName $TaskName"
