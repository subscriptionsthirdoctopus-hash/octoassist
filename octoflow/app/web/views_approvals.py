"""Approvals — manager inbox + per-sheet review + approve/reject + bulk approve.

Permission rule (see services.timesheet.can_approve):
  · Admins can approve any submitted sheet in their tenant.
  · Managers can approve a sheet whose owner reports to them
    (User.manager_id == viewer.id).
"""
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_user
from ..database import get_db
from ..models import Timesheet, TimesheetStatus, User
from ..services import timesheet as ts_svc

router = APIRouter(tags=["approvals"])

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# ─────────────────────────────── /approvals (inbox) ──────────────────

@router.get("/approvals", response_class=HTMLResponse)
def approvals_inbox(
    request: Request, flash: str | None = None,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    pending = ts_svc.pending_for_approver(db, user)

    # Hydrate per-sheet totals for the inbox table
    rows = []
    for s in pending:
        rows.append({
            "sheet": s,
            "total":    ts_svc.total_hours(s),
            "billable": ts_svc.billable_hours(s),
            "entries":  len(s.entries),
        })

    return templates.TemplateResponse(
        request=request, name="approvals_inbox.html",
        context={"current_user": user, "rows": rows, "flash": flash},
    )


# ─────────────────────────────── /approvals/{sheet_id} (review) ──────

@router.get("/approvals/{sheet_id}", response_class=HTMLResponse)
def approvals_review(
    request: Request, sheet_id: int, flash: str | None = None,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    sheet = db.get(Timesheet, sheet_id)
    if not sheet or not ts_svc.can_approve(user, sheet):
        raise HTTPException(404)

    days = ts_svc.day_columns(sheet.period)
    grid = ts_svc.entries_grid(sheet)
    return templates.TemplateResponse(
        request=request, name="approvals_review.html",
        context={
            "current_user":  user,
            "sheet":         sheet, "period": sheet.period, "days": days,
            "grid":          grid,
            "totals_by_day": ts_svc.totals_by_day(sheet),
            "total_hours":   ts_svc.total_hours(sheet),
            "billable_hours":ts_svc.billable_hours(sheet),
            "flash":         flash,
            "can_act":       sheet.status == TimesheetStatus.submitted,
        },
    )


# ─────────────────────────────── Actions ─────────────────────────────

@router.post("/approvals/{sheet_id}/approve")
def approvals_approve(
    sheet_id: int,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    sheet = db.get(Timesheet, sheet_id)
    if not sheet or not ts_svc.can_approve(user, sheet):
        raise HTTPException(404)
    if sheet.status != TimesheetStatus.submitted:
        return RedirectResponse(url=f"/approvals?flash={quote('Sheet is no longer pending')}", status_code=303)
    ts_svc.approve(db, sheet, user)
    return RedirectResponse(
        url=f"/approvals?flash={quote(f'Approved · {sheet.user.display_name} · week of {sheet.period.start_date.isoformat()}')}",
        status_code=303,
    )


@router.post("/approvals/{sheet_id}/reject")
def approvals_reject(
    sheet_id: int,
    reason: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    sheet = db.get(Timesheet, sheet_id)
    if not sheet or not ts_svc.can_approve(user, sheet):
        raise HTTPException(404)
    if sheet.status != TimesheetStatus.submitted:
        return RedirectResponse(url=f"/approvals?flash={quote('Sheet is no longer pending')}", status_code=303)
    reason = (reason or "").strip()
    if not reason:
        return RedirectResponse(
            url=f"/approvals/{sheet_id}?flash={quote('A reason is required to reject')}",
            status_code=303,
        )
    ts_svc.reject(db, sheet, user, reason)
    return RedirectResponse(
        url=f"/approvals?flash={quote(f'Rejected · {sheet.user.display_name} · reason recorded')}",
        status_code=303,
    )


@router.post("/approvals/bulk-approve")
async def approvals_bulk_approve(
    request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    """TS-230 — approve every ticked sheet from the inbox in one go."""
    form = await request.form()
    ids = [int(v) for k, v in form.multi_items() if k == "sheet_id" and str(v).isdigit()]
    if not ids:
        return RedirectResponse(url=f"/approvals?flash={quote('No timesheets selected')}", status_code=303)

    approved = 0
    skipped  = 0
    for sid in ids:
        sheet = db.get(Timesheet, sid)
        if not sheet or not ts_svc.can_approve(user, sheet):
            skipped += 1
            continue
        if sheet.status != TimesheetStatus.submitted:
            skipped += 1
            continue
        ts_svc.approve(db, sheet, user)
        approved += 1

    msg = f"Bulk approved {approved} timesheet{'' if approved == 1 else 's'}"
    if skipped:
        msg += f" · {skipped} skipped (already processed or not yours)"
    return RedirectResponse(url=f"/approvals?flash={quote(msg)}", status_code=303)
