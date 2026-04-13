<#
.SYNOPSIS
    Windows Task Scheduler registration script

.DESCRIPTION
    Register 3 scheduled tasks:
      - Investment_Scheduler     : 5min interval
      - Investment_NightWorker   : Daily 3:05
      - Investment_EventScheduler: Monthly 1st 9:00

.EXAMPLE
    # Run as Administrator
    .\scripts\setup_tasks.ps1
#>

$ScriptsDir = "C:\Users\osuim\Dev\Investment_2026_0413\scripts"

# --- 1. Investment_Scheduler (5min interval) ---
schtasks /Create /F `
    /TN "Investment_Scheduler" `
    /TR "$ScriptsDir\run_scheduler.bat" `
    /SC MINUTE /MO 5 `
    /NP

if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: Investment_Scheduler registered" -ForegroundColor Green
} else {
    Write-Host "FAIL: Investment_Scheduler" -ForegroundColor Red
}

# --- 2. Investment_NightWorker (Daily 3:05) ---
schtasks /Create /F `
    /TN "Investment_NightWorker" `
    /TR "$ScriptsDir\run_nightworker.bat" `
    /SC DAILY `
    /ST 03:05 `
    /NP

if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: Investment_NightWorker registered" -ForegroundColor Green
} else {
    Write-Host "FAIL: Investment_NightWorker" -ForegroundColor Red
}

# --- 3. Investment_EventScheduler (Monthly 1st 9:00) ---
schtasks /Create /F `
    /TN "Investment_EventScheduler" `
    /TR "$ScriptsDir\run_event_scheduler.bat" `
    /SC MONTHLY /D 1 `
    /ST 09:00 `
    /NP

if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: Investment_EventScheduler registered" -ForegroundColor Green
} else {
    Write-Host "FAIL: Investment_EventScheduler" -ForegroundColor Red
}

# --- Verify ---
Write-Host ""
Write-Host "Registered tasks:" -ForegroundColor Cyan
schtasks /Query /TN "Investment_Scheduler" /FO LIST 2>$null
schtasks /Query /TN "Investment_NightWorker" /FO LIST 2>$null
schtasks /Query /TN "Investment_EventScheduler" /FO LIST 2>$null
