"""Change Management — number assignment, CAB approval workflow, audit log."""
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (
    Change, ChangeEvent, ChangeEventKind, ChangeRisk, ChangeStatus, ChangeType, User,
)
from . import notifications


def next_change_number(db: Session, tenant_id: int) -> str:
    import re
    rows = db.query(Change.change_number).filter(Change.tenant_id == tenant_id).all()
    max_num = 0
    for (cn,) in rows:
        m = re.match(r"CHG-(\d+)", cn)
        if m:
            num = int(m.group(1))
            if num > max_num:
                max_num = num
    return f"CHG-{max_num + 1:05d}"



def _record(
    db: Session, *, change: Change, actor: User | None, kind: ChangeEventKind,
    note: str | None = None, before: dict | None = None, after: dict | None = None,
) -> None:
    ev = ChangeEvent(
        change_id=change.id,
        actor_id=actor.id if actor else None,
        kind=kind, note=note, before_value=before, after_value=after,
        created_at=datetime.now(timezone.utc),
    )
    db.add(ev)


def create_change(
    db: Session,
    *,
    tenant_id: int,
    requester: User,
    title: str,
    description: str = "",
    change_type: ChangeType = ChangeType.normal,
    risk: ChangeRisk = ChangeRisk.medium,
    rollback_plan: str = "",
    impact: str = "",
    planned_start: datetime | None = None,
    planned_end: datetime | None = None,
) -> Change:
    c = Change(
        tenant_id=tenant_id,
        change_number=next_change_number(db, tenant_id),
        title=title.strip()[:255],
        description=description.strip(),
        change_type=change_type,
        risk=risk,
        status=ChangeStatus.draft,
        rollback_plan=rollback_plan.strip(),
        impact=impact.strip(),
        planned_start=planned_start,
        planned_end=planned_end,
        requester_id=requester.id,
    )
    db.add(c)
    db.flush()
    _record(db, change=c, actor=requester, kind=ChangeEventKind.created, after={
        "change_number": c.change_number,
        "title": c.title,
        "type": c.change_type.value,
        "risk": c.risk.value,
    })
    db.commit()
    db.refresh(c)
    return c


def submit_for_review(db: Session, *, change: Change, actor: User) -> Change:
    if change.status != ChangeStatus.draft:
        return change
    change.status = ChangeStatus.under_review
    change.updated_at = datetime.now(timezone.utc)
    _record(db, change=change, actor=actor, kind=ChangeEventKind.submitted_for_review)
    
    # Auto-create pending CAB votes for Change Management committee members
    from ..models import CabVote, CabCommittee, CabCommitteeMember
    committee = db.query(CabCommittee).filter(
        CabCommittee.tenant_id == change.tenant_id,
        CabCommittee.name == "Change Management"
    ).first()
    
    # Clear existing votes first (for safety/idempotency)
    db.query(CabVote).filter(CabVote.change_id == change.id).delete()
    
    if committee:
        members = db.query(User).join(
            CabCommitteeMember, User.id == CabCommitteeMember.user_id
        ).filter(
            CabCommitteeMember.committee_id == committee.id,
            User.is_active == True
        ).all()
        for member in members:
            db.add(CabVote(change_id=change.id, user_id=member.id, approve=None))
        
    db.commit()
    db.refresh(change)
    try:
        notifications.change_submitted(db, change)
    except Exception:
        pass
    return change


def approve(db: Session, *, change: Change, actor: User, note: str = "") -> Change:
    old = change.status.value
    change.status = ChangeStatus.approved
    change.cab_approver_id = actor.id
    change.cab_decision_at = datetime.now(timezone.utc)
    change.cab_decision_note = note.strip()
    change.updated_at = change.cab_decision_at
    _record(db, change=change, actor=actor, kind=ChangeEventKind.approved, note=note or None)
    db.commit()
    db.refresh(change)
    try:
        notifications.change_status_changed(db, change, old, actor, note)
    except Exception:
        pass
    # Audit log
    from .audit import log_admin_action
    try:
        log_admin_action(db, tenant_id=change.tenant_id, user=actor, action="approve_change",
                         details=f"Approved Change Request {change.change_number} ('{change.title}'). Note: {note}")
    except Exception:
        pass
    return change


