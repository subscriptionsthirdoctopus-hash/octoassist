"""Ticket creation, transitions, and number assignment."""
from datetime import datetime, timezone
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (
    Category, Ticket, TicketComment, TicketEvent, TicketEventKind,
    TicketKind, TicketPriority, TicketStatus, User,
)
from .audit import record
from .sla import compute_sla
from . import notifications
from .location_routing import auto_assignee_for, derive_location_for


def next_ticket_number(db: Session, tenant_id: int, kind: TicketKind) -> str:
    prefix = "INC" if kind == TicketKind.incident else "SR"
    n = (
        db.query(func.count(Ticket.id))
        .filter(Ticket.tenant_id == tenant_id, Ticket.kind == kind)
        .scalar()
    ) or 0
    return f"{prefix}-{n + 1:05d}"


def create_ticket(
    db: Session,
    *,
    tenant_id: int,
    reporter: User,
    category: Category,
    title: str,
    description: str,
    priority: TicketPriority | None = None,
) -> Ticket:
    prio = priority or category.default_priority
    now = datetime.now(timezone.utc)
    due_response, due_resolution = compute_sla(category, prio, now)

    # Phase C: derive ticket location from the reporter's primary asset (or
    # their Entra profile fallback), then look up a routing rule to auto-
    # assign. Both are best-effort — failures leave fields NULL/None.
    location = derive_location_for(user=reporter, db=db)
    auto_assignee = auto_assignee_for(location=location, tenant_id=tenant_id, db=db)

    ticket = Ticket(
        tenant_id=tenant_id,
        ticket_number=next_ticket_number(db, tenant_id, category.kind),
        kind=category.kind,
        category_id=category.id,
        title=title.strip()[:255],
        description=description.strip(),
        priority=prio,
        status=TicketStatus.open,
        reporter_id=reporter.id,
        assignee_id=auto_assignee.id if auto_assignee else None,
        location=(location or None),
        due_response_at=due_response,
        due_resolution_at=due_resolution,
        created_at=now,
        updated_at=now,
    )
    db.add(ticket)
    db.flush()  # assign id

    after_payload = {
        "ticket_number": ticket.ticket_number,
        "title": ticket.title,
        "priority": ticket.priority.value,
        "category": category.name,
        "kind": ticket.kind.value,
    }
    if ticket.location:
        after_payload["location"] = ticket.location
    if auto_assignee:
        after_payload["auto_assigned_to"] = auto_assignee.email
    record(db, ticket=ticket, actor=reporter, kind=TicketEventKind.created, after=after_payload)
    db.commit()
    db.refresh(ticket)
    notifications.ticket_created(db, ticket)
    return ticket


def add_comment(db: Session, *, ticket: Ticket, author: User, body: str, is_internal: bool = False) -> TicketComment:
    body = body.strip()
    if not body:
        raise ValueError("empty comment")

    cm = TicketComment(
        ticket_id=ticket.id,
        author_id=author.id,
        body=body,
        is_internal=is_internal,
    )
    db.add(cm)

    # First-response timer: an agent commenting publicly closes the response SLA.
    if (ticket.first_response_at is None
            and not is_internal
            and author.role.value in ("admin", "agent")
            and author.id != ticket.reporter_id):
        ticket.first_response_at = datetime.now(timezone.utc)

    record(db, ticket=ticket, actor=author, kind=TicketEventKind.comment_added,
           after={"body_preview": body[:120], "is_internal": is_internal})
    ticket.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(cm)
    notifications.ticket_comment(db, ticket, cm)
    return cm


def transition_status(db: Session, *, ticket: Ticket, actor: User, new_status: TicketStatus) -> Ticket:
    if ticket.status == new_status:
        return ticket
    old = ticket.status
    ticket.status = new_status
    now = datetime.now(timezone.utc)
    if new_status == TicketStatus.resolved and ticket.resolved_at is None:
        ticket.resolved_at = now
    if new_status == TicketStatus.closed and ticket.closed_at is None:
        ticket.closed_at = now
    if new_status == TicketStatus.in_progress and ticket.first_response_at is None:
        ticket.first_response_at = now

    record(db, ticket=ticket, actor=actor, kind=TicketEventKind.status_changed,
           before={"status": old.value}, after={"status": new_status.value})
    ticket.updated_at = now
    db.commit()
    db.refresh(ticket)
    notifications.ticket_status_changed(db, ticket, old.value)
    return ticket


def assign(db: Session, *, ticket: Ticket, actor: User, assignee: User | None) -> Ticket:
    old_id = ticket.assignee_id
    new_id = assignee.id if assignee else None
    if old_id == new_id:
        return ticket
    ticket.assignee_id = new_id

    if new_id is None:
        record(db, ticket=ticket, actor=actor, kind=TicketEventKind.unassigned,
               before={"assignee_id": old_id})
    else:
        record(db, ticket=ticket, actor=actor, kind=TicketEventKind.assigned,
               before={"assignee_id": old_id},
               after={"assignee_id": new_id, "assignee_email": assignee.email if assignee else None})
    ticket.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(ticket)
    return ticket


def update_priority(db: Session, *, ticket: Ticket, actor: User, new_priority: TicketPriority) -> Ticket:
    if ticket.priority == new_priority:
        return ticket
    old = ticket.priority
    ticket.priority = new_priority
    record(db, ticket=ticket, actor=actor, kind=TicketEventKind.priority_changed,
           before={"priority": old.value}, after={"priority": new_priority.value})
    ticket.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(ticket)
    return ticket
