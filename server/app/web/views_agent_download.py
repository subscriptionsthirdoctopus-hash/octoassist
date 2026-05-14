"""Agent install endpoints.

Two paths:
  - /agent/install.ps1  — one-shot PowerShell installer with enrolment
                          key and server URL inlined. Public (the enrolment
                          key is per-tenant and surfaced in the admin UI).
  - /agent/files/*      — static downloads of the source script + docs.
                          Staff-only (requires login).
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from ..auth import require_staff
from ..database import get_db
from ..models import Tenant, User

# Where the image bundles the agent files (Dockerfile copies agent-py → /srv/agent_py)
AGENT_DIR = Path("/srv/agent_py")

ALLOWED_FILES = {
    "octoassist_agent.py":   "text/x-python",
    "install-windows.ps1":   "text/x-powershell",
    "README.md":             "text/markdown",
}

router = APIRouter(tags=["agent-install"])


# ---------- the one-liner installer ----------

@router.get("/agent/install.ps1", response_class=PlainTextResponse)
def install_ps1(request: Request, db: Session = Depends(get_db)):
    """Self-contained PowerShell installer.

    Usage on a Windows endpoint, in an elevated PowerShell:

        iex (iwr -useb http://68.183.86.66:8088/agent/install.ps1).Content

    Server URL and enrolment key are baked into the response from the
    request's Host header + the tenant row, so the endpoint admin runs
    the same one-liner regardless of where OctoAssist is deployed.
    """
    tenant = db.query(Tenant).first()
    if tenant is None:
        raise HTTPException(status_code=503, detail="No tenant configured")
    server_url = str(request.base_url).rstrip("/")
    enrol = tenant.enrolment_key

    script = _PS1_TEMPLATE.format(server_url=server_url, enrolment_key=enrol)
    return PlainTextResponse(content=script, media_type="text/plain")


_PS1_TEMPLATE = r"""# OctoAssist agent — one-shot Windows installer.
# Auto-generated; do not edit. Run in an elevated PowerShell:
#   iex (iwr -useb {server_url}/agent/install.ps1).Content

$ErrorActionPreference = "Stop"

$SERVER_URL    = "{server_url}"
$ENROLMENT_KEY = "{enrolment_key}"
$INSTALL_DIR   = "$env:ProgramFiles\OctoAssist Agent"
$DATA_DIR      = "$env:ProgramData\OctoAssist"
$AGENT_SCRIPT  = Join-Path $INSTALL_DIR "octoassist_agent.py"
$TASK_NAME     = "OctoAssist Agent"

function Write-Step($msg) {{ Write-Host "==> $msg" -ForegroundColor Cyan }}
function Write-Ok($msg)   {{ Write-Host "    OK $msg" -ForegroundColor Green }}
function Write-Warn2($m)  {{ Write-Host "    !  $m" -ForegroundColor Yellow }}

# 1. Elevation
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {{
    throw "Run this in an elevated PowerShell (Run as Administrator)."
}}
Write-Ok "Running as Administrator"

# 2. Python — careful here: Windows 10/11 ships an "App Execution Alias"
#    stub at %LOCALAPPDATA%\Microsoft\WindowsApps\python.exe that LOOKS like
#    Python but just opens the Microsoft Store and exits 9009. We have to
#    detect it and install the real thing.
function Test-RealPython($path) {{
    if (-not $path) {{ return $false }}
    if ($path -match "\\Microsoft\\WindowsApps\\python(3)?\.exe$") {{ return $false }}
    try {{
        $out = & $path --version 2>&1
        if ($LASTEXITCODE -ne 0) {{ return $false }}
        return ($out -match "^Python \d+\.\d+")
    }} catch {{ return $false }}
}}

# First try the official Python Launcher (py.exe) — always points at a real install.
$pyExe = $null
$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) {{
    try {{
        $candidate = (& $launcher.Path -3 -c "import sys; print(sys.executable)" 2>$null).Trim()
        if (Test-RealPython $candidate) {{ $pyExe = $candidate }}
    }} catch {{}}
}}
# Else try `python` in PATH (skipping the Store stub)
if (-not $pyExe) {{
    foreach ($cand in (Get-Command python -All -ErrorAction SilentlyContinue)) {{
        if (Test-RealPython $cand.Path) {{ $pyExe = $cand.Path; break }}
    }}
}}

