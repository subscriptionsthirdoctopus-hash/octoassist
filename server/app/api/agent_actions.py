"""Remote action channel — agent <-> server.

Workflow:
  1. Admin creates a RemoteAction row (kind + params) via the dashboard,
     targeted at one agent. Status = pending.
  2. Agent on its next 30-sec poll calls GET /api/v1/agent/actions.
     Server returns all pending actions for that agent.
  3. For each action the agent:
       - POSTs /actions/{id}/start  → status flips to in_progress
       - Runs the action locally (Get-Process / Stop-Process / .exe / etc.)
       - POSTs /actions/{id}/result with exit_code + stdout + stderr +
         optional structured result. Status flips to succeeded or failed.

Same Bearer-token auth as the rest of /api/v1/agent. Token only lets the
agent act on ITS OWN action rows.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import authenticate_agent
from ..database import get_db
from ..models import Agent, RemoteAction, RemoteActionStatus

router = APIRouter(prefix="/api/v1/agent", tags=["agent-actions"])


class PendingAction(BaseModel):
    id: int
    kind: str
    params: dict


class ActionResult(BaseModel):
    exit_code: int | None = None
    stdout:    str | None = None
    stderr:    str | None = None
    result:    dict | None = None       # structured payload (e.g. process list)
    success:   bool = False             # explicit success bit


@router.get("/actions", response_model=list[PendingAction])
def list_pending_actions(
    agent: Agent = Depends(authenticate_agent),
    db: Session = Depends(get_db),
) -> list[PendingAction]:
    rows = (db.query(RemoteAction)
              .filter(RemoteAction.agent_id == agent.id,
                      RemoteAction.tenant_id == agent.tenant_id,
                      RemoteAction.status == RemoteActionStatus.pending)
              .order_by(RemoteAction.created_at).all())
    return [PendingAction(id=r.id, kind=r.kind.value, params=r.params or {}) for r in rows]


def _load_for_agent(db: Session, agent: Agent, action_id: int) -> RemoteAction:
    a = db.get(RemoteAction, action_id)
    if a is None or a.agent_id != agent.id or a.tenant_id != agent.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return a


@router.post("/actions/{action_id}/start")
def action_start(
    action_id: int,
    agent: Agent = Depends(authenticate_agent),
    db: Session = Depends(get_db),
):
    a = _load_for_agent(db, agent, action_id)
    if a.status not in (RemoteActionStatus.pending, RemoteActionStatus.in_progress):
        raise HTTPException(status_code=409, detail=f"Action already {a.status.value}")
    a.status = RemoteActionStatus.in_progress
    a.started_at = datetime.now(timezone.utc)
    db.commit()
    return {"accepted": True, "id": a.id, "status": a.status.value}


@router.post("/actions/{action_id}/result")
def action_result(
    action_id: int,
    body: ActionResult = Body(...),
    agent: Agent = Depends(authenticate_agent),
    db: Session = Depends(get_db),
):
    a = _load_for_agent(db, agent, action_id)
    a.exit_code   = body.exit_code
    a.stdout      = (body.stdout or "")[:32000] or None
    a.stderr      = (body.stderr or "")[:8000]  or None
    a.result      = body.result
    a.status      = (RemoteActionStatus.succeeded if body.success
                     else RemoteActionStatus.failed)
    a.finished_at = datetime.now(timezone.utc)
    db.commit()

    # Trigger notifications for software remote actions
    try:
        from ..services.notifications import remote_action_completed
        remote_action_completed(db, a)
    except Exception:
        pass

    return {"accepted": True, "id": a.id, "status": a.status.value}
