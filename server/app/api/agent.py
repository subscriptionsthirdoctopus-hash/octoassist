from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import authenticate_agent
from ..database import get_db
from ..models import Agent, AssetSnapshot, PatchObservation, PatchSeverity, Tenant
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


def _upsert_patch_observations(
    db: Session, agent_id: int, patches: list[dict], now: datetime
) -> tuple[int, int, int]:
    """Upsert observations + auto-resolve patches that disappeared from this check-in.

    - Existing observation still in this check-in → bump last_seen_at.
    - New observation                              → insert with first_seen_at = now.
    - Existing observation NOT in this check-in    → set resolved_at = now (= installed).

    Returns (added, refreshed, resolved) for logging / future telemetry.
    """
    existing = (db.query(PatchObservation)
                  .filter(PatchObservation.agent_id == agent_id,
                          PatchObservation.resolved_at.is_(None))
                  .all())
    by_key: dict[tuple[str, str | None], PatchObservation] = {
        (o.package_name, o.available_version): o for o in existing
    }

    added = refreshed = 0
    seen: set[tuple[str, str | None]] = set()

    for p in patches[:5000]:
        name = (p.get("name") or "").strip()[:255]
        if not name:
            continue
        ver = p.get("available_version") or None
        key = (name, ver)
        seen.add(key)

        if key in by_key:
            obs = by_key[key]
            obs.last_seen_at = now
            obs.severity = _coerce_severity(p.get("severity"))
            obs.source = (p.get("source") or obs.source)[:60]
            if p.get("title"):
                obs.title = p["title"]
            if p.get("current_version"):
                obs.current_version = p["current_version"]
            refreshed += 1
        else:
            db.add(PatchObservation(
                agent_id=agent_id,
                package_name=name,
                current_version=(p.get("current_version") or None),
                available_version=ver,
                severity=_coerce_severity(p.get("severity")),
                source=(p.get("source") or "unknown")[:60],
                title=p.get("title"),
                first_seen_at=now,
                last_seen_at=now,
            ))
            added += 1

    resolved = 0
    for key, obs in by_key.items():
        if key not in seen:
            obs.resolved_at = now
            resolved += 1

    return added, refreshed, resolved


@router.post("/checkin", response_model=CheckinResponse)
def checkin(
    req: CheckinRequest,
    agent: Agent = Depends(authenticate_agent),
    db: Session = Depends(get_db),
) -> CheckinResponse:
    now = datetime.now(timezone.utc)
    snapshot = AssetSnapshot(
        agent_id=agent.id,
        snapshot_at=req.snapshot_at or now,
        payload=req.model_dump(mode="json"),
    )
    db.add(snapshot)
    agent.last_seen_at = now

    if req.patches is not None:
        _upsert_patch_observations(db, agent.id, req.patches, now)

    db.commit()
    db.refresh(snapshot)
    return CheckinResponse(accepted=True, snapshot_id=snapshot.id)
