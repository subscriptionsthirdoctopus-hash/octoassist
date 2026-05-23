"""Link an OctoAssist endpoint (Agent) to the OctoAssist user who's signed
in on it, using the Windows-reported logged_in_user string from the agent's
check-in snapshot.

Windows' `Get-CimInstance Win32_ComputerSystem | Select UserName` returns
strings like `TEMAINDIA\\arun.d` (domain-joined), `LAPTOP01\\arun` (local),
or `arun.d@temaindia.com` on AAD-joined boxes. We normalise all three to a
plain username and match against the email local-part of synced users.

Sync timing: this runs on every check-in (every 2h slow cycle + on demand
via ↻ Refresh now), so when a different user signs in on a shared laptop,
the assignment follows them automatically. We never CLEAR an existing
link when the snapshot reports no logged-in user — "screen locked at the
moment" is a normal transient state and shouldn't unassign the laptop.
"""
from __future__ import annotations

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Agent, User

log = logging.getLogger("octoassist.asset_linker")


def _normalise_username(logged_in_user: str | None) -> str | None:
    """Reduce a Windows-style user string to a bare username.

    Examples
    --------
    >>> _normalise_username("TEMAINDIA\\arun.d")   -> "arun.d"
    >>> _normalise_username("arun.d@temaindia.com") -> "arun.d"
    >>> _normalise_username("arun.d")              -> "arun.d"
    >>> _normalise_username(None)                  -> None
    >>> _normalise_username("")                    -> None
    """
    if not logged_in_user:
        return None
    u = logged_in_user.strip()
    if not u:
        return None
    if "\\" in u:           # DOMAIN\username
        u = u.split("\\", 1)[1]
    if "@" in u:            # username@domain.tld
        u = u.split("@", 1)[0]
    u = u.strip().lower()
    return u or None


def link_agent_to_user(
    db: Session, *, agent: Agent, logged_in_user: str | None,
) -> User | None:
    """Match the agent to the OctoAssist User whose email starts with the
    normalised username, scoped to the agent's tenant. Returns the User the
    agent now points at, or None if no link was made / changed.

    Idempotent. Preserves the existing primary_user_id if no match is found
    (does not unassign).
    """
    username = _normalise_username(logged_in_user)
    if username is None:
        return None

    # Match by email local-part. Prefer Entra-synced users when ambiguous
    # (some tenants have stale duplicates); break ties with id for determinism.
    pattern = f"{username}@%"
    matches = (
        db.query(User)
          .filter(User.tenant_id == agent.tenant_id,
                  User.is_active.is_(True),
                  func.lower(User.email).like(pattern))
          .order_by(User.entra_oid.isnot(None).desc(), User.id)
          .all()
    )
    if not matches:
        log.debug("asset_linker: no user match for hostname=%s logged_in_user=%r normalised=%r",
                  agent.hostname, logged_in_user, username)
        return None

    user = matches[0]
    changed = False
    if agent.primary_user_id != user.id:
        agent.primary_user_id = user.id
        changed = True
    # Always refresh location — it can change in Entra and we want the
    # asset row to reflect the latest. Safe because location is a denorm.
    if (agent.location or None) != (user.location or None):
        agent.location = user.location
        changed = True

    if changed:
        log.info("asset_linker: linked agent id=%s host=%s to user id=%s email=%s loc=%r",
                 agent.id, agent.hostname, user.id, user.email, user.location)
    return user
