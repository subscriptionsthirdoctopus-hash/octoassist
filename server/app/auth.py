import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import Agent

bearer_scheme = HTTPBearer(auto_error=True)
basic_scheme = HTTPBasic()


def authenticate_agent(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Agent:
    token = creds.credentials
    agent = db.query(Agent).filter(Agent.agent_token == token).first()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token")
    return agent


def authenticate_admin(creds: HTTPBasicCredentials = Depends(basic_scheme)) -> str:
    user_ok = secrets.compare_digest(creds.username, settings.admin_username)
    pass_ok = secrets.compare_digest(creds.password, settings.admin_password)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return creds.username
