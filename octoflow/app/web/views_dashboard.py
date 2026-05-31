"""Adaptive /dashboard — the post-login landing page.

Accepts:
  · ?view=week|month   — default 'week'
  · ?anchor=YYYY-MM-DD — date the chosen window is built around (default today)
"""
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_user
from ..database import get_db
from ..models import User
from ..services import dashboard as dash_svc

router = APIRouter(tags=["dashboard"])

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# Replace the home redirect — / now lands on /dashboard, not /timesheet
@router.get("/", response_class=HTMLResponse)
def home(user: User = Depends(require_user)):
    return RedirectResponse(url="/dashboard", status_code=303)


def _parse_anchor(v: str | None) -> date:
    if not v: return date.today()
    try:    return date.fromisoformat(v)
    except ValueError: return date.today()


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request,
              view: str = "week",
              anchor: str | None = None,
              user: User = Depends(require_user),
              db: Session = Depends(get_db)):
    view_norm = "month" if view == "month" else "week"
    anchor_d  = _parse_anchor(anchor)
    data = dash_svc.compute(db, user, anchor=anchor_d, view=view_norm)
    return templates.TemplateResponse(
        request=request, name="dashboard.html",
        context={"current_user": user, "data": data,
                 "today_iso": date.today().isoformat()},
    )
