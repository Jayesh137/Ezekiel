# scripts/dispatch_workflows.ps1
#
# Drive the repo's workflows from OUTSIDE GitHub, at the cadence their crons
# only wish for.
#
# Why this exists (measured, not assumed): GitHub starts a scheduled workflow
# on a free shared runner best-effort, and drops it under load. trace.yml asks
# for every 30 minutes and ran a MEDIAN of 198 minutes apart over 73 scheduled
# runs (2026-09-01..11); watch.yml asks for every 10 minutes and its scheduled
# runs were 119-145 minutes apart on 2026-09-12. `workflow_dispatch` is NOT
# best-effort: a dispatched run starts within seconds. So one machine that can
# make one API call every few minutes turns a two-hour blind spot into a
# ten-minute one — and that blind spot is the window a migration happens in.
#
# What it does, every time it runs (Task Scheduler, every 5 minutes):
#   for each workflow: if its newest run is queued or in progress, do nothing;
#   if its newest run started less than <interval> minutes ago, do nothing;
#   otherwise dispatch it. A run that GitHub's own cron started counts, so the
#   two schedulers never double up. Nothing here reads or writes data/.
#
# Requirements: `gh` on PATH and authenticated (`gh auth status`) with the
# `repo` scope. The repo is public, so Actions minutes are unlimited.
#
# Install (once, as the logged-on user; every 5 minutes; survives reboots):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dispatch_workflows.ps1 -Install
# Remove:
#   schtasks /Delete /F /TN "Ezekiel workflow dispatcher"
# Log: %LOCALAPPDATA%\Ezekiel\dispatch.log (last 2000 lines kept).
#
# Install is a SWITCH on this script and not a documented schtasks line, because
# the schtasks defaults silently break it on a laptop. Registered with
# `schtasks /Create` on 2026-09-12 the task carried DisallowStartIfOnBatteries
# and StopIfGoingOnBatteries, both TRUE by default: the machine went to battery
# and the dispatcher stopped dead after two runs. Measured at 06:05 UTC the log
# held six lines — 04:06 and 04:39 — against the ~36 lines an hour it writes
# when it is alive, and watch.yml had last run 65 minutes earlier against a
# 10-minute cadence. A mitigation for a blind spot that is itself invisible
# when it fails is worse than none, so the settings that keep it alive belong
# in version control next to the thing they keep alive.

[CmdletBinding()]
param(
    [switch]$Install,
    # Decide and log exactly as a real run would, but dispatch nothing.
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"
$TaskName = "Ezekiel workflow dispatcher"

if ($Install) {
    $me = $MyInvocation.MyCommand.Path
    # Through wscript, not powershell.exe directly: the task runs in the user's
    # own session, so Windows gives powershell.exe a console and a terminal
    # window appeared over whatever the operator was doing every five minutes,
    # showing nothing (this script logs to a file). wscript has no console and
    # starts the child hidden from the first instant, where -WindowStyle Hidden
    # alone only hides it after it has already appeared. See
    # scripts/dispatch_hidden.vbs for what was rejected and why it waits.
    $launcher = Join-Path (Split-Path -Parent $me) "dispatch_hidden.vbs"
    if (-not (Test-Path $launcher)) {
        Write-Error "missing $launcher - the hidden launcher is part of the install"
        return
    }
    $action = New-ScheduledTaskAction -Execute "wscript.exe" `
        -Argument "//nologo `"$launcher`""
    # Repeat forever from a start time already in the past, so the first run is
    # the next 5-minute boundary rather than tomorrow.
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(-1) `
        -RepetitionInterval (New-TimeSpan -Minutes 5)
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
    #  -AllowStartIfOnBatteries / -DontStopIfGoingOnBatteries: the laptop
    #     defaults that killed it. This is the whole reason -Install exists.
    #  -StartWhenAvailable: run as soon as the machine wakes, instead of
    #     silently skipping every occurrence missed while it slept.
    #  -ExecutionTimeLimit 10m with IgnoreNew: a hung `gh` call would otherwise
    #     hold the only permitted instance for the 72-hour default, which is
    #     the same silent death by a different route.
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Force | Out-Null
    Write-Output "Registered '$TaskName': every 5 minutes, on battery too."
    Write-Output "Remove with: schtasks /Delete /F /TN `"$TaskName`""
    return
}
$Repo = "Jayesh137/Ezekiel"
$Ref = "main"

# workflow file -> minimum minutes between run starts. watch.yml is the fast
# tripwire; collect.yml is what notices his silence and drawdowns; trace.yml
# is the graph, the Circle pool and every check that hangs off them.
#
# `Group` names the GitHub concurrency group the workflow belongs to. A group
# holds only ONE pending run, so a run queued behind a long job is CANCELLED
# the moment a newer one arrives. Observed 2026-09-12: a collect dispatched at
# 04:26 queued behind trace, collect's own cron fired at 04:35, and the
# dispatched run was evicted (run 34673027518, conclusion "cancelled").
# Nothing was lost — the evicting run does the same work — but the dispatch
# was wasted, so this script now refuses to queue behind a busy group.
#
# analyze.yml and scan.yml are here because a DAILY cron is the most droppable
# kind. GitHub delivered trace.yml's 30-minute cron a median of 198 minutes
# apart; a once-a-day job that gets dropped is gone for a day, and analyze.yml
# is the ONLY thing that reads the Arbitrum bridge candidate pool, rebuilds the
# fingerprint and runs the GCR wallet tripwire. Measured 2026-09-12: the bridge
# pool had not been refreshed since 02:14 and the correlator had matched
# nothing for six hours. Their intervals are their own crons, so the two
# schedulers agree rather than doubling up.
$Schedule = @(
    @{ File = "watch.yml";   Minutes = 10;   Group = "watch" },
    @{ File = "collect.yml"; Minutes = 15;   Group = "data-commit" },
    @{ File = "trace.yml";   Minutes = 30;   Group = "data-commit" },
    @{ File = "scan.yml";    Minutes = 60;   Group = "data-commit" },
    @{ File = "analyze.yml"; Minutes = 1440; Group = "data-commit" }
)

# Every workflow sharing the `data-commit` group, including the ones this
# script never dispatches. Any of them running means a dispatch into that
# group would queue rather than start.
$DataCommitWorkflows = @("collect.yml", "trace.yml", "scan.yml", "analyze.yml",
                         "backfill.yml", "substrate-backfill.yml")

$LogDir = Join-Path $env:LOCALAPPDATA "Ezekiel"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$Log = Join-Path $LogDir "dispatch.log"

function Write-Log([string]$Message) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date).ToUniversalTime(), $Message
    Add-Content -Path $Log -Value $line -Encoding utf8
}

