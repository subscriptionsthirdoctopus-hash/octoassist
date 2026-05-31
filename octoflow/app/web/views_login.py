"""Login / logout."""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..security import verify_password

router = APIRouter(tags=["auth"])

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None, next: str = "/"):
    return templates.TemplateResponse(
        request=request, name="login.html",
        context={"error": error, "next": next, "current_user": None},
    )


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if user is None or not user.is_active or not verify_password(password, user.password_hash or ""):
        return RedirectResponse(url=f"/login?error=Invalid+email+or+password&next={next}",
                                status_code=303)
    request.session["user_id"] = user.id
    return RedirectResponse(url=next or "/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
