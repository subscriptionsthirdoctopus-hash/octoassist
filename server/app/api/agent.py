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

    # OctoAssist is Windows-only. Reject Linux / macOS at registration so
    # non-Windows endpoints never end up in any dashboard or deployment queue.
    family = (req.os_family or "").strip().lower()
    if family and family != "windows":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"OctoAssist is Windows-only. Registration refused from "
                    f"os_family='{req.os_family}'. Install on Windows 10 / 11."),
        )

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
    # OctoAssist is Windows-only. If an agent's inventory reports a non-Windows
    # OS, refuse to ingest. This catches:
    #   (a) older agents that registered before os_family validation
    #   (b) agents whose OS changed (e.g. dual-boot, OS swap) post-registration
    os_caption = ((req.os or {}).get("caption") or "").lower()
    if os_caption and "windows" not in os_caption and "microsoft" not in os_caption:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"OctoAssist is Windows-only. Agent reports OS='{os_caption}'. "
                    f"Inventory rejected; please uninstall OctoAssist on non-Windows hosts."),
        )

    now = datetime.now(timezone.utc)
    snapshot = AssetSnapshot(
        agent_id=agent.id,
        snapshot_at=req.snapshot_at or now,
        payload=req.model_dump(mode="json"),
    )
    db.add(snapshot)
    agent.last_seen_at = now

    # Phase B: link this laptop to its primary user (best-effort, never raises).
    from ..services.asset_linker import link_agent_to_user
    try:
        link_agent_to_user(db, agent=agent, logged_in_user=req.logged_in_user)
    except Exception:  # noqa: BLE001
        # User linking is enrichment; never block a check-in over it.
        pass

    if req.patches is not None:
        _upsert_patch_observations(db, agent.id, req.patches, now)

    db.commit()
    db.refresh(snapshot)
    return CheckinResponse(accepted=True, snapshot_id=snapshot.id)
