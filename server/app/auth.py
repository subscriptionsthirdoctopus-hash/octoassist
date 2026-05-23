from typing import Iterable

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import Agent, User, UserRole

bearer_scheme = HTTPBearer(auto_error=True)


# ---------------------------------------------------------------------------
# Agent (endpoint) auth — Bearer token, used by OctoAssistAgent.exe
# ---------------------------------------------------------------------------

def authenticate_agent(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Agent:
    token = creds.credentials
    agent = db.query(Agent).filter(Agent.agent_token == token).first()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token")
    return agent


# ---------------------------------------------------------------------------
# Human (browser) auth — cookie session set by /login
# ---------------------------------------------------------------------------

class NotAuthenticated(HTTPException):
    """Raised when a browser request needs to be redirected to /login."""
    def __init__(self) -> None:
        super().__init__(status_code=status.HTTP_307_TEMPORARY_REDIRECT, detail="Login required")


def current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id") if hasattr(request, "session") else None
    if not user_id:
        return None
    user = db.get(User, int(user_id))
    if user is None or not user.is_active:
        return None
    return user


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user_optional(request, db)
    if user is None:
        # Browser flow — let the global handler turn this into a /login redirect
        raise NotAuthenticated()
    return user


def require_roles(*roles: UserRole):
    """Return a FastAPI dependency that allows only the listed roles."""
    allowed = set(roles)

    def _dep(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient privileges")
        return user

    return _dep


# Convenience aliases
require_admin = require_roles(UserRole.admin)
require_staff = require_roles(UserRole.admin, UserRole.agent)
require_any   = require_roles(UserRole.admin, UserRole.agent, UserRole.requester)


def login_redirect_for(request: Request) -> RedirectResponse:
    """Build a 303 redirect to /login carrying the original path as ?next=…"""
    next_url = str(request.url.path)
    if request.url.query:
        next_url += "?" + request.url.query
    return RedirectResponse(url=f"/login?next={next_url}", status_code=303)
