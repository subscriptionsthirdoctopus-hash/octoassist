"""Service layer for the remote-action channel.

Thin helpers around the RemoteAction model:
  - queue() / queue_for_fleet() — create new action(s)
  - recent() — list recent actions for a tenant, optionally filtered by kind/status
  - human_summary() — render an action's params/result as a short caption for UI

Adding a new action kind is two changes:
  1. Add a value to RemoteActionKind in models.py
  2. Add a handler in the agent's dispatcher
The dashboard form just calls queue() with the new kind + params.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..models import (
    Agent, RemoteAction, RemoteActionKind, RemoteActionStatus, User,
)


def queue(
    db: Session, *,
    tenant_id: int,
    creator: User,
    agent_id: int,
    kind: RemoteActionKind,
    params: dict[str, Any],
    ip_address: str | None = None,
) -> RemoteAction:
    """Queue a single action for one agent."""
    agent = db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != tenant_id:
        raise ValueError(f"agent {agent_id} not in tenant")
    action = RemoteAction(
        tenant_id=tenant_id,
        agent_id=agent_id,
        kind=kind,
        params=params,
        status=RemoteActionStatus.pending,
        created_by_id=creator.id,
    )
    db.add(action)
    db.commit()
    db.refresh(action)

    # Audit log
    from .audit import log_admin_action
    details = f"Queued remote action '{kind.value}' on Agent {agent.hostname} (ID: {agent_id}). Params: {params}"
    try:
        log_admin_action(db, tenant_id=tenant_id, user=creator, action="trigger_remote_action", details=details, ip_address=ip_address)
    except Exception:
        pass

    return action


def queue_for_fleet(
    db: Session, *,
    tenant_id: int,
    creator: User,
    kind: RemoteActionKind,
    params: dict[str, Any],
    hostname_pattern: str = "%",
    ip_address: str | None = None,
) -> list[RemoteAction]:
    """Queue one action per matching agent. SQL LIKE pattern (% wildcard)."""
    agents = (db.query(Agent)
                .filter(Agent.tenant_id == tenant_id,
                        Agent.hostname.like(hostname_pattern),
                        Agent.uninstall_pending.is_(False))
                .all())
    out: list[RemoteAction] = []
    for a in agents:
        out.append(RemoteAction(
            tenant_id=tenant_id,
            agent_id=a.id,
            kind=kind,
            params=params,
            status=RemoteActionStatus.pending,
            created_by_id=creator.id,
        ))
    if not out:
        return []
    db.add_all(out)
    db.commit()
    for a in out:
        db.refresh(a)

    # Audit log
    from .audit import log_admin_action
    details = f"Queued fleet-wide action '{kind.value}' on pattern '{hostname_pattern}' (matched {len(out)} agents). Params: {params}"
    try:
        log_admin_action(db, tenant_id=tenant_id, user=creator, action="trigger_fleet_action", details=details, ip_address=ip_address)
    except Exception:
        pass

    return out


def recent(
    db: Session, *,
    tenant_id: int,
    kind: RemoteActionKind | None = None,
    limit: int = 50,
) -> list[RemoteAction]:
    q = (db.query(RemoteAction)
           .filter(RemoteAction.tenant_id == tenant_id))
    if kind is not None:
        q = q.filter(RemoteAction.kind == kind)
    return (q.order_by(RemoteAction.created_at.desc())
              .limit(limit).all())


def cancel_pending(db: Session, action: RemoteAction) -> RemoteAction:
    """Mark a still-pending action as cancelled. No-op if already running/done."""
    if action.status != RemoteActionStatus.pending:
        return action
    action.status = RemoteActionStatus.cancelled
    action.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(action)
    return action


def human_summary(action: RemoteAction) -> str:
    """Short one-line summary for dashboard rows."""
    p = action.params or {}
    k = action.kind.value
    if k == "run_executable":
        label = p.get("label") or p.get("url", "")[:60]
        return f"Run installer: {label}"
    if k == "reboot":
        s = p.get("delay_seconds", 0)
        return f"Reboot in {s}s" if s else "Reboot now"
    if k == "lock_workstation":
        return "Lock workstation"
    if k == "send_toast":
        return f"Notify: {p.get('title','')[:60]}"
    if k == "list_processes":
        return "List running processes"
    if k == "kill_process":
        return f"Kill process: {p.get('name') or p.get('pid','?')}"
    if k == "set_wallpaper":
        return "Set wallpaper"
    if k == "reset_password":
        return f"Reset password: {p.get('username','?')}"
    if k == "custom_powershell":
        return "Run custom PowerShell"
    return k
