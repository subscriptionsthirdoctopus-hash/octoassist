"""Global search — HTML page at /search?q=… and JSON at /api/v1/search?q=…

The HTML page is the "show me everything that matches" fallback when you
hit Enter from the topbar input. The JSON endpoint feeds the live
autocomplete dropdown that opens as you type.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import current_user
from ..database import get_db
from ..jinja_filters import install_on
from ..models import Tenant, User
from ..services import global_search as gs

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["search"])


@router.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = "",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Full-results page. Up to 30 hits per kind."""
    results = gs.search_all(db, tenant_id=user.tenant_id, q=q, per_kind_limit=30)
    total = gs.hit_count(results)
    tenant = db.query(Tenant).first()
    return templates.TemplateResponse(
        request=request, name="search.html",
        context={
            "current_user": user, "tenant": tenant,
            "q": q, "results": results, "total": total,
        },
    )


@router.get("/api/v1/search")
def search_json(
    q: str = "",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """JSON for the live autocomplete in the topbar. Top 5 per kind."""
    results = gs.search_all(db, tenant_id=user.tenant_id, q=q, per_kind_limit=5)
    return JSONResponse({
        "q": q,
        "total": gs.hit_count(results),
        "results": results,
    })
