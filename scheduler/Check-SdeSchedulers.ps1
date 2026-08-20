$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$Expected = @(
    [pscustomobject]@{ Name = "SDE Swing Market Outlook"; Launcher = "scheduler\SCHEDULE_MARKET_OUTLOOK.bat" },
    [pscustomobject]@{ Name = "SDE Swing Post Market"; Launcher = "scheduler\SCHEDULE_POST_MARKET.bat" },
    [pscustomobject]@{ Name = "SDE Swing Final Watchlist"; Launcher = "scheduler\SCHEDULE_FINAL_WATCHLIST.bat" },
    [pscustomobject]@{ Name = "SDE Swing IDX Disclosure Watcher"; Launcher = "scheduler\SCHEDULE_IDX_DISCLOSURE.bat" }
)

$Rows = @()
foreach ($Spec in $Expected) {
    $Task = Get-ScheduledTask -TaskName $Spec.Name -ErrorAction SilentlyContinue
    if (-not $Task) {
        $Rows += [pscustomobject]@{
            Task = $Spec.Name
            State = "MISSING"
            LastRun = ""
            NextRun = ""
            LastResult = ""
            Restart = ""
            Wake = ""
            StartWhenAvailable = ""
            ActionOK = $false
        }
        continue
    }

    $Info = Get-ScheduledTaskInfo -TaskName $Spec.Name
    $Launcher = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Spec.Launcher))
    $ActionOK = $false
    foreach ($Action in $Task.Actions) {
        if ([string]$Action.Arguments -like "*$Launcher*") {
            $ActionOK = $true
            break
        }
    }

    $ResultHex = if ($null -eq $Info.LastTaskResult) {
        ""
    } else {
        "0x{0:X8}" -f ([uint32]$Info.LastTaskResult)
    }

    $Rows += [pscustomobject]@{
        Task = $Spec.Name
        State = [string]$Task.State
        LastRun = if ($Info.LastRunTime.Year -gt 1900) { $Info.LastRunTime } else { "" }
        NextRun = if ($Info.NextRunTime.Year -gt 1900) { $Info.NextRunTime } else { "" }
        LastResult = $ResultHex
        Restart = [string]$Task.Settings.RestartCount
        Wake = [bool]$Task.Settings.WakeToRun
        StartWhenAvailable = [bool]$Task.Settings.StartWhenAvailable
        ActionOK = $ActionOK
    }
}

$Rows | Format-Table -AutoSize

$Problems = @($Rows | Where-Object {
    $_.State -eq "MISSING" -or
    -not $_.ActionOK -or
    $_.Wake -eq $false -or
    $_.StartWhenAvailable -eq $false
})

Write-Host ""
if ($Problems.Count -gt 0) {
    Write-Host "[WARNING] Scheduler configuration problems detected:" -ForegroundColor Yellow
    $Problems | ForEach-Object { Write-Host " - $($_.Task)" }
    Write-Host "Run maintenance\INSTALL_SCHEDULERS.bat to repair the managed tasks."
} else {
    Write-Host "[OK] Managed scheduler settings look consistent." -ForegroundColor Green
}

Write-Host ""
Write-Host "Notes:"
Write-Host "- LastResult 0x00000000 means the last Task Scheduler process ended successfully."
Write-Host "- A laptop that is shut down cannot run scheduled jobs."
Write-Host "- WakeToRun can wake from sleep only when Windows/power policy allows wake timers."
Write-Host "- The IDX watcher uses headed Playwright and therefore requires an active signed-in Windows session."

$StateRoot = Join-Path $ProjectRoot "data\state\scheduler\scheduled_runs"
if (Test-Path $StateRoot) {
    Write-Host ""
    Write-Host "Latest SDE scheduler wrapper state:"
    Get-ChildItem $StateRoot -Filter "*_latest.json" -ErrorAction SilentlyContinue |
        Sort-Object Name |
        ForEach-Object {
            try {
                $Payload = Get-Content -Raw -Encoding UTF8 $_.FullName | ConvertFrom-Json
                Write-Host (" - {0}: {1}, attempts={2}, late={3}m, child_exit={4}" -f `
                    $Payload.job,
                    $Payload.status,
                    $Payload.attempt_count,
                    $Payload.start_lateness_minutes,
                    $Payload.child_exit_code)
            } catch {
                Write-Host " - $($_.Name): unreadable"
            }
        }
}
