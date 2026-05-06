"""Knowledge Base — slug generation, search, view-count increment."""
import re
from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import KbArticle, KbArticleStatus, KbArticleVisibility


def _slugify(text: str, *, max_len: int = 80) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", text or "").strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:max_len] or "article"


def unique_slug(db: Session, base_title: str, *, exclude_id: int | None = None) -> str:
    base = _slugify(base_title)
    candidate = base
    n = 2
    while True:
        q = db.query(KbArticle).filter(KbArticle.slug == candidate)
        if exclude_id is not None:
            q = q.filter(KbArticle.id != exclude_id)
        if q.first() is None:
            return candidate
        candidate = f"{base}-{n}"
        n += 1


def search(
    db: Session,
    *,
    tenant_id: int,
    q: str | None = None,
    category_id: int | None = None,
    portal_only: bool = False,
    limit: int = 100,
) -> list[KbArticle]:
    """Search published articles. Portal-only callers cannot see internal ones."""
    qy = (db.query(KbArticle)
            .filter(KbArticle.tenant_id == tenant_id,
                    KbArticle.status == KbArticleStatus.published))
    if portal_only:
        qy = qy.filter(KbArticle.visibility == KbArticleVisibility.portal)
    if category_id:
        qy = qy.filter(KbArticle.category_id == category_id)
    if q:
        like = f"%{q.strip()}%"
        qy = qy.filter(or_(
            KbArticle.title.ilike(like),
            KbArticle.summary.ilike(like),
            KbArticle.body.ilike(like),
        ))
    return qy.order_by(KbArticle.published_at.desc().nullslast(),
                       KbArticle.updated_at.desc()).limit(limit).all()


def bump_view(db: Session, article: KbArticle) -> None:
    article.view_count = (article.view_count or 0) + 1
    db.commit()


def transition_status(db: Session, *, article: KbArticle, new_status: KbArticleStatus) -> KbArticle:
    if article.status == new_status:
        return article
    article.status = new_status
    if new_status == KbArticleStatus.published and article.published_at is None:
        article.published_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(article)
    return article
