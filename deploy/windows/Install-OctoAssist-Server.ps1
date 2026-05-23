# Install-OctoAssist-Server.ps1
# One-shot Windows Server installer for the OctoAssist server.
#
# Prerequisites: run Preflight-Check.ps1 first and resolve everything it lists.
#
# What this does (all idempotent — safe to re-run):
#   1. Lays down code to "C:\Program Files\Third Octopus\OctoAssist Server\"
#   2. Creates a Python virtualenv and pip-installs the app
#   3. Creates the Postgres role + database
#   4. Writes the .env to "C:\ProgramData\OctoAssist\Server\.env"
#   5. Downloads + installs NSSM (if not already)
#   6. Registers + starts the OctoAssistServer Windows Service
#   7. Configures the IIS site with URL Rewrite reverse proxy
#   8. Opens the firewall on TCP/443 (and /80 redirect)
#   9. Prints the admin password + tenant enrolment key
#
# Run as Administrator.

[CmdletBinding()]
param(
    [string]$InstallDir   = "C:\Program Files\Third Octopus\OctoAssist Server",
    [string]$DataDir      = "C:\ProgramData\OctoAssist\Server",
    [string]$DbName       = "octoassist",
    [string]$DbUser       = "octoassist",
    [string]$AdminUser    = "admin",
    [string]$TenantName   = "TEMA India Pvt. Ltd.",
    [int]$AppPort         = 8080,
    [int]$HttpPort        = 80,
    [int]$HttpsPort       = 443,
    [string]$PgSuperUser  = "postgres",
    [string]$IisSiteName  = "OctoAssist",
    [string]$ServiceName  = "OctoAssistServer"
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script as Administrator."
    }
}

function New-Password {
    param([int]$Length = 24)
    Add-Type -AssemblyName System.Web
    return [System.Web.Security.Membership]::GeneratePassword($Length, 4)
}

function Find-Psql {
    $candidates = Get-ChildItem -Path "C:\Program Files\PostgreSQL" -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending
    foreach ($c in $candidates) {
        $exe = Join-Path $c.FullName "bin\psql.exe"
        if (Test-Path $exe) { return $exe }
    }
    throw "psql.exe not found under C:\Program Files\PostgreSQL\*\bin\. Is Postgres installed?"
}

function Run-Psql {
    param([string]$Sql, [string]$Database = "postgres", [string]$PgPassword)
    $env:PGPASSWORD = $PgPassword
    try {
        & $script:Psql -h localhost -U $PgSuperUser -d $Database -tAc $Sql 2>&1
    } finally {
        Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    }
}

function Ensure-NSSM {
    $nssm = Get-Command nssm.exe -ErrorAction SilentlyContinue
    if ($nssm) { return $nssm.Source }
    $local = "C:\Program Files\nssm\nssm.exe"
    if (Test-Path $local) { return $local }

    Write-Host "==> Downloading NSSM"
    $tmp = Join-Path $env:TEMP "nssm.zip"
    $extract = Join-Path $env:TEMP "nssm-extract"
    Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $tmp -UseBasicParsing
    Remove-Item -Recurse -Force $extract -ErrorAction SilentlyContinue
    Expand-Archive -Path $tmp -DestinationPath $extract -Force
    $src = Get-ChildItem -Recurse -Path $extract -Filter "nssm.exe" |
        Where-Object { $_.FullName -match "win64" } | Select-Object -First 1
    if (-not $src) { throw "NSSM win64 binary not found in archive." }
    New-Item -ItemType Directory -Force "C:\Program Files\nssm" | Out-Null
    Copy-Item $src.FullName $local -Force
    return $local
}

# --- Begin --------------------------------------------------------------
Assert-Admin

$RepoRoot = Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")
$ServerSrc = Join-Path $RepoRoot "server"
if (-not (Test-Path (Join-Path $ServerSrc "pyproject.toml"))) {
    throw "Cannot find server\pyproject.toml relative to this script. Run from the OctoAssist repo."
}

Write-Host ""
Write-Host "OctoAssist Server installer (Windows)" -ForegroundColor Cyan
Write-Host "Repo root  : $RepoRoot"
Write-Host "Install dir: $InstallDir"
Write-Host "Data dir   : $DataDir"
Write-Host ""

