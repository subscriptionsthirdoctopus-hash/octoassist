# install-windows.ps1 — install the OctoAssist agent as a Windows scheduled task.
#
# Usage (Run PowerShell as Administrator):
#   .\install-windows.ps1 -ServerUrl https://octoassist.thirdoctopus.com -EnrolmentKey XXXXXXXX
#
# Requires: Python 3.10+ in PATH (winget install Python.Python.3.12 if needed).
# This script does NOT depend on NSSM. Uses Task Scheduler — runs at boot
# and every 6 hours, hidden window. AV-friendly because it's just stock
# python.exe running our .py file.

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ServerUrl,
    [Parameter(Mandatory=$true)][string]$EnrolmentKey,
    [int]$IntervalHours = 6
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script as Administrator."
    }
}
Assert-Admin

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $python) {
    throw "python.exe not in PATH. Install Python 3.10+ first: winget install Python.Python.3.12"
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$agentSrc  = Join-Path $scriptDir "octoassist_agent.py"
if (-not (Test-Path $agentSrc)) {
    throw "octoassist_agent.py not found next to install-windows.ps1"
}

$installDir = "C:\Program Files\OctoAssist Agent"
$dataDir    = "$env:ProgramData\OctoAssist"

Write-Host "==> Installing agent script to $installDir"
New-Item -ItemType Directory -Force $installDir | Out-Null
Copy-Item $agentSrc (Join-Path $installDir "octoassist_agent.py") -Force

Write-Host "==> Bootstrapping config"
$bootstrap = & $python.Path (Join-Path $installDir "octoassist_agent.py") `
    --bootstrap `
    --server-url=$ServerUrl `
    --enrolment-key=$EnrolmentKey `
    --interval-hours=$IntervalHours
$bootstrap

Write-Host "==> Registering Scheduled Task 'OctoAssist Agent'"
$taskName = "OctoAssist Agent"
$action  = New-ScheduledTaskAction -Execute $python.Path `
    -Argument "`"$installDir\octoassist_agent.py`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)

# Remove any existing task with this name first (idempotent)
Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description "OctoAssist asset agent (Python)" | Out-Null

Write-Host "==> Starting task once now to verify"
Start-ScheduledTask -TaskName $taskName

Write-Host ""
Write-Host "=============================================================="
Write-Host "  OctoAssist agent installed."
Write-Host ""
Write-Host "  Config:     $dataDir\agent.json"
Write-Host "  Logs:       $dataDir\logs\agent.log"
Write-Host "  Task name:  $taskName"
Write-Host "  Run once:   python `"$installDir\octoassist_agent.py`" --once"
Write-Host "  Stop task:  Stop-ScheduledTask -TaskName '$taskName'"
Write-Host "  Uninstall:  Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
Write-Host ""
Write-Host "  Open the OctoAssist admin UI — this host will appear in"
Write-Host "  /assets within a minute of the first check-in."
Write-Host "=============================================================="
