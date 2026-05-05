from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import authenticate_admin
from ..database import get_db
from ..models import Agent, AssetSnapshot, Tenant

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["web"])


def _latest_snapshot(db: Session, agent_id: int) -> AssetSnapshot | None:
    return (
        db.query(AssetSnapshot)
        .filter(AssetSnapshot.agent_id == agent_id)
        .order_by(AssetSnapshot.snapshot_at.desc())
        .first()
    )


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    _: str = Depends(authenticate_admin),
    db: Session = Depends(get_db),
):
    agents = db.query(Agent).order_by(Agent.hostname).all()
    rows = []
    for a in agents:
        snap = _latest_snapshot(db, a.id)
        payload = snap.payload if snap else {}
        rows.append({
            "id": a.id,
            "hostname": a.hostname,
            "os": payload.get("os", {}).get("caption", "—"),
            "cpu": payload.get("cpu", {}).get("name", "—"),
            "ram_gb": payload.get("memory", {}).get("total_gb"),
            "logged_in_user": payload.get("logged_in_user") or "—",
            "last_seen_at": a.last_seen_at,
            "software_count": len(payload.get("software", [])),
        })
    tenant = db.query(Tenant).first()
    return templates.TemplateResponse(
        request=request,
        name="assets_list.html",
        context={"rows": rows, "tenant": tenant, "agent_count": len(rows)},
    )


@router.get("/asset/{agent_id}", response_class=HTMLResponse)
def asset_detail(
    agent_id: int,
    request: Request,
    _: str = Depends(authenticate_admin),
    db: Session = Depends(get_db),
):
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    snap = _latest_snapshot(db, agent.id)
    return templates.TemplateResponse(
        request=request,
        name="asset_detail.html",
        context={
            "agent": agent,
            "snapshot": snap.payload if snap else None,
            "snapshot_at": snap.snapshot_at if snap else None,
        },
    )


@router.get("/enrolment", response_class=HTMLResponse)
def enrolment(
    request: Request,
    _: str = Depends(authenticate_admin),
    db: Session = Depends(get_db),
):
    tenant = db.query(Tenant).first()
    return templates.TemplateResponse(
        request=request,
        name="enrolment.html",
        context={"tenant": tenant},
    )
