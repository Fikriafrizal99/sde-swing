param(
    [switch]$StartIdxNow,
    [switch]$KeepLegacyDuplicates
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ConfigPath = Join-Path $ProjectRoot "config\scheduler.json"

if (-not (Test-Path $ConfigPath)) {
    throw "Scheduler config not found: $ConfigPath"
}

$Config = Get-Content -Raw -Encoding UTF8 $ConfigPath | ConvertFrom-Json
$Runtime = $Config.scheduler_runtime
$Windows = $Runtime.windows_task

function Convert-IsoDuration {
    param([string]$Value, [string]$Fallback)
    $Text = if ([string]::IsNullOrWhiteSpace($Value)) { $Fallback } else { $Value }
    return [System.Xml.XmlConvert]::ToTimeSpan($Text)
}

function Resolve-JobLimit {
    param([string]$JobName, [string]$Fallback)
    $Value = $null
    if ($Runtime -and $Runtime.jobs -and $Runtime.jobs.$JobName) {
        $Value = [string]$Runtime.jobs.$JobName.execution_time_limit
    }
    return Convert-IsoDuration $Value $Fallback
}

$RestartCount = 3
if ($Windows -and $null -ne $Windows.restart_count) {
    $RestartCount = [int]$Windows.restart_count
}
$RestartInterval = Convert-IsoDuration ([string]$Windows.restart_interval) "PT5M"

$UserId = if ($env:USERDOMAIN) {
    "$env:USERDOMAIN\$env:USERNAME"
} else {
    $env:USERNAME
}

$Principal = New-ScheduledTaskPrincipal `
    -UserId $UserId `
    -LogonType Interactive `
    -RunLevel Limited

$Specs = @(
    [pscustomobject]@{
        Name = "SDE Swing Market Outlook"
        Kind = "Daily"
        Time = [string]$Config.market_outlook.time
        Launcher = "scheduler\SCHEDULE_MARKET_OUTLOOK.bat"
        Limit = Resolve-JobLimit "market_outlook" "PT2H"
        Description = "SDE Swing Market Outlook - reliable scheduler wrapper"
    },
    [pscustomobject]@{
        Name = "SDE Swing Post Market"
        Kind = "Daily"
        Time = [string]$Config.post_market.time
        Launcher = "scheduler\SCHEDULE_POST_MARKET.bat"
        Limit = Resolve-JobLimit "post_market" "PT2H30M"
        Description = "SDE Swing Post Market - market-first reliable scheduler wrapper"
    },
    [pscustomobject]@{
        Name = "SDE Swing Final Watchlist"
        Kind = "Daily"
        Time = [string]$Config.final_watchlist.start_time
        Launcher = "scheduler\SCHEDULE_FINAL_WATCHLIST.bat"
        Limit = Resolve-JobLimit "final_watchlist" "PT3H"
        Description = "SDE Swing Final Watchlist - lifecycle-aware reliable scheduler wrapper"
    },
    [pscustomobject]@{
        Name = "SDE Swing IDX Disclosure Watcher"
        Kind = "Logon"
        Time = ""
        Launcher = "scheduler\SCHEDULE_IDX_DISCLOSURE.bat"
        Limit = Resolve-JobLimit "idx_disclosure" "PT0S"
        Description = "SDE Swing IDX disclosure watcher - persistent Playwright service"
    }
)

function New-SdeSettings {
    param([TimeSpan]$ExecutionLimit)
    return New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -WakeToRun `
        -MultipleInstances IgnoreNew `
        -RestartCount $RestartCount `
        -RestartInterval $RestartInterval `
        -ExecutionTimeLimit $ExecutionLimit
}

function Get-LauncherFullPath {
    param([string]$RelativePath)
    return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $RelativePath))
}

foreach ($Spec in $Specs) {
    $Launcher = Get-LauncherFullPath $Spec.Launcher
    if (-not (Test-Path $Launcher)) {
        throw "Scheduler launcher not found: $Launcher"
    }

    $Action = New-ScheduledTaskAction `
        -Execute "cmd.exe" `
        -Argument "/d /c `"`"$Launcher`"`"" `
        -WorkingDirectory $ProjectRoot

    if ($Spec.Kind -eq "Daily") {
        $At = [datetime]::Today.Add([TimeSpan]::ParseExact($Spec.Time, "hh\:mm", $null))
        $Trigger = New-ScheduledTaskTrigger -Daily -At $At
    } else {
        $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
    }

    $Settings = New-SdeSettings $Spec.Limit
    $Task = New-ScheduledTask `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Description $Spec.Description

    Register-ScheduledTask `
        -TaskName $Spec.Name `
        -InputObject $Task `
        -Force | Out-Null

    Write-Host "[OK] Registered: $($Spec.Name)"
}

if (-not $KeepLegacyDuplicates) {
    $ExpectedNames = @($Specs | ForEach-Object { $_.Name })
    $LauncherPaths = @($Specs | ForEach-Object { Get-LauncherFullPath $_.Launcher })

    foreach ($Task in Get-ScheduledTask) {
        if ($ExpectedNames -contains $Task.TaskName) {
            continue
        }

        $MatchesThisProject = $false
        foreach ($Action in $Task.Actions) {
            $Arguments = [string]$Action.Arguments
            foreach ($LauncherPath in $LauncherPaths) {
                if ($Arguments -like "*$LauncherPath*") {
                    $MatchesThisProject = $true
                    break
                }
            }
            if ($MatchesThisProject) { break }
        }

        if ($MatchesThisProject) {
            Disable-ScheduledTask -TaskName $Task.TaskName -TaskPath $Task.TaskPath | Out-Null
            Write-Host "[DISABLED LEGACY DUPLICATE] $($Task.TaskPath)$($Task.TaskName)"
        }
    }
}

Write-Host ""
Write-Host "Scheduler registration complete."
Write-Host "Account mode: InteractiveToken ($UserId)"
Write-Host "Tasks continue while Windows is locked, but not after sign-out/shutdown."
Write-Host "WakeToRun and StartWhenAvailable are enabled."
Write-Host ""

if ($StartIdxNow) {
    $IdxName = "SDE Swing IDX Disclosure Watcher"
    Start-ScheduledTask -TaskName $IdxName
    Write-Host "[STARTED] $IdxName"
} else {
    Write-Host "IDX watcher will start automatically at the next Windows logon."
    Write-Host "Use -StartIdxNow only after stopping any manually running IDX watcher."
}
