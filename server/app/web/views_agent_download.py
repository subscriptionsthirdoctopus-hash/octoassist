"""Public-ish download endpoints for the cross-platform Python agent.

The files are baked into the image at /srv/octoassist/agent_py/ at build
time. Staff users get a download index at /agent; the actual files are
served at /agent/files/<name>.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_staff
from ..database import get_db
from ..models import Tenant, User

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Where the image bundles the agent files (Dockerfile copies agent-py → /srv/agent_py)
AGENT_DIR = Path("/srv/agent_py")

ALLOWED_FILES = {
    "octoassist_agent.py":   "text/x-python",
    "install-linux.sh":      "text/x-shellscript",
    "install-windows.ps1":   "text/x-powershell",
    "README.md":             "text/markdown",
}

router = APIRouter(tags=["agent-download"])


@router.get("/agent", response_class=HTMLResponse)
def agent_index(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    tenant = db.query(Tenant).first()
    server_url = str(request.base_url).rstrip("/")
    return templates.TemplateResponse(
        request=request, name="agent_download.html",
        context={
            "current_user": user, "tenant": tenant,
            "server_url": server_url,
            "enrolment_key": tenant.enrolment_key if tenant else "",
            "files": list(ALLOWED_FILES.keys()),
        },
    )


@router.get("/agent/files/{filename}")
def agent_file(filename: str, _: User = Depends(require_staff)):
    if filename not in ALLOWED_FILES:
        raise HTTPException(status_code=404)
    path = AGENT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not bundled with this image")
    return FileResponse(
        str(path),
        media_type=ALLOWED_FILES[filename],
        filename=filename,
    )
