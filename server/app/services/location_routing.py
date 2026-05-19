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

from ..models import Agent, CategoryRule, LocationRule, User

log = logging.getLogger("octoassist.location_routing")


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
    if not location:
        return None
    rule = (db.query(LocationRule)
              .filter(LocationRule.tenant_id == tenant_id,
                      func.lower(LocationRule.location) == location.strip().lower())
              .first())
    if rule is None:
        return None
    # Sanity: only auto-assign to active staff (admin/agent).
    assignee = rule.default_assignee
    if assignee is None or not assignee.is_active:
        return None
    return assignee


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
    assignee = rule.default_assignee
    if assignee is None or not assignee.is_active:
        return None
    return assignee
