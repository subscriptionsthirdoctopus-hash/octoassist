"""Problem Management — number assignment, ticket linkage, transitions."""
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (
    Problem, ProblemStatus, ProblemTicketLink, Ticket, TicketPriority, User,
)


def next_problem_number(db: Session, tenant_id: int) -> str:
    n = db.query(func.count(Problem.id)).filter(Problem.tenant_id == tenant_id).scalar() or 0
    return f"PRB-{n + 1:05d}"


def create_problem(
    db: Session,
    *,
    tenant_id: int,
    reporter: User,
    title: str,
    description: str,
    priority: TicketPriority = TicketPriority.medium,
) -> Problem:
    p = Problem(
        tenant_id=tenant_id,
        problem_number=next_problem_number(db, tenant_id),
        title=title.strip()[:255],
        description=description.strip(),
        priority=priority,
        status=ProblemStatus.investigating,
        reporter_id=reporter.id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def transition_status(db: Session, *, problem: Problem, new_status: ProblemStatus) -> Problem:
    if problem.status == new_status:
        return problem
    now = datetime.now(timezone.utc)
    problem.status = new_status
    if new_status == ProblemStatus.resolved and problem.resolved_at is None:
        problem.resolved_at = now
    if new_status == ProblemStatus.closed and problem.closed_at is None:
        problem.closed_at = now
    problem.updated_at = now
    db.commit()
    db.refresh(problem)
    return problem


def link_ticket(db: Session, *, problem: Problem, ticket: Ticket, by: User | None) -> ProblemTicketLink:
    existing = (db.query(ProblemTicketLink)
                  .filter(ProblemTicketLink.problem_id == problem.id,
                          ProblemTicketLink.ticket_id == ticket.id)
                  .first())
    if existing:
        return existing
    link = ProblemTicketLink(
        problem_id=problem.id,
        ticket_id=ticket.id,
        linked_by_id=by.id if by else None,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def unlink_ticket(db: Session, *, problem_id: int, ticket_id: int) -> None:
    (db.query(ProblemTicketLink)
       .filter(ProblemTicketLink.problem_id == problem_id,
               ProblemTicketLink.ticket_id == ticket_id)
       .delete())
    db.commit()
