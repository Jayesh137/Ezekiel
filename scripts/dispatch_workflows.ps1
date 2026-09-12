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
#   schtasks /Create /F /SC MINUTE /MO 5 /TN "Ezekiel workflow dispatcher" `
#     /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PWD\scripts\dispatch_workflows.ps1`""
# Remove:
#   schtasks /Delete /F /TN "Ezekiel workflow dispatcher"
# Log: %LOCALAPPDATA%\Ezekiel\dispatch.log (last 2000 lines kept).

$ErrorActionPreference = "Continue"
$Repo = "Jayesh137/Ezekiel"
$Ref = "main"

# workflow file -> minimum minutes between run starts. watch.yml is the fast
# tripwire; collect.yml is what notices his silence and drawdowns; trace.yml
# is the graph, the Circle pool and every check that hangs off them.
$Schedule = @(
    @{ File = "watch.yml";   Minutes = 10 },
    @{ File = "collect.yml"; Minutes = 15 },
    @{ File = "trace.yml";   Minutes = 30 }
)

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

$now = (Get-Date).ToUniversalTime()
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
    & gh workflow run $wf --repo $Repo --ref $Ref 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Log ("{0} : dispatched (last run {1:n1} min ago, event {2})" -f $wf, $age, $newest.event)
    } else {
        Write-Log "$wf : dispatch FAILED (exit $LASTEXITCODE)"
    }
}

# Keep the log bounded.
try {
    $lines = Get-Content $Log -ErrorAction Stop
    if ($lines.Count -gt 2000) { $lines[-2000..-1] | Set-Content -Path $Log -Encoding utf8 }
} catch {}