function Get-NewestRun([string]$Workflow) {
    # status: queued | in_progress | completed ; createdAt: ISO-8601 UTC
    $raw = & gh run list --repo $Repo --workflow $Workflow --limit 1 --json status,createdAt,event 2>$null
    if (-not $raw) { return $null }
    try {
        $rows = $raw | ConvertFrom-Json
    } catch {
        return $null
    }
    if ($rows -is [System.Array]) { if ($rows.Count -eq 0) { return $null } ; return $rows[0] }
    return $rows
}

function Test-GroupBusy([string]$Group) {
    # True when some workflow in $Group already has a run queued or running,
    # so dispatching into it would only queue (and risk being evicted).
    if ($Group -ne "data-commit") { return $false }
    foreach ($wf in $DataCommitWorkflows) {
        $newest = Get-NewestRun $wf
        if ($null -ne $newest -and $newest.status -ne "completed") {
            Write-Log "  (group data-commit busy: $wf is $($newest.status))"
            return $true
        }
    }
    return $false
}

$now = (Get-Date).ToUniversalTime()
$due = @()
$index = 0
foreach ($job in $Schedule) {
    $wf = $job.File
    $newest = Get-NewestRun $wf
    if ($null -eq $newest) {
        # Could not read the run list: dispatching blind could pile runs up
        # behind a stuck one, and the crons still exist. Say so and move on.
        Write-Log "$wf : run list unreadable (gh not authenticated?) - not dispatching"
        continue
    }
    if ($newest.status -ne "completed") {
        Write-Log "$wf : newest run is $($newest.status) - waiting"
        continue
    }
    $started = ([DateTime]::Parse($newest.createdAt)).ToUniversalTime()
    $age = ($now - $started).TotalMinutes
    if ($age -lt $job.Minutes) {
        Write-Log ("{0} : last run {1:n1} min ago (< {2}) - not yet" -f $wf, $age, $job.Minutes)
        continue
    }
    $due += [pscustomobject]@{ Index = $index; File = $wf; Group = $job.Group; Age = $age
                               Overdue = $age / $job.Minutes; Event = $newest.event }
    $index += 1
}

# At most ONE dispatch per concurrency group per run, the most overdue (age as
# a multiple of its interval) first. A group holds one running and one PENDING
# run, so dispatching several into it in the same seconds starts one, queues
# one and lets the next evict it: collect, trace and scan went out at
# 04:10:54-58 on 2026-09-17 and trace (run 35180941359) was "cancelled" four
# seconds later. The rest wait for a later run, when the group is free.
foreach ($group in @($due | ForEach-Object { $_.Group } | Select-Object -Unique)) {
    $inGroup = @($due | Where-Object { $_.Group -eq $group } | Sort-Object Overdue -Descending)
    $pick = $inGroup[0]
    foreach ($other in @($inGroup | Select-Object -Skip 1)) {
        Write-Log ("{0} : due, but {1} is more overdue in group '{2}' - next run" -f $other.File, $pick.File, $group)
    }
    # Don't queue behind a busy concurrency group: the run would sit pending
    # and be cancelled by the next arrival.
    if (Test-GroupBusy $group) {
        Write-Log ("{0} : group '{1}' is busy - skipping rather than queueing" -f $pick.File, $group)
        continue
    }
    if ($DryRun) {
        Write-Log ("{0} : WOULD dispatch (last run {1:n1} min ago, event {2}) [dry run]" -f $pick.File, $pick.Age, $pick.Event)
        continue
    }
    & gh workflow run $pick.File --repo $Repo --ref $Ref 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Log ("{0} : dispatched (last run {1:n1} min ago, event {2})" -f $pick.File, $pick.Age, $pick.Event)
    } else {
        Write-Log "$($pick.File) : dispatch FAILED (exit $LASTEXITCODE)"
    }
}

# Keep the log bounded.
try {
    $lines = Get-Content $Log -ErrorAction Stop
    if ($lines.Count -gt 2000) { $lines[-2000..-1] | Set-Content -Path $Log -Encoding utf8 }
} catch {}
