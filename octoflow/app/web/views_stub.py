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


# /approvals is now served by views_approvals (real manager inbox).


# /reports is now served by views_reports (real reports cluster).


# /settings is now served by views_settings (real admin surface).
