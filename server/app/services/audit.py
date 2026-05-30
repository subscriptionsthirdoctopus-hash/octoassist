"""Audit logging and log helper services for ticketing and admin actions."""
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from ..models import Ticket, TicketEvent, TicketEventKind, User, AuditLog

def record(
    db: Session,
    *,
    ticket: Ticket,
    actor: User | None,
    kind: TicketEventKind,
    before: dict | None = None,
    after: dict | None = None,
    note: str | None = None,
) -> TicketEvent:
    ev = TicketEvent(
        ticket_id=ticket.id,
        actor_id=actor.id if actor else None,
        kind=kind,
        before_value=before,
        after_value=after,
        note=note,
        created_at=datetime.now(timezone.utc),
    )
    db.add(ev)
    return ev


def log_admin_action(
    db: Session, *,
    tenant_id: int,
    user: User,
    action: str,
    details: str,
    ip_address: str | None = None
) -> None:
    # Capture only administrative or helpdesk staff actions (role 'admin' or 'agent')
    if user.role.value not in ("admin", "agent"):
        return
    
    log = AuditLog(
        tenant_id=tenant_id,
        user_id=user.id,
        action=action,
        details=details[:5000],
        ip_address=ip_address,
    )
    db.add(log)
    db.commit()
