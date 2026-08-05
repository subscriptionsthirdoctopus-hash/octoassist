"""Remote-action dashboard routes.

  GET  /actions                         — tenant-wide history with kind/status filters
  GET  /asset/{id}/actions              — per-endpoint history
  GET  /asset/{id}/processes            — latest list_processes result + kill buttons
  POST /asset/{id}/action               — queue a generic action (kind + params)
  POST /asset/{id}/processes/refresh    — queue a list_processes refresh
  POST /asset/{id}/processes/kill       — queue a kill_process action
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import require_admin, require_staff
from ..database import get_db
from ..jinja_filters import install_on
from ..models import (
    Agent, RemoteAction, RemoteActionKind, RemoteActionStatus, Tenant, User,
)
from ..services import remote_actions as ra_svc

from sqlalchemy.orm import Session

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["actions"])


def _ctx(user: User, db: Session, **extra) -> dict:
    return {"current_user": user, "tenant": db.query(Tenant).first(), **extra}


# ---------- Tenant-wide history ----------

@router.get("/actions", response_class=HTMLResponse)
def actions_list(
    request: Request,
    kind: str | None = None,
    status: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    q = (db.query(RemoteAction)
           .filter(RemoteAction.tenant_id == user.tenant_id))
    if kind:
        try:
            q = q.filter(RemoteAction.kind == RemoteActionKind(kind))
        except ValueError:
            pass
    if status:
        try:
            q = q.filter(RemoteAction.status == RemoteActionStatus(status))
        except ValueError:
            pass
    actions = (q.order_by(RemoteAction.created_at.desc()).limit(200).all())
    return templates.TemplateResponse(
        request=request, name="actions_list.html",
        context=_ctx(user, db,
                     actions=actions,
                     kinds=[k.value for k in RemoteActionKind],
                     statuses=[s.value for s in RemoteActionStatus],
                     filter_kind=kind or "", filter_status=status or "",
                     ra_svc=ra_svc),
    )


# ---------- Per-asset action queue ----------

@router.post("/asset/{agent_id}/action")
async def asset_action_queue(
    agent_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Single generic POST endpoint for every action kind. The form
    submits 'kind' + any kind-specific fields; we assemble the params
    dict here based on the kind."""
    form = await request.form()
    kind_str = (form.get("kind") or "").strip()
    try:
        kind = RemoteActionKind(kind_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown action kind: {kind_str}")

    # Build params per-kind. Anything not in the allow-list is dropped.
    params: dict = {}
    if kind == RemoteActionKind.run_executable:
        params = {"label": form.get("label", "")[:120],
                  "url": (form.get("url") or "").strip(),
                  "args": (form.get("args") or "").strip()}
    elif kind == RemoteActionKind.reboot:
        try:    params["delay_seconds"] = max(0, int(form.get("delay_seconds", 60)))
        except (TypeError, ValueError): params["delay_seconds"] = 60
        params["reason"] = (form.get("reason") or "OctoAssist-initiated reboot")[:120]
    elif kind == RemoteActionKind.lock_workstation:
        params = {}
    elif kind == RemoteActionKind.send_toast:
        params = {"title": (form.get("title") or "OctoAssist")[:80],
                  "body":  (form.get("body")  or "")[:500]}
    elif kind == RemoteActionKind.list_processes:
        params = {}
    elif kind == RemoteActionKind.kill_process:
        n = (form.get("name") or "").strip()
        p = (form.get("pid")  or "").strip()
        if not n and not p:
            raise HTTPException(status_code=400, detail="name or pid required")
        if n: params["name"] = n
        if p:
            try: params["pid"] = int(p)
            except ValueError: raise HTTPException(status_code=400, detail="pid must be int")
    elif kind == RemoteActionKind.set_wallpaper:
        # Accept either an uploaded image OR a pasted URL
        url = (form.get("url") or "").strip()
        wallpaper_file = form.get("wallpaper_file")
        file_supplied = (hasattr(wallpaper_file, "filename") and wallpaper_file.filename
                         and wallpaper_file.size and wallpaper_file.size > 0)
        if file_supplied:
            from ..api.uploads import ensure_upload_dir, _safe_ext, MAX_BYTES
            from ..models import UploadedFile as _UF
            import hashlib as _hl, uuid as _uuid
            file_id = _uuid.uuid4().hex
            ext = _safe_ext(wallpaper_file.filename)
            target_path = ensure_upload_dir() / f"{file_id}{ext}"
            h = _hl.sha256(); written = 0
            try:
                with open(target_path, "wb") as out:
                    while True:
                        chunk = await wallpaper_file.read(1024 * 256)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_BYTES:
                            out.close(); target_path.unlink(missing_ok=True)
                            raise HTTPException(status_code=413, detail="File too large (>1 GB)")
                        h.update(chunk); out.write(chunk)
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                target_path.unlink(missing_ok=True)
                raise HTTPException(status_code=500, detail=f"upload failed: {e}")
            db.add(_UF(
                id=file_id, tenant_id=user.tenant_id,
                original_filename=wallpaper_file.filename[:255],
                content_type=(wallpaper_file.content_type or "image/jpeg")[:120],
                size_bytes=written, sha256=h.hexdigest(),
                purpose="wallpaper", created_by_id=user.id,
            ))
            db.commit()
            from ..config import settings
            base = settings.base_url.rstrip("/")
            url = f"{base}/files/{file_id}{ext}"
        if not url:
            raise HTTPException(status_code=400, detail="Upload an image or paste a URL")
        params = {"url": url}
    elif kind == RemoteActionKind.reset_password:
        params = {"username":     (form.get("username") or "").strip(),
                  "new_password":  form.get("new_password") or ""}
    elif kind == RemoteActionKind.custom_powershell:
        params = {"script": form.get("script") or ""}
    elif kind == RemoteActionKind.force_refresh:
        params = {}   # no params; agent does the work

    try:
        action = ra_svc.queue(db,
                              tenant_id=user.tenant_id, creator=user,
                              agent_id=agent_id, kind=kind, params=params,
                              ip_address=request.client.host if request.client else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RedirectResponse(
        url=f"/asset/{agent_id}/actions?flash=Queued+{kind.value}+(#{action.id})",
        status_code=303,
    )


# ---------- Canned PSWindowsUpdate self-repair ----------
# Two-stage installer:
#   1. Try the orthodox path — TLS 1.2 + NuGet + PSGallery trust + Install-Module
#   2. If PSGallery is unreachable (corporate firewall blocking the public CDN),
#      fall back to the offline bundle we host at /agent/files/pswindowsupdate.zip
#      — the endpoint already trusts and reaches the OctoAssist server every
#      30 seconds, so this path works even on locked-down networks.
#
# The script uses a placeholder {SERVER_URL} which is resolved per-request
# from the incoming Request object — keeps the URL right whether the server
# is behind a custom domain, an IP, a non-standard port, etc.
_PSW_INSTALL_SCRIPT_TEMPLATE = r"""
$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Test-Installed { (Get-Module -ListAvailable PSWindowsUpdate) -ne $null }

# Stage 1 — orthodox PSGallery path
Write-Host '--- Stage 1: PSGallery ---'
try { Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -Scope AllUsers -ErrorAction Stop | Out-Null } catch { Write-Host ("NuGet bootstrap: {0}" -f $_.Exception.Message) }
try { Set-PSRepository -Name PSGallery -InstallationPolicy Trusted -ErrorAction Stop } catch { Write-Host ("PSGallery trust: {0}" -f $_.Exception.Message) }
if (-not (Test-Installed)) {
    try { Install-Module PSWindowsUpdate -Scope AllUsers -Force -AllowClobber -ErrorAction Stop } catch { Write-Host ("Install-Module: {0}" -f $_.Exception.Message) }
}

# Stage 2 — fall back to the offline bundle hosted on the OctoAssist server
if (-not (Test-Installed)) {
    Write-Host '--- Stage 2: offline bundle from OctoAssist server ---'
    $bundleUrl = '{SERVER_URL}/agent/files/pswindowsupdate.zip'
    $tmpZip   = Join-Path $env:TEMP 'pswindowsupdate.zip'
    $modRoot  = Join-Path $env:ProgramFiles 'WindowsPowerShell\Modules'
    try {
        Write-Host ("downloading {0}" -f $bundleUrl)
        Invoke-WebRequest -Uri $bundleUrl -OutFile $tmpZip -UseBasicParsing -ErrorAction Stop
        if (-not (Test-Path $modRoot)) { New-Item -ItemType Directory -Path $modRoot -Force | Out-Null }
        # Remove any old/broken install
        $existing = Join-Path $modRoot 'PSWindowsUpdate'
        if (Test-Path $existing) { Remove-Item -Path $existing -Recurse -Force -ErrorAction SilentlyContinue }
        # Unzip — Expand-Archive needs PS5+; this is Win10+ so fine
        Expand-Archive -Path $tmpZip -DestinationPath $modRoot -Force
        Remove-Item $tmpZip -ErrorAction SilentlyContinue
        Write-Host ("extracted PSWindowsUpdate to {0}" -f $existing)
    } catch {
        Write-Host ("offline bundle install failed: {0}" -f $_.Exception.Message)
    }
}

# Verify
$mod = Get-Module -ListAvailable PSWindowsUpdate | Select-Object -First 1
if ($mod) {
    Write-Host ("OK: PSWindowsUpdate {0} installed" -f $mod.Version)
    exit 0
} else {
    Write-Error "PSWindowsUpdate still missing after both PSGallery and offline-bundle attempts"
    exit 2
}
""".strip()


def _build_psw_install_script(request: Request) -> str:
    """Substitute {SERVER_URL} with the settings.base_url so the bundled
    PSWindowsUpdate.zip is fetched from the canonical OctoAssist server."""
    from ..config import settings
    base = settings.base_url.rstrip("/")
    return _PSW_INSTALL_SCRIPT_TEMPLATE.replace("{SERVER_URL}", base)


@router.post("/asset/{agent_id}/fix-pswindowsupdate")
def asset_fix_psw(
    agent_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """One-click remediation: queue the PSWindowsUpdate install script as a
    custom_powershell remote action. The script tries PSGallery first then
    falls back to the offline bundle we host at /agent/files/pswindowsupdate.zip
    — works even when the endpoint can't reach the public CDN.
    Surfaces in the same audit/action log as any other admin-initiated action."""
    agent = db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    try:
        action = ra_svc.queue(
            db, tenant_id=user.tenant_id, creator=user,
            agent_id=agent_id, kind=RemoteActionKind.custom_powershell,
            params={"script": _build_psw_install_script(request),
                    "label": "Self-repair: install PSWindowsUpdate"},
            ip_address=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(
        url=f"/asset/{agent_id}/actions?flash=Queued+PSWindowsUpdate+self-repair+(action+%23{action.id})+%E2%80%94+agent+picks+up+within+30+seconds",
        status_code=303,
    )


# ---------- Per-asset history ----------

@router.get("/asset/{agent_id}/actions", response_class=HTMLResponse)
def asset_actions(
    agent_id: int,
    request: Request,
    flash: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    agent = db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    actions = (db.query(RemoteAction)
                 .filter(RemoteAction.agent_id == agent_id,
                         RemoteAction.tenant_id == user.tenant_id)
                 .order_by(RemoteAction.created_at.desc())
                 .limit(100).all())
    return templates.TemplateResponse(
        request=request, name="asset_actions.html",
        context=_ctx(user, db, agent=agent, actions=actions, flash=flash,
                     ra_svc=ra_svc),
    )


# ---------- Per-asset processes ----------

@router.get("/asset/{agent_id}/processes", response_class=HTMLResponse)
def asset_processes(
    agent_id: int,
    request: Request,
    flash: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    agent = db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)

    # Latest completed list_processes action for this agent
    latest = (db.query(RemoteAction)
                .filter(RemoteAction.agent_id == agent_id,
                        RemoteAction.tenant_id == user.tenant_id,
                        RemoteAction.kind == RemoteActionKind.list_processes,
                        RemoteAction.status == RemoteActionStatus.succeeded)
                .order_by(RemoteAction.created_at.desc()).first())
    pending = (db.query(RemoteAction)
                 .filter(RemoteAction.agent_id == agent_id,
                         RemoteAction.tenant_id == user.tenant_id,
                         RemoteAction.kind == RemoteActionKind.list_processes,
                         RemoteAction.status.in_(
                             [RemoteActionStatus.pending,
                              RemoteActionStatus.in_progress]))
                 .order_by(RemoteAction.created_at.desc()).first())

    processes = []
    if latest and latest.result:
        processes = (latest.result.get("processes") or [])
    return templates.TemplateResponse(
        request=request, name="asset_processes.html",
        context=_ctx(user, db, agent=agent,
                     processes=processes,
                     scan_time=(latest.finished_at if latest else None),
                     pending_refresh=pending,
                     flash=flash),
    )


@router.post("/asset/{agent_id}/processes/refresh")
def asset_processes_refresh(
    agent_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    try:
        ra_svc.queue(db, tenant_id=user.tenant_id, creator=user,
                     agent_id=agent_id, kind=RemoteActionKind.list_processes,
                     params={},
                     ip_address=request.client.host if request.client else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(
        url=f"/asset/{agent_id}/processes?flash=Refresh+queued+(arrives+in+~30+sec)",
        status_code=303,
    )


@router.post("/asset/{agent_id}/processes/kill")
def asset_processes_kill(
    agent_id: int,
    request: Request,
    name: str = Form(""),
    pid:  str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    params: dict = {}
    name = name.strip(); pid = pid.strip()
    if name: params["name"] = name
    if pid:
        try: params["pid"] = int(pid)
        except ValueError: raise HTTPException(status_code=400, detail="pid must be int")
    if not params:
        raise HTTPException(status_code=400, detail="name or pid required")
    try:
        ra_svc.queue(db, tenant_id=user.tenant_id, creator=user,
                     agent_id=agent_id, kind=RemoteActionKind.kill_process,
                     params=params,
                     ip_address=request.client.host if request.client else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(
        url=f"/asset/{agent_id}/processes?flash=Kill+queued+for+{name or pid}",
        status_code=303,
    )


# ---------- Per-asset Windows Services ----------

@router.get("/asset/{agent_id}/services", response_class=HTMLResponse)
def asset_services(
    agent_id: int,
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    agent = db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)

    # Query latest completed custom powershell action with label "List Windows Services"
    latest = (db.query(RemoteAction)
                .filter(RemoteAction.agent_id == agent_id,
                        RemoteAction.tenant_id == user.tenant_id,
                        RemoteAction.kind == RemoteActionKind.custom_powershell,
                        RemoteAction.params['label'].astext == "List Windows Services",
                        RemoteAction.status == RemoteActionStatus.succeeded)
                .order_by(RemoteAction.created_at.desc()).first())
                
    pending = (db.query(RemoteAction)
                 .filter(RemoteAction.agent_id == agent_id,
                         RemoteAction.tenant_id == user.tenant_id,
                         RemoteAction.kind == RemoteActionKind.custom_powershell,
                         RemoteAction.params['label'].astext == "List Windows Services",
                         RemoteAction.status.in_(
                             [RemoteActionStatus.pending,
                              RemoteActionStatus.in_progress]))
                 .order_by(RemoteAction.created_at.desc()).first())

    services = []
    if latest and latest.stdout:
        try:
            import json
            services = json.loads(latest.stdout)
            if isinstance(services, dict):
                services = [services]
        except Exception:
            services = []
            
    # Process Status and StartType enums to strings for uniform rendering
    STATUS_MAP = {
        1: "Stopped",
        2: "StartPending",
        3: "StopPending",
        4: "Running",
        5: "ContinuePending",
        6: "PausePending",
        7: "Paused"
    }
    START_TYPE_MAP = {
        0: "Boot",
        1: "System",
        2: "Automatic",
        3: "Manual",
        4: "Disabled"
    }
    
    formatted_services = []
    for s in services:
        status_val = s.get("Status")
        if isinstance(status_val, int):
            status_str = STATUS_MAP.get(status_val, str(status_val))
        else:
            status_str = str(status_val or "Unknown")
            
        start_val = s.get("StartType")
        if isinstance(start_val, int):
            start_str = START_TYPE_MAP.get(start_val, str(start_val))
        else:
            start_str = str(start_val or "Unknown")
            
        formatted_services.append({
            "Name": s.get("Name", "Unknown"),
            "DisplayName": s.get("DisplayName", "Unknown"),
            "Status": status_str,
            "StartType": start_str
        })
        
    # Sort services by DisplayName
    formatted_services.sort(key=lambda x: x["DisplayName"].lower())

    return templates.TemplateResponse(
        request=request, name="asset_services.html",
        context=_ctx(user, db, agent=agent,
                     services=formatted_services,
                     scan_time=(latest.finished_at if latest else None),
                     pending_refresh=pending,
                     flash=flash,
                     error=error),
    )


@router.post("/asset/{agent_id}/services/refresh")
def asset_services_refresh(
    agent_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    script = "Get-Service | Select-Object Name, DisplayName, Status, StartType | ConvertTo-Json -Compress"
    try:
        ra_svc.queue(db, tenant_id=user.tenant_id, creator=user,
                     agent_id=agent_id, kind=RemoteActionKind.custom_powershell,
                     params={"script": script, "label": "List Windows Services"},
                     ip_address=request.client.host if request.client else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(
        url=f"/asset/{agent_id}/services?flash=Refresh+queued+(arrives+in+~30+sec)",
        status_code=303,
    )


@router.post("/asset/{agent_id}/services/control")
def asset_services_control(
    agent_id: int,
    request: Request,
    name: str = Form(...),
    action: str = Form(...),
    user: User = Depends(require_admin), # Service control is strictly admin-only!
    db: Session = Depends(get_db),
):
    name = name.strip()
    action = action.strip().lower()
    if action not in ("start", "stop", "restart"):
        raise HTTPException(status_code=400, detail="Invalid service action")
        
    if action == "start":
        cmd = f"Start-Service -Name '{name}'"
    elif action == "stop":
        cmd = f"Stop-Service -Name '{name}' -Force"
    else:
        cmd = f"Restart-Service -Name '{name}' -Force"
        
    # Combine command with service listing so the view updates instantly upon execution!
    script = f"{cmd}; Get-Service | Select-Object Name, DisplayName, Status, StartType | ConvertTo-Json -Compress"
    
    try:
        ra_svc.queue(db, tenant_id=user.tenant_id, creator=user,
                     agent_id=agent_id, kind=RemoteActionKind.custom_powershell,
                     params={"script": script, "label": "List Windows Services"},
                     ip_address=request.client.host if request.client else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    return RedirectResponse(
        url=f"/asset/{agent_id}/services?flash=Service+{action}+{name}+queued",
        status_code=303,
    )

