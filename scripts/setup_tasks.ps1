#Requires -RunAsAdministrator
<#
.SYNOPSIS
    投資パイプラインの Windows タスクスケジューラー登録スクリプト

.DESCRIPTION
    3つのタスクを一括登録する:
      - Investment_Scheduler     : 5分間隔の定期監視
      - Investment_NightWorker   : 毎日 3:05 の夜間メンテナンス
      - Investment_EventScheduler: 毎月1日 9:00 の経済イベント取得

.EXAMPLE
    # 管理者権限の PowerShell で実行
    .\scripts\setup_tasks.ps1
#>

$ProjectRoot = "C:\Users\osuim\Dev\Investment_2026_0413"
$ScriptsDir = "$ProjectRoot\scripts"

# --- 共通設定 ---
$CommonSettings = @{
    StartWhenAvailable = $true
    MultipleInstances  = "IgnoreNew"
}

# --- 1. Investment_Scheduler（5分間隔） ---
$schedulerAction = New-ScheduledTaskAction `
    -Execute "$ScriptsDir\run_scheduler.bat" `
    -WorkingDirectory $ProjectRoot

$schedulerTrigger = New-ScheduledTaskTrigger `
    -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

$schedulerSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "Investment_Scheduler" `
    -Action $schedulerAction `
    -Trigger $schedulerTrigger `
    -Settings $schedulerSettings `
    -Description "投資パイプライン定期監視（5分間隔）" `
    -Force

Write-Host "[OK] Investment_Scheduler を登録しました" -ForegroundColor Green

# --- 2. Investment_NightWorker（毎日 3:05） ---
$nightAction = New-ScheduledTaskAction `
    -Execute "$ScriptsDir\run_nightworker.bat" `
    -WorkingDirectory $ProjectRoot

$nightTrigger = New-ScheduledTaskTrigger -Daily -At "03:05"

$nightSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "Investment_NightWorker" `
    -Action $nightAction `
    -Trigger $nightTrigger `
    -Settings $nightSettings `
    -Description "投資パイプライン NightWorker（毎日 3:05 JST）" `
    -Force

Write-Host "[OK] Investment_NightWorker を登録しました" -ForegroundColor Green

# --- 3. Investment_EventScheduler（毎月1日 9:00） ---
$eventAction = New-ScheduledTaskAction `
    -Execute "$ScriptsDir\run_event_scheduler.bat" `
    -WorkingDirectory $ProjectRoot

$eventTrigger = New-ScheduledTaskTrigger -Monthly -DaysOfMonth 1 -At "09:00"

$eventSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "Investment_EventScheduler" `
    -Action $eventAction `
    -Trigger $eventTrigger `
    -Settings $eventSettings `
    -Description "投資パイプライン EventScheduler（毎月1日 9:00 JST）" `
    -Force

Write-Host "[OK] Investment_EventScheduler を登録しました" -ForegroundColor Green

# --- 確認表示 ---
Write-Host ""
Write-Host "登録済みタスク一覧:" -ForegroundColor Cyan
Get-ScheduledTask -TaskName "Investment_*" | Format-Table TaskName, State, Description -AutoSize
