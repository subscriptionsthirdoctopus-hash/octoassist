"""Phase C: location-based ticket routing.

Two responsibilities:

1. `derive_location_for(user, db)` — figure out what location to stamp on a
   new ticket given the reporter. Tries (in order): the location of any
   OctoAssist agent that lists this user as primary_user, then the user's
   own User.location. None if neither is set.

2. `auto_assignee_for(location, tenant_id, db)` — look up the LocationRule
   matching the location string (case-insensitive) and return the User to
   assign to, or None.

Both helpers are best-effort and never raise on lookup failure.
"""
from __future__ import annotations

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Agent, CategoryRule, LocationRule, User, UserRole

log = logging.getLogger("octoassist.location_routing")

# Auto-assignment may only target staff who can actually work a ticket.
_ASSIGNABLE_ROLES = (UserRole.admin, UserRole.agent)


def _assignable(assignee: User | None) -> User | None:
    """Return `assignee` only if they can actually be given a ticket: active
    staff (admin/agent). A requester must never be auto-assigned — they cannot
    work the queue, and the ticket would look owned while nobody is on it.
    The settings UI enforces this when a rule is created, but a rule outlives
    the role it was created against (e.g. an agent later becomes a requester),
    so it has to be re-checked at assignment time.
    """
    if assignee is None or not assignee.is_active:
        return None
    if assignee.role not in _ASSIGNABLE_ROLES:
        return None
    return assignee


def derive_location_for(*, user: User, db: Session) -> str | None:
    """Pick the best location string for a new ticket reported by `user`.

    Order of preference:
      1. The location of the agent (laptop) where this user is the primary
         user, if any. Asset-level location wins so a temporarily-relocated
         user (e.g. on assignment in another office) gets correct routing.
      2. The user's own profile location (User.location from Entra sync).
      3. None — caller leaves Ticket.location NULL.
    """
    try:
        agent_loc = (db.query(Agent.location)
                       .filter(Agent.primary_user_id == user.id,
                               Agent.location.is_not(None))
                       .order_by(Agent.last_seen_at.desc().nulls_last())
                       .limit(1).scalar())
        if agent_loc:
            return agent_loc
    except Exception:  # noqa: BLE001
        pass
    return user.location or None


def auto_assignee_for(
    *, location: str | None, tenant_id: int, db: Session,
) -> User | None:
    """Return the LocationRule.default_assignee whose location matches
    `location` (case-insensitive, exact match), scoped to tenant. None if
    no rule matches or the location is blank.
    """
    if not location or not location.strip():
        return None
    # Trim BOTH sides: the stored rule may carry stray whitespace from an
    # earlier free-text entry, which would otherwise silently never match.
    rule = (db.query(LocationRule)
              .filter(LocationRule.tenant_id == tenant_id,
                      func.lower(func.trim(LocationRule.location))
                      == location.strip().lower())
              .first())
    if rule is None:
        return None
    return _assignable(rule.default_assignee)


def category_assignee_for(
    *, category_id: int | None, tenant_id: int, db: Session,
) -> User | None:
    """Phase F: look up a CategoryRule for this category, return assignee.
    Called as a fallback by services.ticketing.create_ticket when location
    routing didn't match."""
    if category_id is None:
        return None
    rule = (db.query(CategoryRule)
              .filter(CategoryRule.tenant_id == tenant_id,
                      CategoryRule.category_id == category_id).first())
    if rule is None:
        return None
    return _assignable(rule.default_assignee)
