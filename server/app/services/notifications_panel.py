"""Notification bell counts — what the current user needs to act on.

Computed server-side on every authenticated browser request via the
middleware in main.py. Cheap aggregation queries (COUNT(*) with indexed
filters), called ~once per page load. If the page count grows we'd add
a 60s in-memory cache keyed by (user_id, minute-bucket).

The dict returned drives:
    .total       — the red badge on the bell icon (sum of items)
    .items       — list of {label, count, url, severity} for the dropdown panel

Each item is one row in the panel: 'Assigned to me · 5 → /tickets?mine=1'.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (
    Change, ChangeStatus, PatchObservation, PatchSeverity,
    Ticket, TicketStatus, User, UserRole,
)


def compute(db: Session, user: User) -> dict:
    """Return a dict for the bell dropdown:
        {
          "total": int,
          "items": [{label, count, url, severity}, ...],
        }
    """
    items: list[dict] = []
    tid = user.tenant_id

    # 1. Tickets assigned to me, not closed
    assigned = (db.query(func.count(Ticket.id))
                  .filter(Ticket.tenant_id == tid,
                          Ticket.assignee_id == user.id,
                          Ticket.status.in_([TicketStatus.open,
                                             TicketStatus.in_progress,
                                             TicketStatus.on_hold]))
                  .scalar()) or 0
    if assigned:
        items.append({
            "label": "Assigned to me",
            "count": int(assigned),
            "url": "/tickets?mine=1&status=",
            "severity": "info",
        })

    # 2. Tickets nearing SLA breach (assigned to me, < 4h to resolution)
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=4)
    near_breach = (db.query(func.count(Ticket.id))
                     .filter(Ticket.tenant_id == tid,
                             Ticket.assignee_id == user.id,
                             Ticket.resolved_at.is_(None),
                             Ticket.due_resolution_at.isnot(None),
                             Ticket.due_resolution_at <= horizon)
                     .scalar()) or 0
    if near_breach:
        items.append({
            "label": "SLA breach risk (<4h)",
            "count": int(near_breach),
            "url": "/tickets?mine=1&status=",
            "severity": "warn",
        })

    # 3. Changes awaiting MY CAB review (not my own, status=under_review)
    if user.role in (UserRole.admin, UserRole.agent):
        cab = (db.query(func.count(Change.id))
                 .filter(Change.tenant_id == tid,
                         Change.status == ChangeStatus.under_review,
                         Change.requester_id != user.id)
                 .scalar()) or 0
        if cab:
            items.append({
                "label": "Changes awaiting CAB",
                "count": int(cab),
                "url": "/changes?status=under_review",
                "severity": "warn",
            })

    # 4. Critical patches outstanding (admin-only — fleet-wide)
    if user.role == UserRole.admin:
        from ..models import Agent
        crit = (db.query(func.count(PatchObservation.id))
                  .join(Agent, Agent.id == PatchObservation.agent_id)
                  .filter(Agent.tenant_id == tid,
                          Agent.uninstall_pending.is_(False),
                          PatchObservation.resolved_at.is_(None),
                          PatchObservation.severity == PatchSeverity.critical)
                  .scalar()) or 0
        if crit:
            items.append({
                "label": "Critical patches outstanding",
                "count": int(crit),
                "url": "/patches?posture=pending",
                "severity": "danger",
            })

    return {
        "total": sum(it["count"] for it in items),
        "items": items,
    }


def empty() -> dict:
    """Cheap placeholder for unauthenticated / public pages."""
    return {"total": 0, "items": []}
