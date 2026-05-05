"""Audit log helper — every write to a ticket should append an event."""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Ticket, TicketEvent, TicketEventKind, User


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