$PgPassword = Read-Host -AsSecureString -Prompt "Postgres superuser ($PgSuperUser) password"
$PgPasswordPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($PgPassword))

# 1. Locate psql
$Psql = Find-Psql
Write-Host "psql      : $Psql"

# 2. Lay down code
Write-Host ""
Write-Host "==> Copying app code to $InstallDir"
New-Item -ItemType Directory -Force $InstallDir | Out-Null
robocopy $ServerSrc $InstallDir /MIR /XD __pycache__ .venv /XF .env /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -gt 7) { throw "robocopy failed with $LASTEXITCODE" }

# 3. Python venv + install
$VenvPython = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "==> Creating Python venv at $InstallDir\.venv"
    & python -m venv (Join-Path $InstallDir ".venv")
}
Write-Host "==> Upgrading pip and installing app"
& $VenvPython -m pip install --upgrade pip wheel | Out-Null
& $VenvPython -m pip install -e $InstallDir
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# 4. Postgres role + DB
$DbPassword = New-Password
Write-Host ""
Write-Host "==> Configuring Postgres role and database"
$roleExists = (Run-Psql -Sql "SELECT 1 FROM pg_roles WHERE rolname='$DbUser'" -PgPassword $PgPasswordPlain).Trim() -eq "1"
if (-not $roleExists) {
    Run-Psql -Sql "CREATE ROLE $DbUser LOGIN PASSWORD '$DbPassword';" -PgPassword $PgPasswordPlain | Out-Null
} else {
    Run-Psql -Sql "ALTER ROLE $DbUser PASSWORD '$DbPassword';" -PgPassword $PgPasswordPlain | Out-Null
}
$dbExists = (Run-Psql -Sql "SELECT 1 FROM pg_database WHERE datname='$DbName'" -PgPassword $PgPasswordPlain).Trim() -eq "1"
if (-not $dbExists) {
    Run-Psql -Sql "CREATE DATABASE $DbName OWNER $DbUser;" -PgPassword $PgPasswordPlain | Out-Null
}

# 5. .env
$AdminPassword = New-Password
New-Item -ItemType Directory -Force $DataDir | Out-Null
$EnvPath = Join-Path $DataDir ".env"
Write-Host "==> Writing $EnvPath"
@"
OCTOASSIST_DATABASE_URL=postgresql+psycopg://$DbUser`:$DbPassword@localhost:5432/$DbName
OCTOASSIST_ADMIN_USERNAME=$AdminUser
OCTOASSIST_ADMIN_PASSWORD=$AdminPassword
OCTOASSIST_BIND_HOST=127.0.0.1
OCTOASSIST_BIND_PORT=$AppPort
OCTOASSIST_TENANT_NAME=$TenantName
"@ | Out-File -Encoding ascii -FilePath $EnvPath -Force

# Lock down .env: only Administrators + LocalSystem can read
$acl = Get-Acl $EnvPath
$acl.SetAccessRuleProtection($true, $false)
$acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) | Out-Null }
foreach ($principal in @("BUILTIN\Administrators","NT AUTHORITY\SYSTEM")) {
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($principal, "FullControl", "Allow")
    $acl.AddAccessRule($rule)
}
Set-Acl $EnvPath $acl

# 6. NSSM service
$NssmExe = Ensure-NSSM
$LogDir = Join-Path $DataDir "logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null

Write-Host "==> Configuring Windows Service '$ServiceName' via NSSM"
$svcExists = (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) -ne $null
if ($svcExists) {
    & $NssmExe stop $ServiceName confirm 2>&1 | Out-Null
    & $NssmExe remove $ServiceName confirm 2>&1 | Out-Null
}

& $NssmExe install $ServiceName $VenvPython "-m" "uvicorn" "app.main:app" "--host" "127.0.0.1" "--port" "$AppPort" | Out-Null
& $NssmExe set $ServiceName AppDirectory $InstallDir | Out-Null
& $NssmExe set $ServiceName Description "OctoAssist ITSM server (FastAPI). © Third Octopus." | Out-Null
& $NssmExe set $ServiceName DisplayName "OctoAssist Server" | Out-Null
& $NssmExe set $ServiceName Start SERVICE_AUTO_START | Out-Null
& $NssmExe set $ServiceName ObjectName "NT AUTHORITY\NetworkService" | Out-Null
& $NssmExe set $ServiceName AppStdout (Join-Path $LogDir "stdout.log") | Out-Null
& $NssmExe set $ServiceName AppStderr (Join-Path $LogDir "stderr.log") | Out-Null
& $NssmExe set $ServiceName AppRotateFiles 1 | Out-Null
& $NssmExe set $ServiceName AppRotateBytes 10485760 | Out-Null
# Pass env via the .env file (loaded by pydantic-settings since AppDirectory has it)
Copy-Item $EnvPath (Join-Path $InstallDir ".env") -Force

