from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import authenticate_admin
from ..database import get_db
from ..models import Agent, AssetSnapshot

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])


def _latest_snapshot_for(db: Session, agent_id: int) -> AssetSnapshot | None:
    return (
        db.query(AssetSnapshot)
        .filter(AssetSnapshot.agent_id == agent_id)
        .order_by(AssetSnapshot.snapshot_at.desc())
        .first()
    )


@router.get("")
def list_assets(_: str = Depends(authenticate_admin), db: Session = Depends(get_db)) -> list[dict]:
    agents = db.query(Agent).all()
    out = []
    for a in agents:
        snap = _latest_snapshot_for(db, a.id)
        payload = snap.payload if snap else {}
        out.append({
            "agent_id": a.id,
            "hostname": a.hostname,
            "machine_id": a.machine_id,
            "os": payload.get("os", {}).get("caption", ""),
            "cpu": payload.get("cpu", {}).get("name", ""),
            "ram_gb": payload.get("memory", {}).get("total_gb"),
            "logged_in_user": payload.get("logged_in_user"),
            "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None,
            "software_count": len(payload.get("software", [])),
        })
    return out


@router.get("/{agent_id}")
def get_asset(agent_id: int, _: str = Depends(authenticate_admin), db: Session = Depends(get_db)) -> dict:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    snap = _latest_snapshot_for(db, agent.id)
    return {
        "agent": {
            "id": agent.id,
            "hostname": agent.hostname,
            "machine_id": agent.machine_id,
            "registered_at": agent.registered_at.isoformat(),
            "last_seen_at": agent.last_seen_at.isoformat() if agent.last_seen_at else None,
        },
        "snapshot": snap.payload if snap else None,
        "snapshot_at": snap.snapshot_at.isoformat() if snap else None,
    }
