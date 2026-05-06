"""Agent → server deployment-pickup endpoints (Phase 6).

Workflow:
  1. Admin sets a PatchWindow to status='in_progress' with auto_execute=True
     and selected_packages set.
  2. Agent on its next check-in calls GET /api/v1/agent/deployments to fetch
     pending deployments for itself (its un-finished targets in active windows).
  3. For each pending target the agent: marks itself in-progress, runs the
     install (apt-get / Get-WindowsUpdate / etc.), POSTs an attempt row per
     package, then POSTs the finish summary.
  4. Server auto-marks the target as succeeded (all attempts ok) or failed.

Same Bearer-token auth as the rest of /api/v1/agent. The token only lets the
agent act on ITS OWN target rows — never anyone else's.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import authenticate_agent
from ..database import get_db
from ..models import (
    Agent, PatchDeploymentAttempt, PatchObservation, PatchSeverity,
    PatchWindow, PatchWindowStatus, PatchWindowTarget, PatchWindowTargetStatus,
)

router = APIRouter(prefix="/api/v1/agent", tags=["agent-deploy"])


# ---------- Schemas ----------

class PendingDeployment(BaseModel):
    target_id: int
    window_id: int
    window_name: str
    severity_filter: str | None
    selected_packages: list[str]
    method_hint: str = "auto"   # "apt", "dnf", "windows-update", "auto"


class AttemptIn(BaseModel):
    package_name: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    success: bool = False
    needs_reboot: bool = False
    stdout: str | None = None
    stderr: str | None = None
    method: str = "apt"


class FinishIn(BaseModel):
    note: str | None = None
    needs_reboot: bool = False


# ---------- Pending list ----------

@router.get("/deployments", response_model=list[PendingDeployment])
def list_pending_deployments(
    agent: Agent = Depends(authenticate_agent),
    db: Session = Depends(get_db),
) -> list[PendingDeployment]:
    targets = (db.query(PatchWindowTarget)
                 .join(PatchWindow, PatchWindow.id == PatchWindowTarget.window_id)
                 .filter(PatchWindowTarget.agent_id == agent.id,
                         PatchWindowTarget.status == PatchWindowTargetStatus.planned,
                         PatchWindow.status == PatchWindowStatus.in_progress,
                         PatchWindow.auto_execute == True,  # noqa: E712
                         PatchWindow.tenant_id == agent.tenant_id)
                 .all())

    out: list[PendingDeployment] = []
    for t in targets:
        win = t.window
        # Resolve selected packages: explicit list, or compute from current observations.
        if win.selected_packages:
            packages = list(win.selected_packages)
        else:
            q = (db.query(PatchObservation.package_name)
                   .filter(PatchObservation.agent_id == agent.id,
                           PatchObservation.resolved_at.is_(None)))
            if win.severity_filter is not None:
                q = q.filter(PatchObservation.severity == win.severity_filter)
            packages = sorted({n for (n,) in q.all()})

        out.append(PendingDeployment(
            target_id=t.id,
            window_id=win.id,
            window_name=win.name,
            severity_filter=(win.severity_filter.value if win.severity_filter else None),
            selected_packages=packages,
            method_hint="auto",
        ))
    return out


# ---------- Pickup ----------

def _load_target_for_agent(db: Session, agent: Agent, target_id: int) -> PatchWindowTarget:
    target = db.get(PatchWindowTarget, target_id)
    if target is None or target.agent_id != agent.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    win = target.window
    if win.tenant_id != agent.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if win.status != PatchWindowStatus.in_progress:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Window is not in_progress")
    return target


@router.post("/deployments/{target_id}/start")
def deployment_start(
    target_id: int,
    agent: Agent = Depends(authenticate_agent),
    db: Session = Depends(get_db),
):
    target = _load_target_for_agent(db, agent, target_id)
    if target.status not in (PatchWindowTargetStatus.planned, PatchWindowTargetStatus.in_progress):
        raise HTTPException(status_code=409, detail=f"Target already {target.status.value}")
    target.status = PatchWindowTargetStatus.in_progress
    db.commit()
    return {"accepted": True, "target_id": target.id, "status": target.status.value}


@router.post("/deployments/{target_id}/attempt")
def deployment_attempt(
    target_id: int,
    body: AttemptIn = Body(...),
    agent: Agent = Depends(authenticate_agent),
    db: Session = Depends(get_db),
):
    target = _load_target_for_agent(db, agent, target_id)
    att = PatchDeploymentAttempt(
        target_id=target.id,
        package_name=body.package_name[:255],
        started_at=body.started_at or datetime.now(timezone.utc),
        finished_at=body.finished_at,
        exit_code=body.exit_code,
        success=bool(body.success),
        needs_reboot=bool(body.needs_reboot),
        # Truncate verbose output to keep DB lean
        stdout=(body.stdout or "")[:8000] or None,
        stderr=(body.stderr or "")[:8000] or None,
        method=(body.method or "manual")[:32],
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    return {"accepted": True, "attempt_id": att.id}


@router.post("/deployments/{target_id}/finish")
def deployment_finish(
    target_id: int,
    body: FinishIn = Body(...),
    agent: Agent = Depends(authenticate_agent),
    db: Session = Depends(get_db),
):
    target = _load_target_for_agent(db, agent, target_id)
    # Compute final status from attempts
    attempts = (db.query(PatchDeploymentAttempt)
                  .filter(PatchDeploymentAttempt.target_id == target.id)
                  .all())
    if not attempts:
        target.status = PatchWindowTargetStatus.skipped
    else:
        all_ok = all(a.success for a in attempts)
        target.status = (PatchWindowTargetStatus.succeeded if all_ok
                         else PatchWindowTargetStatus.failed)
    if body.note:
        target.note = body.note[:1000]
    db.commit()
    return {"accepted": True, "status": target.status.value, "attempts": len(attempts)}