def reject(db: Session, *, change: Change, actor: User, note: str = "") -> Change:
    old = change.status.value
    change.status = ChangeStatus.rejected
    change.cab_approver_id = actor.id
    change.cab_decision_at = datetime.now(timezone.utc)
    change.cab_decision_note = note.strip()
    change.updated_at = change.cab_decision_at
    _record(db, change=change, actor=actor, kind=ChangeEventKind.rejected, note=note or None)
    db.commit()
    db.refresh(change)
    try:
        notifications.change_status_changed(db, change, old, actor, note)
    except Exception:
        pass
    # Audit log
    from .audit import log_admin_action
    try:
        log_admin_action(db, tenant_id=change.tenant_id, user=actor, action="reject_change",
                         details=f"Rejected Change Request {change.change_number} ('{change.title}'). Note: {note}")
    except Exception:
        pass
    return change


def start_implementation(db: Session, *, change: Change, actor: User) -> Change:
    if change.status != ChangeStatus.approved:
        return change
    old = change.status.value
    change.status = ChangeStatus.in_progress
    change.actual_start = datetime.now(timezone.utc)
    change.updated_at = change.actual_start
    if change.implementer_id is None:
        change.implementer_id = actor.id
    _record(db, change=change, actor=actor, kind=ChangeEventKind.started)
    db.commit()
    db.refresh(change)
    try:
        notifications.change_status_changed(db, change, old, actor)
    except Exception:
        pass
    # Audit log
    from .audit import log_admin_action
    try:
        log_admin_action(db, tenant_id=change.tenant_id, user=actor, action="start_change_implementation",
                         details=f"Started implementation for Change Request {change.change_number} ('{change.title}').")
    except Exception:
        pass
    return change


def complete(db: Session, *, change: Change, actor: User, note: str = "") -> Change:
    old = change.status.value
    change.status = ChangeStatus.completed
    change.actual_end = datetime.now(timezone.utc)
    change.updated_at = change.actual_end
    _record(db, change=change, actor=actor, kind=ChangeEventKind.completed, note=note or None)
    db.commit()
    db.refresh(change)
    try:
        notifications.change_status_changed(db, change, old, actor, note)
    except Exception:
        pass
    # Audit log
    from .audit import log_admin_action
    try:
        log_admin_action(db, tenant_id=change.tenant_id, user=actor, action="complete_change",
                         details=f"Completed Change Request {change.change_number} ('{change.title}'). Note: {note}")
    except Exception:
        pass
    return change


def fail(db: Session, *, change: Change, actor: User, note: str = "") -> Change:
    old = change.status.value
    change.status = ChangeStatus.failed
    change.actual_end = datetime.now(timezone.utc)
    change.updated_at = change.actual_end
    _record(db, change=change, actor=actor, kind=ChangeEventKind.failed, note=note or None)
    db.commit()
    db.refresh(change)
    try:
        notifications.change_status_changed(db, change, old, actor, note)
    except Exception:
        pass
    # Audit log
    from .audit import log_admin_action
    try:
        log_admin_action(db, tenant_id=change.tenant_id, user=actor, action="fail_change",
                         details=f"Marked Change Request {change.change_number} ('{change.title}') as FAILED. Note: {note}")
    except Exception:
        pass
    return change


def cancel(db: Session, *, change: Change, actor: User, note: str = "") -> Change:
    old = change.status.value
    change.status = ChangeStatus.cancelled
    change.updated_at = datetime.now(timezone.utc)
    _record(db, change=change, actor=actor, kind=ChangeEventKind.cancelled, note=note or None)
    db.commit()
    db.refresh(change)
    try:
        notifications.change_status_changed(db, change, old, actor, note)
    except Exception:
        pass
    # Audit log
    from .audit import log_admin_action
    try:
        log_admin_action(db, tenant_id=change.tenant_id, user=actor, action="cancel_change",
                         details=f"Cancelled Change Request {change.change_number} ('{change.title}'). Note: {note}")
    except Exception:
        pass
    return change
