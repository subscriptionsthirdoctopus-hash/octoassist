"""Out-of-pocket expense lifecycle + permission helpers.

States:        draft → submitted → approved → reimbursed
                             ↘ rejected (→ back to draft)

Permission model (mirrors timesheet approvals):
  · Owner: edit / submit / recall / delete their own draft or rejected.
  · Manager: approve / reject expenses from their direct reports.
  · Admin:   approve / reject anyone in tenant + mark reimbursed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models import AuditLog, Expense, ExpenseStatus, User, UserRole


RECEIPTS_DIR = Path(settings.receipts_dir)
MAX_BYTES    = settings.receipt_max_mb * 1024 * 1024
ALLOWED_MIME = {
    "image/jpeg": ".jpg",
    "image/png":  ".png",
    "image/webp": ".webp",
    "image/gif":  ".gif",
    "application/pdf": ".pdf",
}


# ─── Permission helpers ─────────────────────────────────────────────

def can_view(viewer: User, exp: Expense) -> bool:
    if exp.tenant_id != viewer.tenant_id:
        return False
    if exp.user_id == viewer.id:               return True
    if viewer.role == UserRole.admin:          return True
    if viewer.role == UserRole.manager and \
       exp.user is not None and \
       exp.user.manager_id == viewer.id:       return True
    return False


def can_edit(viewer: User, exp: Expense) -> bool:
    return (exp.user_id == viewer.id and
            exp.status in (ExpenseStatus.draft, ExpenseStatus.rejected))


def can_submit(viewer: User, exp: Expense) -> bool:
    return can_edit(viewer, exp)


def can_recall(viewer: User, exp: Expense) -> bool:
    return exp.user_id == viewer.id and exp.status == ExpenseStatus.submitted


def can_delete(viewer: User, exp: Expense) -> bool:
    # owner deletes own draft/rejected; admin can also delete (cleanup)
    if viewer.role == UserRole.admin and exp.tenant_id == viewer.tenant_id:
        return True
    return can_edit(viewer, exp)


def can_approve(viewer: User, exp: Expense) -> bool:
    if exp.tenant_id != viewer.tenant_id:                  return False
    if viewer.role == UserRole.admin:                       return True
    return (viewer.role == UserRole.manager and
            exp.user is not None and
            exp.user.manager_id == viewer.id)


def can_mark_reimbursed(viewer: User, exp: Expense) -> bool:
    return viewer.role == UserRole.admin and exp.tenant_id == viewer.tenant_id


# ─── Approval-queue query ───────────────────────────────────────────

def pending_for_approver(db: Session, viewer: User) -> list[Expense]:
    q = (db.query(Expense)
           .join(User, User.id == Expense.user_id)
           .filter(Expense.tenant_id == viewer.tenant_id,
                   Expense.status == ExpenseStatus.submitted))
    if viewer.role != UserRole.admin:
        q = q.filter(User.manager_id == viewer.id)
    return q.order_by(Expense.submitted_at.asc()).all()


def awaiting_reimbursement(db: Session, viewer: User) -> list[Expense]:
    return (db.query(Expense)
              .filter(Expense.tenant_id == viewer.tenant_id,
                      Expense.status == ExpenseStatus.approved)
              .order_by(Expense.approved_at.asc()).all())


# ─── State transitions ──────────────────────────────────────────────

def _log(db: Session, viewer: User, action: str, exp: Expense, detail: str = ""):
    db.add(AuditLog(tenant_id=viewer.tenant_id, actor_id=viewer.id,
                    action=action, resource=f"expense:{exp.id}", detail=detail))


def submit(db: Session, exp: Expense, viewer: User) -> None:
    exp.status = ExpenseStatus.submitted
    exp.submitted_at = datetime.now(timezone.utc)
    exp.rejection_reason = None
    _log(db, viewer, "expense.submit", exp)
    db.commit()


def recall(db: Session, exp: Expense, viewer: User) -> None:
    exp.status = ExpenseStatus.draft
    exp.submitted_at = None
    _log(db, viewer, "expense.recall", exp)
    db.commit()


def approve(db: Session, exp: Expense, viewer: User) -> None:
    exp.status = ExpenseStatus.approved
    exp.approver_id = viewer.id
    exp.approved_at = datetime.now(timezone.utc)
    _log(db, viewer, "expense.approve", exp)
    db.commit()


def reject(db: Session, exp: Expense, viewer: User, reason: str) -> None:
    exp.status = ExpenseStatus.rejected
    exp.rejection_reason = reason
    _log(db, viewer, "expense.reject", exp, detail=reason)
    db.commit()


def mark_reimbursed(db: Session, exp: Expense, viewer: User) -> None:
    exp.status = ExpenseStatus.reimbursed
    exp.reimbursed_at = datetime.now(timezone.utc)
    exp.reimbursed_by_id = viewer.id
    _log(db, viewer, "expense.reimburse", exp)
    db.commit()


# ─── Receipt file storage ───────────────────────────────────────────

def receipt_target(exp: Expense, ext: str) -> tuple[Path, str]:
    """Return (absolute path, relative path stored in DB) for a receipt."""
    rel = Path(str(exp.tenant_id)) / str(exp.id) / f"receipt{ext}"
    abs_ = RECEIPTS_DIR / rel
    return abs_, str(rel)


def receipt_absolute(exp: Expense) -> Path | None:
    if not exp.receipt_path:
        return None
    return RECEIPTS_DIR / exp.receipt_path


def save_receipt(exp: Expense, content: bytes, mime: str, filename: str) -> None:
    """Persist receipt bytes to disk and mutate exp (caller commits)."""
    if mime not in ALLOWED_MIME:
        raise ValueError(f"Unsupported receipt type {mime!r} — allowed: {', '.join(ALLOWED_MIME)}")
    if len(content) > MAX_BYTES:
        raise ValueError(f"Receipt too large ({len(content)} bytes) — max {MAX_BYTES} bytes")
    ext = ALLOWED_MIME[mime]
    abs_path, rel = receipt_target(exp, ext)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(content)
    # Best effort cleanup if extension changed (old receipt of different type)
    if exp.receipt_path and exp.receipt_path != rel:
        old = RECEIPTS_DIR / exp.receipt_path
        try:
            old.unlink()
        except FileNotFoundError:
            pass
    exp.receipt_path     = rel
    exp.receipt_filename = filename
    exp.receipt_mime     = mime