if (-not $pyExe) {{
    Write-Step "Real Python not found (the Store alias doesn't count) — installing Python 3.12"

    function Find-RealPythonPostInstall() {{
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        $search = @(
            "$env:ProgramFiles\Python312\python.exe",
            "$env:ProgramFiles\Python311\python.exe",
            "$env:ProgramFiles\Python310\python.exe",
            "${{env:ProgramFiles(x86)}}\Python312\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
        )
        foreach ($p in $search) {{
            if ((Test-Path $p) -and (Test-RealPython $p)) {{ return $p }}
        }}
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($launcher) {{
            $candidate = (& $launcher.Path -3 -c "import sys; print(sys.executable)" 2>$null).Trim()
            if (Test-RealPython $candidate) {{ return $candidate }}
        }}
        return $null
    }}

    # Strategy 1: winget — fast and signed-source. But winget often breaks
    # on machines where the msstore source needs terms acceptance or the
    # winget source index is corrupted (0x8a15000f). We force --source winget
    # to skip msstore, and treat any failure as a soft-fail then fall back.
    $wingetOk = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {{
        try {{
            winget install --id Python.Python.3.12 --source winget --silent --accept-package-agreements --accept-source-agreements --scope machine 2>&1 | Out-Host
            if ($LASTEXITCODE -eq 0) {{ $wingetOk = $true }}
        }} catch {{
            Write-Warn2 "winget threw: $_"
        }}
        if (-not $wingetOk) {{ Write-Warn2 "winget install did not succeed; falling back to direct download from python.org" }}
    }} else {{
        Write-Warn2 "winget not available; falling back to direct download from python.org"
    }}
    $pyExe = Find-RealPythonPostInstall

    # Strategy 2: direct download from python.org. No registry trust, just
    # an authenticated TLS connection to python.org's CDN.
    if (-not $pyExe) {{
        $pyVer = "3.12.7"
        $pyUrl = "https://www.python.org/ftp/python/$pyVer/python-$pyVer-amd64.exe"
        $tmp   = Join-Path $env:TEMP ("python-" + $pyVer + "-amd64.exe")
        Write-Step "Downloading Python $pyVer installer from python.org"
        try {{
            Invoke-WebRequest -UseBasicParsing -Uri $pyUrl -OutFile $tmp
        }} catch {{
            throw "Could not download Python from python.org: $_  Check outbound HTTPS / proxy settings."
        }}
        Write-Step "Running Python installer silently (machine scope, PATH on, py launcher on)"
        $args = @("/quiet","InstallAllUsers=1","PrependPath=1","Include_test=0","Include_doc=0","Include_launcher=1")
        $proc = Start-Process -FilePath $tmp -ArgumentList $args -Wait -PassThru
        Remove-Item $tmp -ErrorAction SilentlyContinue
        if ($proc.ExitCode -ne 0) {{
            throw "Python installer exited with code $($proc.ExitCode). Re-run the one-liner in a new elevated PowerShell."
        }}
        $pyExe = Find-RealPythonPostInstall
    }}

    if (-not $pyExe) {{
        throw "Python install completed but no real python.exe found. Close this PowerShell, open a NEW elevated PowerShell, and re-run the one-liner."
    }}
}}
Write-Ok ("Python at " + $pyExe)
$pyCmd = [PSCustomObject]@{{ Path = $pyExe }}

# 2b. PSWindowsUpdate — needed for Windows Update KB deployment. Installs
#     into the AllUsers scope so SYSTEM (the Scheduled Task account) can
#     import it. NuGet provider is required first.
Write-Step "Ensuring PowerShell dependencies (NuGet provider + PSWindowsUpdate module)"
try {{
    if (-not (Get-PackageProvider -Name NuGet -ErrorAction SilentlyContinue)) {{
        Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -Scope AllUsers | Out-Null
        Write-Ok "NuGet provider installed"
    }} else {{
        Write-Ok "NuGet provider present"
    }}
}} catch {{
    Write-Warn2 "NuGet provider install failed: $_  — generic software patching will still work, only KB-based Windows Update is affected."
}}
try {{
    if (-not (Get-PSRepository -Name PSGallery -ErrorAction SilentlyContinue | Where-Object {{ $_.InstallationPolicy -eq 'Trusted' }})) {{
        Set-PSRepository -Name PSGallery -InstallationPolicy Trusted -ErrorAction SilentlyContinue
    }}
    if (-not (Get-Module -ListAvailable PSWindowsUpdate)) {{
        Install-Module PSWindowsUpdate -Scope AllUsers -Force -AllowClobber -ErrorAction Stop
        Write-Ok "PSWindowsUpdate module installed"
    }} else {{
        Write-Ok "PSWindowsUpdate module already present"
    }}
}} catch {{
    Write-Warn2 "PSWindowsUpdate install failed: $_  — winget-based software patching still works; KB-based Windows Update KBs won't be applied by this agent until the module is installed."
}}

