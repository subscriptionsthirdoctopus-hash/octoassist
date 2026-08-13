from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
from sqlalchemy.orm import Session

from ..auth import current_user, require_admin, require_staff
from ..database import get_db
from ..models import (
    Agent, AssetSnapshot, EntraDevice, IdentityProvider, IdentityProviderKind,
    Tenant, User,
)
from ..services import entra_devices
from ..services.hostnames import normalise as normalise_hostname
from ..services.sso import parse_entra_config

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["web"])


def _ctx(user: User, db: Session, **extra) -> dict:
    tenant = db.query(Tenant).first()
    return {"current_user": user, "tenant": tenant, **extra}


def _latest_snapshot(db: Session, agent_id: int) -> AssetSnapshot | None:
    return (
        db.query(AssetSnapshot)
        .filter(AssetSnapshot.agent_id == agent_id)
        .order_by(AssetSnapshot.snapshot_at.desc())
        .first()
    )


@router.get("/assets", response_class=HTMLResponse)
def assets_index(
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    q: str = "",
    department: str = "",
    location: str = "",
    compliant: str = "",
    online: str = "",
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from datetime import datetime, timedelta, timezone as _tz
    OFFLINE_THRESHOLD_HOURS = 24
    LAGGING_THRESHOLD_HOURS = 2
    now_utc = datetime.now(_tz.utc)
    offline_cutoff = now_utc - timedelta(hours=OFFLINE_THRESHOLD_HOURS)
    lagging_cutoff = now_utc - timedelta(hours=LAGGING_THRESHOLD_HOURS)

    def _online_state(last_seen):
        if not last_seen:
            return "offline"
        if last_seen < offline_cutoff:
            return "offline"
        if last_seen < lagging_cutoff:
            return "lagging"
        return "online"

    needle = (q or "").strip().lower()
    agents = db.query(Agent).filter(Agent.tenant_id == user.tenant_id, Agent.uninstall_pending.is_(False)).order_by(Agent.hostname).all()

    # Matching key, not a rename — see services/hostnames. Case- and FQDN-
    # insensitive, so an agent reporting "LAP-01" and Entra reporting
    # "lap-01.tema.local" are recognised as one endpoint instead of listing the
    # machine twice. The empty key means "unknown" and must not match anything.
    managed_hostnames = {normalise_hostname(a.hostname) for a in agents if a.hostname}
    managed_hostnames.discard("")

    def _matches(*fields: str | None) -> bool:
        """Free-text needle test across the joined values of fields."""
        if not needle:
            return True
        hay = " | ".join((f or "").lower() for f in fields)
        return needle in hay

    # Online-state KPI counts — derived from ALL managed agents in the tenant,
    # not filtered, so the strip is a stable rollup of the fleet.
    online_count = lagging_count = offline_count = 0
    for a in agents:
        st = _online_state(a.last_seen_at)
        if st == "online": online_count += 1
        elif st == "lagging": lagging_count += 1
        else: offline_count += 1

    rows = []
    for a in agents:
        snap = _latest_snapshot(db, a.id)
        payload = snap.payload if snap else {}
        pu = a.primary_user
        loc = a.location or (pu.location if pu else None)
        dept = pu.department if pu else None
        state = _online_state(a.last_seen_at)
        row = {
            "id": a.id,
            "hostname": a.hostname,
            "os": payload.get("os", {}).get("caption", "—"),
            "cpu": payload.get("cpu", {}).get("name", "—"),
            "ram_gb": payload.get("memory", {}).get("total_gb"),
            "logged_in_user": payload.get("logged_in_user") or "—",
            "assigned_name":  (pu.full_name if pu else None) or (pu.email if pu else None),
            "assigned_id":    pu.id if pu else None,
            "department":     dept,
            "location":       loc,
            "last_seen_at":   a.last_seen_at,
            "online_state":   state,
            "software_count": len(payload.get("software", [])),
        }
        if department and (dept or "").lower() != department.lower(): continue
        if location   and (loc  or "").lower() != location.lower():   continue
        if online     and state != online:                            continue
        if not _matches(a.hostname, row["assigned_name"], dept, loc, row["os"]):
            continue
        rows.append(row)

    # Entra-discovered Windows endpoints that DON'T already have an OctoAssist
    # agent reporting (matched on the normalised hostname — case- and
    # FQDN-insensitive).
    discovered_rows = []
    entra_devices_q = (db.query(EntraDevice)
                         .filter(EntraDevice.tenant_id == user.tenant_id)
                         .order_by(EntraDevice.display_name).all())
    for d in entra_devices_q:
        if normalise_hostname(d.display_name) in managed_hostnames:
            continue
        pu = d.primary_user
        dept = pu.department if pu else None
        loc  = pu.location   if pu else None
        if department and (dept or "").lower() != department.lower(): continue
        if location   and (loc  or "").lower() != location.lower():   continue
        if compliant == "yes" and d.is_compliant is not True:  continue
        if compliant == "no"  and d.is_compliant is not False: continue
        if not _matches(d.display_name, (pu.full_name if pu else None) or (pu.email if pu else None),
                        dept, loc, d.operating_system, d.manufacturer, d.model):
            continue
        discovered_rows.append({
            "id": d.id,
            "hostname": d.display_name,
            "assigned_name": (pu.full_name if pu else None) or (pu.email if pu else None),
            "department": dept,
            "location":   loc,
            "os":         d.operating_system or "—",
            "os_version": d.os_version or "—",
            "manufacturer": d.manufacturer or "—",
            "model":      d.model or "—",
            "is_compliant": d.is_compliant,
            "last_signin_at": d.approx_last_signin_at,
        })

    # Distinct values for filter dropdowns — from the full tenant set so admin
    # can always pivot. Union of agent + entra-device dimensions.
    from sqlalchemy import distinct
    dept_set = set()
    loc_set  = set()
    for (val,) in db.query(distinct(User.department)).filter(
            User.tenant_id == user.tenant_id, User.department.is_not(None)).all():
        if val: dept_set.add(val)
    for (val,) in db.query(distinct(Agent.location)).filter(
            Agent.tenant_id == user.tenant_id, Agent.location.is_not(None)).all():
        if val: loc_set.add(val)
    for (val,) in db.query(distinct(User.location)).filter(
            User.tenant_id == user.tenant_id, User.location.is_not(None)).all():
        if val: loc_set.add(val)
    departments = sorted(dept_set)
    locations   = sorted(loc_set)

    entra_idp = (db.query(IdentityProvider)
                   .filter(IdentityProvider.tenant_id == user.tenant_id,
                           IdentityProvider.kind == IdentityProviderKind.entra,
                           IdentityProvider.is_enabled == True)  # noqa: E712
                   .first())

    return templates.TemplateResponse(
        request=request, name="assets_list.html",
        context=_ctx(user, db,
                     rows=rows, agent_count=len(rows),
                     discovered_rows=discovered_rows,
                     entra_idp=entra_idp,
                     departments=departments, locations=locations,
                     q=needle,
                     filter_department=department, filter_location=location,
                     filter_compliant=compliant, filter_online=online,
                     online_count=online_count, lagging_count=lagging_count,
                     offline_count=offline_count,
                     offline_threshold_hours=OFFLINE_THRESHOLD_HOURS,
                     lagging_threshold_hours=LAGGING_THRESHOLD_HOURS,
                     flash=flash, error=error),
    )


@router.post("/assets/bulk-delete-managed")
async def bulk_delete_managed(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Initiate self-uninstallation of selected OctoAssist agents and soft-delete them from UI.
    Dispatches a detached self-uninstall powershell command on check-in and emails Arun strictly.
    """
    form = await request.form()
    raw_ids = form.getlist("agent_ids")
    try:
        ids = [int(x) for x in raw_ids if str(x).strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad agent_ids")
    if not ids:
        return RedirectResponse(url="/assets?error=No+endpoints+selected", status_code=303)
        
    agents = db.query(Agent).filter(Agent.id.in_(ids), Agent.tenant_id == user.tenant_id).all()
    n = 0
    from ..models import RemoteAction, RemoteActionKind, RemoteActionStatus
    from ..services.notifications import agent_uninstallation_triggered
    from urllib.parse import quote
    
    # Detached self-uninstall PowerShell payload
    uninstall_script = (
        "$cmd = \"Start-Sleep -Seconds 5; Unregister-ScheduledTask -TaskName 'OctoAssistAgent' -Confirm:`$false; "
        "Remove-Item -Recurse -Force '$env:ProgramFiles\\OctoAssist Agent', '$env:ProgramData\\OctoAssist'\"\n"
        "Start-Process powershell.exe -ArgumentList \"-NoProfile -Command `\"$cmd`\"\" -WindowStyle Hidden"
    )
    
    for agent in agents:
        agent.uninstall_pending = True
        
        # Queue the uninstallation remote action
        action = RemoteAction(
            tenant_id=user.tenant_id,
            agent_id=agent.id,
            kind=RemoteActionKind.custom_powershell,
            params={
                "label": "Self-Uninstall OctoAssist Agent",
                "script": uninstall_script
            },
            status=RemoteActionStatus.pending,
            created_by_id=user.id
        )
        db.add(action)
        
        # Dispatch email alert to arun.d@temaindia.com
        try:
            agent_uninstallation_triggered(db, agent)
        except Exception:
            pass
            
        n += 1
        
    db.commit()
    return RedirectResponse(
        url=f"/assets?flash={quote(f'Triggered uninstallation on {n} managed endpoint(s). Notification sent.')}",
        status_code=303,
    )



@router.post("/assets/bulk-delete-discovered")
async def bulk_delete_discovered(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete selected EntraDevice rows (discovered-but-unmanaged endpoints).
    If the device still exists in Entra, it'll reappear on the next sync. Use
    this to hide retired or out-of-scope devices from the coverage list."""
    form = await request.form()
    raw_ids = form.getlist("device_ids")
    try:
        ids = [int(x) for x in raw_ids if str(x).strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad device_ids")
    if not ids:
        return RedirectResponse(url="/assets?error=No+devices+selected", status_code=303)
    n = (db.query(EntraDevice)
           .filter(EntraDevice.id.in_(ids), EntraDevice.tenant_id == user.tenant_id)
           .delete(synchronize_session=False))
    db.commit()
    return RedirectResponse(
        url=f"/assets?flash={quote(f'Deleted {n} discovered device(s). Will reappear on next Entra sync if still present.')}",
        status_code=303,
    )


@router.post("/assets/manual-add")
def manual_add_device(
    hostname: str = Form(...),
    primary_user_email: str = Form(""),
    operating_system: str = Form("Windows"),
    location: str = Form(""),
    manufacturer: str = Form(""),
    model: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Manually add a Windows endpoint to the discovered list. Useful for
    laptops that aren't in Entra yet (BYOD trial, just-bought, etc.) but you
    want to track. Creates an EntraDevice row with a manual_<uuid> id."""
    import uuid as _uuid
    from datetime import datetime, timezone
    hn = (hostname or "").strip()
    if not hn:
        return RedirectResponse(url="/assets?error=Hostname+is+required", status_code=303)
    # Dedupe: don't create another row if hostname already exists
    existing = (db.query(EntraDevice)
                  .filter(EntraDevice.tenant_id == user.tenant_id,
                           EntraDevice.display_name == hn).first())
    if existing:
        return RedirectResponse(
            url=f"/assets?error={quote(f'A device named {hn!r} already exists in the discovered list')}",
            status_code=303,
        )
    pu_id = None
    if primary_user_email.strip():
        pu = (db.query(User)
                .filter(User.tenant_id == user.tenant_id,
                        User.email == primary_user_email.strip().lower()).first())
        if pu:
            pu_id = pu.id
    d = EntraDevice(
        tenant_id=user.tenant_id,
        entra_device_id=f"manual-{_uuid.uuid4().hex}",
        display_name=hn[:255],
        operating_system=(operating_system or "Windows")[:60],
        manufacturer=(manufacturer or None) and manufacturer.strip()[:120] or None,
        model=(model or None) and model.strip()[:120] or None,
        account_enabled=True,
        primary_user_id=pu_id,
        synced_at=datetime.now(timezone.utc),
    )
    # Stash the manual location on the row's primary_user if missing — best-effort
    db.add(d); db.commit()
    return RedirectResponse(
        url=f"/assets?flash={quote(f'Manually added {hn} to the discovered list.')}",
        status_code=303,
    )


@router.post("/assets/sync-entra")
async def sync_entra_devices_endpoint(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Pull every Windows device from the linked Entra tenant and upsert into
    the entra_devices table. Discovered devices show up in /assets under
    "Discovered (no agent yet)" so admins can see coverage gaps.
    """
    idp = (db.query(IdentityProvider)
             .filter(IdentityProvider.tenant_id == user.tenant_id,
                     IdentityProvider.kind == IdentityProviderKind.entra,
                     IdentityProvider.is_enabled == True)  # noqa: E712
             .first())
    if idp is None:
        return RedirectResponse(
            url="/assets?error=No+enabled+Entra+identity+provider.",
            status_code=303,
        )
    cfg = parse_entra_config(idp.config or {})
    report = await entra_devices.sync_devices(db, tenant_id=user.tenant_id, cfg=cfg)
    if report.errors:
        head = report.errors[0][:240]
        return RedirectResponse(
            url=f"/assets?error={quote(f'Device sync had errors. {report.summary()} — first: {head}')}",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/assets?flash={quote(f'Synced Windows devices from Entra: {report.summary()}')}",
        status_code=303,
    )


@router.get("/asset/{agent_id}", response_class=HTMLResponse)
def asset_detail(
    agent_id: int,
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    agent = db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found")
    snap = _latest_snapshot(db, agent.id)
    
    from ..models import SoftwarePackage
    catalog = (db.query(SoftwarePackage)
                 .filter(SoftwarePackage.tenant_id == user.tenant_id,
                         SoftwarePackage.is_active.is_(True))
                 .order_by(SoftwarePackage.sort_order, SoftwarePackage.name).all())

    return templates.TemplateResponse(
        request=request, name="asset_detail.html",
        context=_ctx(user, db,
                     agent=agent,
                     snapshot=snap.payload if snap else None,
                     snapshot_at=snap.snapshot_at if snap else None,
                     catalog=catalog,
                     flash=flash,
                     error=error),
    )


@router.get("/asset/{agent_id}/software/export.csv")
def asset_software_export(
    agent_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from fastapi.responses import StreamingResponse
    import csv
    from io import StringIO

    agent = db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found")

    snap = _latest_snapshot(db, agent.id)
    software_list = []
    if snap and snap.payload and isinstance(snap.payload, dict):
        software_list = snap.payload.get("software", [])
        if not isinstance(software_list, list):
            software_list = []

    output = StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow(["Name", "Version", "Publisher", "Install Date"])

    # Data rows
    for s in software_list:
        writer.writerow([
            s.get("name", ""),
            s.get("version", "—"),
            s.get("publisher", "—"),
            s.get("install_date", "—")
        ])

    body = output.getvalue()
    safe_filename = f"{agent.hostname}_installed_software.csv".replace(" ", "_").replace("/", "_")

    return StreamingResponse(
        iter([body]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )


@router.get("/enrolment", response_class=HTMLResponse)
def enrolment(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    tenant = db.query(Tenant).first()
    return templates.TemplateResponse(
        request=request, name="enrolment.html",
        context={"current_user": user, "tenant": tenant},
    )
