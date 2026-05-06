"""Knowledge Base — staff CRUD + end-user read paths.

Staff (admin / agent):
  /kb                  — list with filters
  /kb/new              — create form
  /kb/{id}             — view
  /kb/{id}/edit        — edit form
  /kb/{id}/delete      — delete
  /kb/{id}/publish     — toggle publish/draft
  /kb/categories       — manage categories

End-users (any logged-in user):
  /portal/kb           — list (portal-visibility, published only)
  /portal/kb/{slug}    — read
"""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import current_user, require_staff
from ..database import get_db
from ..models import (
    KbArticle, KbArticleStatus, KbArticleVisibility, KbCategory, Tenant, User,
)
from ..services.kb import bump_view, search, transition_status, unique_slug

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["kb"])


def _ctx(user: User, db: Session, **extra) -> dict:
    return {"current_user": user, "tenant": db.query(Tenant).first(), **extra}


# ---- Staff routes ---------------------------------------------------------

@router.get("/kb", response_class=HTMLResponse)
def kb_list(
    request: Request,
    q: str | None = None,
    category_id: int | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    qy = db.query(KbArticle).filter(KbArticle.tenant_id == user.tenant_id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        from sqlalchemy import or_
        qy = qy.filter(or_(
            KbArticle.title.ilike(like),
            KbArticle.summary.ilike(like),
            KbArticle.body.ilike(like),
        ))
    if category_id:
        qy = qy.filter(KbArticle.category_id == category_id)
    rows = qy.order_by(KbArticle.updated_at.desc()).limit(200).all()
    cats = (db.query(KbCategory)
              .filter(KbCategory.tenant_id == user.tenant_id)
              .order_by(KbCategory.sort_order, KbCategory.name).all())
    return templates.TemplateResponse(
        request=request, name="kb_list.html",
        context=_ctx(user, db, rows=rows, q=q or "", categories=cats, filter_category_id=category_id),
    )


@router.get("/kb/new", response_class=HTMLResponse)
def kb_new(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    cats = (db.query(KbCategory)
              .filter(KbCategory.tenant_id == user.tenant_id)
              .order_by(KbCategory.sort_order, KbCategory.name).all())
    return templates.TemplateResponse(
        request=request, name="kb_edit.html",
        context=_ctx(user, db, article=None, categories=cats,
                     statuses=[s.value for s in KbArticleStatus],
                     visibilities=[v.value for v in KbArticleVisibility]),
    )


@router.post("/kb/new")
def kb_create(
    title: str = Form(...),
    summary: str = Form(""),
    body: str = Form(""),
    category_id: str = Form(""),
    status: str = Form("draft"),
    visibility: str = Form("portal"),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    cat_id = int(category_id) if category_id.strip() else None
    art = KbArticle(
        tenant_id=user.tenant_id,
        category_id=cat_id,
        slug=unique_slug(db, title),
        title=title.strip()[:255],
        summary=summary.strip(),
        body=body,
        status=KbArticleStatus(status),
        visibility=KbArticleVisibility(visibility),
        author_id=user.id,
    )
    if art.status == KbArticleStatus.published:
        art.published_at = datetime.now(timezone.utc)
    db.add(art)
    db.commit()
    db.refresh(art)
    return RedirectResponse(url=f"/kb/{art.id}", status_code=303)


# NOTE: /kb/categories routes MUST be declared before /kb/{article_id} so the
# string "categories" doesn't get matched as an int article id (returns 422).
@router.get("/kb/categories", response_class=HTMLResponse)
def kb_categories(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(KbCategory)
              .filter(KbCategory.tenant_id == user.tenant_id)
              .order_by(KbCategory.sort_order, KbCategory.name).all())
    return templates.TemplateResponse(
        request=request, name="kb_categories.html",
        context=_ctx(user, db, rows=rows),
    )


@router.post("/kb/categories/new")
def kb_category_create(
    name: str = Form(...),
    description: str = Form(""),
    sort_order: int = Form(0),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    cat = KbCategory(
        tenant_id=user.tenant_id,
        name=name.strip()[:120],
        description=description.strip(),
        sort_order=sort_order,
    )
    db.add(cat)
    db.commit()
    return RedirectResponse(url="/kb/categories", status_code=303)


@router.post("/kb/categories/{category_id}/delete")
def kb_category_delete(
    category_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    cat = db.get(KbCategory, category_id)
    if cat is None or cat.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    db.delete(cat)
    db.commit()
    return RedirectResponse(url="/kb/categories", status_code=303)


@router.get("/kb/{article_id}", response_class=HTMLResponse)
def kb_detail(
    article_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    art = db.get(KbArticle, article_id)
    if art is None or art.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request, name="kb_detail.html",
        context=_ctx(user, db, article=art, portal_view=False),
    )


@router.get("/kb/{article_id}/edit", response_class=HTMLResponse)
def kb_edit_form(
    article_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    art = db.get(KbArticle, article_id)
    if art is None or art.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    cats = (db.query(KbCategory)
              .filter(KbCategory.tenant_id == user.tenant_id)
              .order_by(KbCategory.sort_order, KbCategory.name).all())
    return templates.TemplateResponse(
        request=request, name="kb_edit.html",
        context=_ctx(user, db, article=art, categories=cats,
                     statuses=[s.value for s in KbArticleStatus],
                     visibilities=[v.value for v in KbArticleVisibility]),
    )


@router.post("/kb/{article_id}/edit")
def kb_edit_save(
    article_id: int,
    title: str = Form(...),
    summary: str = Form(""),
    body: str = Form(""),
    category_id: str = Form(""),
    status: str = Form("draft"),
    visibility: str = Form("portal"),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    art = db.get(KbArticle, article_id)
    if art is None or art.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    cat_id = int(category_id) if category_id.strip() else None

    if title.strip() and title.strip() != art.title:
        art.slug = unique_slug(db, title, exclude_id=art.id)
    art.title = title.strip()[:255]
    art.summary = summary.strip()
    art.body = body
    art.category_id = cat_id
    art.visibility = KbArticleVisibility(visibility)
    new_status = KbArticleStatus(status)
    if new_status != art.status:
        transition_status(db, article=art, new_status=new_status)
    art.updated_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(url=f"/kb/{art.id}", status_code=303)


@router.post("/kb/{article_id}/publish")
def kb_publish_toggle(
    article_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    art = db.get(KbArticle, article_id)
    if art is None or art.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    new = (KbArticleStatus.draft
           if art.status == KbArticleStatus.published
           else KbArticleStatus.published)
    transition_status(db, article=art, new_status=new)
    return RedirectResponse(url=f"/kb/{art.id}", status_code=303)


@router.post("/kb/{article_id}/delete")
def kb_delete(
    article_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    art = db.get(KbArticle, article_id)
    if art is None or art.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    db.delete(art)
    db.commit()
    return RedirectResponse(url="/kb", status_code=303)


# ---- End-user portal routes ---------------------------------------------

@router.get("/portal/kb", response_class=HTMLResponse)
def portal_kb_list(
    request: Request,
    q: str | None = None,
    category_id: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = search(
        db, tenant_id=user.tenant_id, q=q, category_id=category_id,
        portal_only=True, limit=100,
    )
    cats = (db.query(KbCategory)
              .filter(KbCategory.tenant_id == user.tenant_id)
              .order_by(KbCategory.sort_order, KbCategory.name).all())
    return templates.TemplateResponse(
        request=request, name="portal_kb_list.html",
        context=_ctx(user, db, rows=rows, q=q or "", categories=cats,
                     filter_category_id=category_id),
    )


@router.get("/portal/kb/{slug}", response_class=HTMLResponse)
def portal_kb_read(
    slug: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    art = (db.query(KbArticle)
             .filter(KbArticle.tenant_id == user.tenant_id,
                     KbArticle.slug == slug,
                     KbArticle.status == KbArticleStatus.published,
                     KbArticle.visibility == KbArticleVisibility.portal)
             .first())
    if art is None:
        raise HTTPException(status_code=404)
    bump_view(db, art)
    return templates.TemplateResponse(
        request=request, name="kb_detail.html",
        context=_ctx(user, db, article=art, portal_view=True),
    )