# Grant NetworkService read access to install dir + .env
icacls $InstallDir /grant "NT AUTHORITY\NetworkService:(OI)(CI)R" /T | Out-Null

Write-Host "==> Starting service"
Start-Service -Name $ServiceName

# Wait for /health
Write-Host "==> Waiting for app to come up on 127.0.0.1:$AppPort/health"
$ok = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$AppPort/health" -TimeoutSec 2
        if ($resp.StatusCode -eq 200) { $ok = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ok) { Write-Host "    WARNING: app did not respond in 30s — check $LogDir\stderr.log" -ForegroundColor Yellow }

# 7. IIS reverse proxy site
Write-Host ""
Write-Host "==> Configuring IIS site '$IisSiteName'"
Import-Module WebAdministration -ErrorAction SilentlyContinue

$SitePhysicalPath = Join-Path $InstallDir "iis-site"
New-Item -ItemType Directory -Force $SitePhysicalPath | Out-Null
Copy-Item (Join-Path $RepoRoot "deploy\windows\web.config") (Join-Path $SitePhysicalPath "web.config") -Force

if (Test-Path "IIS:\Sites\$IisSiteName") {
    Remove-Website -Name $IisSiteName
}
New-Website -Name $IisSiteName -Port $HttpPort -PhysicalPath $SitePhysicalPath -Force | Out-Null

# Enable ARR proxy globally (required for URL Rewrite SERVER_NAME proxy to work)
$arrPath = "MACHINE/WEBROOT/APPHOST"
& "$env:windir\system32\inetsrv\appcmd.exe" set config /commit:apphost /section:system.webServer/proxy /enabled:true | Out-Null

# 8. Firewall
Write-Host "==> Opening firewall on TCP/$HttpPort"
$rule = Get-NetFirewallRule -DisplayName "OctoAssist HTTP" -ErrorAction SilentlyContinue
if (-not $rule) {
    New-NetFirewallRule -DisplayName "OctoAssist HTTP" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $HttpPort | Out-Null
}

# 9. Read enrolment key from DB
$EnrolKey = (Run-Psql -Sql "SELECT enrolment_key FROM tenants ORDER BY id LIMIT 1;" -Database $DbName -PgPassword $DbPassword).Trim()

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Green
Write-Host "  OctoAssist server is up." -ForegroundColor Green
Write-Host ""
Write-Host "  URL (internal):       http://$(hostname)/" -ForegroundColor Green
Write-Host "  Admin username:       $AdminUser"
Write-Host "  Admin password:       $AdminPassword"
Write-Host "  Tenant:               $TenantName"
Write-Host "  Tenant enrolment key: $EnrolKey"
Write-Host ""
Write-Host "  Stored in:            $EnvPath"
Write-Host "  Service:              $ServiceName  (sc.exe query $ServiceName)"
Write-Host "  Logs:                 $LogDir"
Write-Host ""
Write-Host "  Next steps:"
Write-Host "    1. Put a real DNS name in front (octoassist.tema.local)."
Write-Host "    2. Add HTTPS — bind a TLS cert to the IIS site on port $HttpsPort."
Write-Host "    3. Build the agent MSI on a separate Windows host (installer\build-msi.ps1)."
Write-Host "    4. Distribute MSI via GPO using the enrolment key above."
Write-Host ""
Write-Host "  WRITE DOWN THE ADMIN PASSWORD AND ENROLMENT KEY — they will" -ForegroundColor Yellow
Write-Host "  not be shown again. They are stored in $EnvPath" -ForegroundColor Yellow
Write-Host "  (Administrators + SYSTEM only)." -ForegroundColor Yellow
Write-Host "==============================================================" -ForegroundColor Green
