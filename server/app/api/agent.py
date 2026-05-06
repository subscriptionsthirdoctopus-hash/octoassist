from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import authenticate_agent
from ..database import get_db
from ..models import Agent, AssetSnapshot, PatchAvailable, PatchSeverity, Tenant
from ..schemas import (
    AgentRegisterRequest,
    AgentRegisterResponse,
    CheckinRequest,
    CheckinResponse,
)

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


@router.post("/register", response_model=AgentRegisterResponse)
def register(req: AgentRegisterRequest, db: Session = Depends(get_db)) -> AgentRegisterResponse:
    tenant = db.query(Tenant).filter(Tenant.enrolment_key == req.enrolment_key).first()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid enrolment key")

    agent = db.query(Agent).filter(Agent.machine_id == req.machine_id).first()
    if agent is None:
        agent = Agent(tenant_id=tenant.id, machine_id=req.machine_id, hostname=req.hostname)
        db.add(agent)
    else:
        agent.hostname = req.hostname
    db.commit()
    db.refresh(agent)
    return AgentRegisterResponse(agent_id=agent.id, agent_token=agent.agent_token)


def _coerce_severity(raw: str | None) -> PatchSeverity:
    if not raw:
        return PatchSeverity.unknown
    try:
        return PatchSeverity(raw.lower())
    except ValueError:
        return PatchSeverity.unknown


@router.post("/checkin", response_model=CheckinResponse)
def checkin(
    req: CheckinRequest,
    agent: Agent = Depends(authenticate_agent),
    db: Session = Depends(get_db),
) -> CheckinResponse:
    snapshot = AssetSnapshot(
        agent_id=agent.id,
        snapshot_at=req.snapshot_at or datetime.now(timezone.utc),
        payload=req.model_dump(mode="json"),
    )
    db.add(snapshot)
    agent.last_seen_at = datetime.now(timezone.utc)

    # Patches: replace the agent's full set on each check-in.
    if req.patches is not None:
        db.query(PatchAvailable).filter(PatchAvailable.agent_id == agent.id).delete()
        now = datetime.now(timezone.utc)
        for p in req.patches[:5000]:  # safety cap
            name = (p.get("name") or "").strip()[:255]
            if not name:
                continue
            db.add(PatchAvailable(
                agent_id=agent.id,
                package_name=name,
                current_version=(p.get("current_version") or None),
                available_version=(p.get("available_version") or None),
                severity=_coerce_severity(p.get("severity")),
                source=(p.get("source") or "unknown")[:60],
                title=p.get("title"),
                detected_at=now,
            ))

    db.commit()
    db.refresh(snapshot)
    return CheckinResponse(accepted=True, snapshot_id=snapshot.id)
