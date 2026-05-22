"""Global search — one search box, all the things.

Searches across:
    tickets   (ticket_number, title, description)
    problems  (problem_number, title, description)
    changes   (change_number, title, description)
    assets    (hostname, machine_id, primary user's name/email)
    users     (email, full_name)
    kb        (title, summary)

Every query is tenant-scoped. The dropdown shows the top 5 per category;
the full HTML page at /search shows up to 30 per category. Match is
case-insensitive substring (Postgres ILIKE) — cheap, no FTS index needed
at the current data volumes; can be promoted to PG full-text later.
"""
from __future__ import annotations

from typing import TypedDict
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from ..models import (
    Agent, Change, KbArticle, KbArticleStatus, Problem,
    Ticket, User,
)


class SearchHit(TypedDict):
    kind: str         # 'ticket' / 'problem' / 'change' / 'asset' / 'user' / 'kb'
    title: str        # primary line shown in the dropdown
    subtitle: str     # secondary line (category, status, etc.)
    url: str          # where clicking the row navigates
    badge: str | None # optional label shown on the right (status pill text)


def search_all(
    db: Session,
    *,
    tenant_id: int,
    q: str,
    per_kind_limit: int = 5,
) -> dict[str, list[SearchHit]]:
    """Run search across every kind. Returns dict keyed by kind."""
    needle = (q or "").strip()
    if not needle or len(needle) < 2:
        return {"tickets": [], "problems": [], "changes": [],
                "assets": [], "users": [], "kb": []}
    like = f"%{needle.lower()}%"
    fn = func.lower

    # Tickets
    t_rows = (db.query(Ticket)
                .filter(Ticket.tenant_id == tenant_id,
                        or_(fn(Ticket.ticket_number).like(like),
                            fn(Ticket.title).like(like),
                            fn(Ticket.description).like(like)))
                .order_by(Ticket.created_at.desc())
                .limit(per_kind_limit).all())
    tickets = [{
        "kind": "ticket",
        "title": f"{t.ticket_number} — {t.title}",
        "subtitle": f"{t.kind.value.replace('_',' ')} · {t.category.name if t.category else '—'} · {t.status.value.replace('_',' ')}",
        "url": f"/tickets/{t.id}",
        "badge": t.priority.value,
    } for t in t_rows]

    # Problems
    p_rows = (db.query(Problem)
                .filter(Problem.tenant_id == tenant_id,
                        or_(fn(Problem.problem_number).like(like),
                            fn(Problem.title).like(like),
                            fn(Problem.description).like(like)))
                .order_by(Problem.created_at.desc())
                .limit(per_kind_limit).all())
    problems = [{
        "kind": "problem",
        "title": f"{p.problem_number} — {p.title}",
        "subtitle": f"{p.status.value.replace('_',' ')} · {len(p.linked_tickets)} linked",
        "url": f"/problems/{p.id}",
        "badge": p.priority.value,
    } for p in p_rows]

    # Changes
    c_rows = (db.query(Change)
                .filter(Change.tenant_id == tenant_id,
                        or_(fn(Change.change_number).like(like),
                            fn(Change.title).like(like),
                            fn(Change.description).like(like)))
                .order_by(Change.created_at.desc())
                .limit(per_kind_limit).all())
    changes = [{
        "kind": "change",
        "title": f"{c.change_number} — {c.title}",
        "subtitle": f"{c.change_type.value} · {c.risk.value} risk · {c.status.value.replace('_',' ')}",
        "url": f"/changes/{c.id}",
        "badge": c.risk.value,
    } for c in c_rows]

    # Assets (agents)
    a_rows = (db.query(Agent)
                .filter(Agent.tenant_id == tenant_id,
                        or_(fn(Agent.hostname).like(like),
                            fn(Agent.machine_id).like(like)))
                .order_by(Agent.hostname)
                .limit(per_kind_limit).all())
    # Also match on assigned-user name/email (separate join to keep query simple)
    a_user_rows = (db.query(Agent).join(User, User.id == Agent.primary_user_id)
                     .filter(Agent.tenant_id == tenant_id,
                             or_(fn(User.email).like(like),
                                 fn(User.full_name).like(like)))
                     .limit(per_kind_limit).all())
    # Merge, de-dupe, cap
    seen_ids: set[int] = set()
    asset_combined = []
    for a in list(a_rows) + list(a_user_rows):
        if a.id in seen_ids: continue
        seen_ids.add(a.id)
        asset_combined.append(a)
        if len(asset_combined) >= per_kind_limit: break
    assets = [{
        "kind": "asset",
        "title": a.hostname,
        "subtitle": (
            (a.primary_user.display_name if a.primary_user else "unassigned")
            + (" · " + (a.location or "") if a.location else "")
        ),
        "url": f"/asset/{a.id}",
        "badge": None,
    } for a in asset_combined]

    # Users
    u_rows = (db.query(User)
                .filter(User.tenant_id == tenant_id,
                        or_(fn(User.email).like(like),
                            fn(User.full_name).like(like)))
                .order_by(User.full_name, User.email)
                .limit(per_kind_limit).all())
    users = [{
        "kind": "user",
        "title": u.display_name,
        "subtitle": f"{u.email} · {u.role.value}",
        "url": f"/users",  # No per-user page yet; jumps to users list
        "badge": u.role.value,
    } for u in u_rows]

    # KB articles (published only, both portal + internal)
    kb_rows = (db.query(KbArticle)
                 .filter(KbArticle.tenant_id == tenant_id,
                         KbArticle.status == KbArticleStatus.published,
                         or_(fn(KbArticle.title).like(like),
                             fn(KbArticle.summary).like(like)))
                 .order_by(KbArticle.updated_at.desc())
                 .limit(per_kind_limit).all())
    kb = [{
        "kind": "kb",
        "title": k.title,
        "subtitle": (k.summary or "")[:100] + ("…" if k.summary and len(k.summary) > 100 else ""),
        "url": f"/kb/{k.id}",
        "badge": k.visibility.value,
    } for k in kb_rows]

    return {
        "tickets": tickets, "problems": problems, "changes": changes,
        "assets": assets, "users": users, "kb": kb,
    }


def hit_count(results: dict[str, list]) -> int:
    return sum(len(v) for v in results.values())
