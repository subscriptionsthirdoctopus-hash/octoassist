from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
from sqlalchemy.orm import Session

from ..auth import current_user, require_staff
from ..database import get_db
from ..models import Agent, AssetSnapshot, Tenant, User

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
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    agents = db.query(Agent).filter(Agent.tenant_id == user.tenant_id).order_by(Agent.hostname).all()
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
    return templates.TemplateResponse(
        request=request, name="assets_list.html",
        context=_ctx(user, db, rows=rows, agent_count=len(rows)),
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