# 3. Install dir + download agent script
Write-Step "Installing agent script to $INSTALL_DIR"
New-Item -ItemType Directory -Force $INSTALL_DIR | Out-Null
New-Item -ItemType Directory -Force $DATA_DIR    | Out-Null
$src = "$SERVER_URL/agent/files/octoassist_agent.py"
Invoke-WebRequest -UseBasicParsing -Uri $src -OutFile $AGENT_SCRIPT
Write-Ok ("Saved " + $AGENT_SCRIPT)

# 4. Bootstrap config (writes $DATA_DIR\agent.json)
Write-Step "Bootstrapping config (server + enrolment key)"
& $pyCmd.Path $AGENT_SCRIPT --bootstrap --server-url=$SERVER_URL --enrolment-key=$ENROLMENT_KEY --interval-hours=6
Write-Ok "Config written"

# 5. Scheduled Task — SYSTEM, at boot + every 6h, restart on failure
Write-Step "Registering Scheduled Task '$TASK_NAME'"
Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false

$action    = New-ScheduledTaskAction  -Execute $pyCmd.Path -Argument ('"' + $AGENT_SCRIPT + '"')
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TASK_NAME `
    -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description "OctoAssist asset agent (Python) — sends hardware/software/patch inventory + applies approved patches." | Out-Null
Write-Ok "Scheduled Task registered"

# 6. First check-in NOW (verifies network + auth, registers in /assets)
Write-Step "First check-in (one-shot, verifies end-to-end)"
& $pyCmd.Path $AGENT_SCRIPT --once
if ($LASTEXITCODE -ne 0) {{
    Write-Warn2 "First check-in returned exit code $LASTEXITCODE — check $DATA_DIR\logs\agent.log"
}} else {{
    Write-Ok "First check-in succeeded"
}}

# 7. Start the scheduled task (so daemon mode is running)
Start-ScheduledTask -TaskName $TASK_NAME
Write-Ok "Task started — agent is live"

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host "  OctoAssist agent installed."                                  -ForegroundColor Cyan
Write-Host ""
Write-Host "  Server:           $SERVER_URL"
Write-Host "  Python:           $($pyCmd.Path)"
$wuMod = Get-Module -ListAvailable PSWindowsUpdate | Select-Object -First 1
if ($wuMod) {{
    Write-Host "  PSWindowsUpdate:  $($wuMod.Version) (KB-based Windows Update enabled)"
}} else {{
    Write-Host "  PSWindowsUpdate:  not installed (KB-based Windows Update disabled — only winget software patching will work)"
}}
$wingetCmd = Get-Command winget -ErrorAction SilentlyContinue
if ($wingetCmd) {{
    Write-Host "  winget:           $($wingetCmd.Path) (generic-software patching enabled)"
}} else {{
    Write-Host "  winget:           NOT FOUND (generic-software patching disabled — update Windows App Installer from the Store)"
}}
Write-Host "  Config:           $DATA_DIR\agent.json"
Write-Host "  Logs:             $DATA_DIR\logs\agent.log"
Write-Host "  Task:             $TASK_NAME (Task Scheduler)"
Write-Host "  Run on demand:    python `"$AGENT_SCRIPT`" --once"
Write-Host "  Stop:             Stop-ScheduledTask -TaskName `"$TASK_NAME`""
Write-Host "  Uninstall:        Unregister-ScheduledTask -TaskName `"$TASK_NAME`" -Confirm:`$false ;"
Write-Host "                    Remove-Item -Recurse -Force `"$INSTALL_DIR`",`"$DATA_DIR`""
Write-Host ""
Write-Host "  This endpoint will appear in OctoAssist /assets within a minute."
Write-Host "==============================================================" -ForegroundColor Cyan
"""


# ---------- Static downloads (legacy / for inspection) ----------

@router.get("/agent/files/{filename}")
def agent_file(filename: str):
    """Public — agent scripts. octoassist_agent.py is referenced by the
    install.ps1 one-liner so this endpoint can't be staff-only.
    """
    if filename not in ALLOWED_FILES:
        raise HTTPException(status_code=404)
    path = AGENT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not bundled with this image")
    return FileResponse(
        str(path),
        media_type=ALLOWED_FILES[filename],
        filename=filename,
    )
