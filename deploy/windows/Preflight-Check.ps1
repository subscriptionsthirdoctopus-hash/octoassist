# Preflight-Check.ps1
# Verifies that the Windows Server has the prerequisites the OctoAssist server
# install needs. Prints exactly what's missing and the command to install it.
# Does NOT modify the system — read-only check.
#
# Run as Administrator.

$ErrorActionPreference = "Stop"

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host "ERROR: must run as Administrator." -ForegroundColor Red
    exit 2
}

$missing = @()

function Check-Item {
    param([string]$Name, [scriptblock]$Test, [string]$Fix)
    Write-Host -NoNewline ("  {0,-32}" -f $Name)
    try {
        $ok = & $Test
        if ($ok) {
            Write-Host "OK" -ForegroundColor Green
        } else {
            Write-Host "MISSING" -ForegroundColor Yellow
            $script:missing += [pscustomobject]@{ Name=$Name; Fix=$Fix }
        }
    } catch {
        Write-Host "MISSING" -ForegroundColor Yellow
        $script:missing += [pscustomobject]@{ Name=$Name; Fix=$Fix }
    }
}

Write-Host ""
Write-Host "OctoAssist Server — Pre-flight check" -ForegroundColor Cyan
Write-Host "------------------------------------"

# 1. Windows Server edition
Check-Item "Windows Server (2019/2022/2025)" {
    $caption = (Get-CimInstance Win32_OperatingSystem).Caption
    return ($caption -match "Server")
} "This script targets Windows Server. Client editions (Win10/11) may work but are not supported."

# 2. PowerShell 5.1+
Check-Item "PowerShell 5.1+" {
    return ($PSVersionTable.PSVersion.Major -ge 5)
} "Update to PowerShell 5.1 or later (Windows Server 2016+ has this by default)."

# 3. Python 3.11+
Check-Item "Python 3.11+" {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { return $false }
    $v = & python --version 2>&1
    if ($v -match "Python (\d+)\.(\d+)") {
        return ([int]$matches[1] -gt 3) -or ([int]$matches[1] -eq 3 -and [int]$matches[2] -ge 11)
    }
    return $false
} "winget install Python.Python.3.11 --silent --accept-source-agreements --accept-package-agreements"

# 4. PostgreSQL 14+ (we target 16 but 14+ works)
Check-Item "PostgreSQL 14+" {
    $svc = Get-Service -Name "postgresql-*" -ErrorAction SilentlyContinue
    return ($null -ne $svc)
} "Download PostgreSQL 16 from https://www.enterprisedb.com/downloads/postgres-postgresql-downloads — install with default options. Remember the postgres password."

# 5. IIS Web Server role
Check-Item "IIS Web Server role" {
    $f = Get-WindowsFeature -Name Web-Server -ErrorAction SilentlyContinue
    return ($null -ne $f -and $f.Installed)
} "Install-WindowsFeature -Name Web-Server, Web-Common-Http, Web-Static-Content, Web-Default-Doc, Web-Dir-Browsing, Web-Http-Errors, Web-Http-Logging, Web-Stat-Compression, Web-Filtering, Web-Mgmt-Console -IncludeManagementTools"

# 6. URL Rewrite IIS module
Check-Item "IIS URL Rewrite module" {
    $reg = "HKLM:\SOFTWARE\Microsoft\IIS Extensions\URL Rewrite"
    return (Test-Path $reg)
} "Download from https://www.iis.net/downloads/microsoft/url-rewrite — install the rewrite_amd64_en-US.msi."

# 7. Application Request Routing IIS module
Check-Item "IIS Application Request Routing" {
    $reg = "HKLM:\SOFTWARE\Microsoft\IIS Extensions\Application Request Routing"
    return (Test-Path $reg)
} "Download from https://www.iis.net/downloads/microsoft/application-request-routing — install the requestRouter_amd64.msi (this also pulls Web Farm Framework)."

# 8. NSSM (Non-Sucking Service Manager)
Check-Item "NSSM (service wrapper)" {
    $nssm = Get-Command nssm -ErrorAction SilentlyContinue
    if ($nssm) { return $true }
    return (Test-Path "C:\Program Files\nssm\nssm.exe")
} "The main install script will auto-download NSSM from https://nssm.cc — no action needed here."

# Summary
Write-Host ""
if ($missing.Count -eq 0) {
    Write-Host "All prerequisites satisfied. Run Install-OctoAssist-Server.ps1 next." -ForegroundColor Green
    exit 0
} else {
    Write-Host "$($missing.Count) prerequisite(s) missing. Fix in this order:" -ForegroundColor Yellow
    foreach ($m in $missing) {
        Write-Host ""
        Write-Host "  $($m.Name)" -ForegroundColor Yellow
        Write-Host "    $($m.Fix)" -ForegroundColor Gray
    }
    Write-Host ""
    Write-Host "After installing prerequisites, re-run this script to verify, then run Install-OctoAssist-Server.ps1." -ForegroundColor Cyan
    exit 1
}
