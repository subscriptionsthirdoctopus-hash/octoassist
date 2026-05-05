# Uninstall-OctoAssist-Server.ps1
# Removes the OctoAssist server installation. Drops the IIS site and the
# Windows Service. Does NOT drop the Postgres database — that is destructive
# and intentional. To drop the DB, do it manually with psql.
#
# Run as Administrator.

[CmdletBinding()]
param(
    [string]$InstallDir   = "C:\Program Files\Third Octopus\OctoAssist Server",
    [string]$DataDir      = "C:\ProgramData\OctoAssist\Server",
    [string]$IisSiteName  = "OctoAssist",
    [string]$ServiceName  = "OctoAssistServer",
    [switch]$KeepData
)

$ErrorActionPreference = "Continue"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script as Administrator."
    }
}

Assert-Admin

Write-Host "==> Stopping and removing service '$ServiceName'"
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.Status -eq "Running") { Stop-Service -Name $ServiceName -Force }
    $nssm = Get-Command nssm.exe -ErrorAction SilentlyContinue
    if (-not $nssm) { $nssm = "C:\Program Files\nssm\nssm.exe" } else { $nssm = $nssm.Source }
    if (Test-Path $nssm) {
        & $nssm remove $ServiceName confirm 2>&1 | Out-Null
    } else {
        & sc.exe delete $ServiceName | Out-Null
    }
}

Write-Host "==> Removing IIS site '$IisSiteName'"
Import-Module WebAdministration -ErrorAction SilentlyContinue
if (Test-Path "IIS:\Sites\$IisSiteName") { Remove-Website -Name $IisSiteName }

Write-Host "==> Removing firewall rule"
Get-NetFirewallRule -DisplayName "OctoAssist HTTP" -ErrorAction SilentlyContinue | Remove-NetFirewallRule

Write-Host "==> Removing app code at $InstallDir"
if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }

if (-not $KeepData) {
    Write-Host "==> Removing data directory $DataDir (use -KeepData to preserve .env and logs)"
    if (Test-Path $DataDir) { Remove-Item -Recurse -Force $DataDir }
} else {
    Write-Host "==> Keeping data directory $DataDir (-KeepData)"
}

Write-Host ""
Write-Host "Uninstall complete. The Postgres DB and role were NOT dropped." -ForegroundColor Cyan
Write-Host "If you want to drop them:"
Write-Host "  psql -U postgres -c `"DROP DATABASE octoassist;`""
Write-Host "  psql -U postgres -c `"DROP ROLE octoassist;`""
