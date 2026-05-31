"""Stub pages for sections not yet built — Approvals, Reports, Settings.
Each renders a single 'coming next' card so the nav is fully wired up
even before the underlying features ship."""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..auth import require_user
from ..models import User

router = APIRouter(tags=["stubs"])

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _stub(request: Request, user: User, title: str, subtitle: str, body: str):
    return templates.TemplateResponse(
        request=request, name="stub.html",
        context={"current_user": user, "title": title, "subtitle": subtitle, "body": body},
    )


@router.get("/approvals", response_class=HTMLResponse)
def approvals(request: Request, user: User = Depends(require_user)):
    return _stub(request, user,
        title="Approvals",
        subtitle="The manager's pending-approvals inbox",
        body="A single inbox of all timesheets awaiting your approval, with Approve / Reject + comment per row. Backed by TS-119 to TS-126.")


@router.get("/reports", response_class=HTMLResponse)
def reports(request: Request, user: User = Depends(require_user)):
    return _stub(request, user,
        title="Reports",
        subtitle="Utilisation, billable vs non-billable, hours by client/project",
        body="The seven Phase 1 reports (TS-132 to TS-138): my timesheet history, submission compliance, hours by project, hours by client, billable split, basic utilisation, Excel/CSV export.")


# /settings is now served by views_settings (real admin surface).
