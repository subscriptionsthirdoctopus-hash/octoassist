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

# 2. Python
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) {{
    Write-Step "Python not in PATH — installing Python 3.12 via winget"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {{
        throw "winget not available — install Python 3.10+ manually first."
    }}
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    # Refresh PATH in this session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pyCmd) {{ throw "Python install completed but `python` still not in PATH. Restart PowerShell and re-run." }}
}}
Write-Ok ("Python at " + $pyCmd.Path)

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
Write-Host "  Server:        $SERVER_URL"
Write-Host "  Config:        $DATA_DIR\agent.json"
Write-Host "  Logs:          $DATA_DIR\logs\agent.log"
Write-Host "  Task:          $TASK_NAME (Task Scheduler)"
Write-Host "  Run on demand: python `"$AGENT_SCRIPT`" --once"
Write-Host "  Stop:          Stop-ScheduledTask -TaskName `"$TASK_NAME`""
Write-Host "  Uninstall:     Unregister-ScheduledTask -TaskName `"$TASK_NAME`" -Confirm:`$false ;"
Write-Host "                 Remove-Item -Recurse -Force `"$INSTALL_DIR`",`"$DATA_DIR`""
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
