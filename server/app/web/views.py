from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
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
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    agents = db.query(Agent).filter(Agent.tenant_id == user.tenant_id).order_by(Agent.hostname).all()
    # Hostnames of agents — used to suppress duplicate Entra-discovered rows
    managed_hostnames = {a.hostname.strip().lower() for a in agents if a.hostname}
    rows = []
    for a in agents:
        snap = _latest_snapshot(db, a.id)
        payload = snap.payload if snap else {}
        pu = a.primary_user  # SQLAlchemy lazy-loads the relationship
        rows.append({
            "id": a.id,
            "hostname": a.hostname,
            "os": payload.get("os", {}).get("caption", "—"),
            "cpu": payload.get("cpu", {}).get("name", "—"),
            "ram_gb": payload.get("memory", {}).get("total_gb"),
            "logged_in_user": payload.get("logged_in_user") or "—",
            "assigned_name":  (pu.full_name if pu else None) or (pu.email if pu else None),
            "assigned_id":    pu.id if pu else None,
            "department":     pu.department if pu else None,
            "location":       a.location or (pu.location if pu else None),
            "last_seen_at": a.last_seen_at,
            "software_count": len(payload.get("software", [])),
        })
    # Entra-discovered Windows endpoints that DON'T already have an OctoAssist
    # agent reporting (matched by hostname, case-insensitive). These are the
    # "coverage gap" — Windows laptops in the tenant that need OctoAssist.
    discovered_rows = []
    entra_devices_q = (db.query(EntraDevice)
                         .filter(EntraDevice.tenant_id == user.tenant_id)
                         .order_by(EntraDevice.display_name).all())
    for d in entra_devices_q:
        if d.display_name and d.display_name.strip().lower() in managed_hostnames:
            continue
        pu = d.primary_user
        discovered_rows.append({
            "id": d.id,
            "hostname": d.display_name,
            "assigned_name": (pu.full_name if pu else None) or (pu.email if pu else None),
            "department": pu.department if pu else None,
            "location":   pu.location   if pu else None,
            "os":         d.operating_system or "—",
            "os_version": d.os_version or "—",
            "manufacturer": d.manufacturer or "—",
            "model":      d.model or "—",
            "is_compliant": d.is_compliant,
            "last_signin_at": d.approx_last_signin_at,
        })

    # Is Entra sync available? Show the button only when an enabled Entra IdP exists.
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
                     flash=flash, error=error),
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
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    agent = db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found")
    snap = _latest_snapshot(db, agent.id)
    return templates.TemplateResponse(
        request=request, name="asset_detail.html",
        context=_ctx(user, db,
                     agent=agent,
                     snapshot=snap.payload if snap else None,
                     snapshot_at=snap.snapshot_at if snap else None),
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
