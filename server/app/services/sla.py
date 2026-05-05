"""SLA computation.

For Phase 2 we keep this simple: SLA targets come from the category, then are
adjusted per priority via a multiplier. Business-hours calendars are out of
scope; we use wall-clock minutes from creation time.
"""
from datetime import datetime, timedelta, timezone

from ..models import Category, TicketPriority


# Multipliers applied to the category's stated SLA (response & resolution).
# Higher priority = tighter SLA = smaller multiplier.
_PRIORITY_MULTIPLIER: dict[TicketPriority, float] = {
    TicketPriority.critical: 0.25,
    TicketPriority.high:     0.50,
    TicketPriority.medium:   1.00,
    TicketPriority.low:      2.00,
}


def compute_sla(category: Category, priority: TicketPriority, created_at: datetime | None = None) -> tuple[datetime, datetime]:
    """Return (due_response_at, due_resolution_at) based on category + priority."""
    base = created_at or datetime.now(timezone.utc)
    mult = _PRIORITY_MULTIPLIER.get(priority, 1.0)
    response_minutes = max(1, int(round(category.sla_response_minutes * mult)))
    resolution_minutes = max(1, int(round(category.sla_resolution_minutes * mult)))
    return (
        base + timedelta(minutes=response_minutes),
        base + timedelta(minutes=resolution_minutes),
    )


def time_to_breach(due: datetime | None, now: datetime | None = None) -> tuple[str, bool]:
    """Format the time until a deadline. Returns (label, is_breached)."""
    if due is None:
        return ("—", False)
    now = now or datetime.now(timezone.utc)
    delta = due - now
    breached = delta.total_seconds() < 0
    abs_secs = int(abs(delta.total_seconds()))
    days, rem = divmod(abs_secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days >= 1:
        text = f"{days}d {hours}h"
    elif hours >= 1:
        text = f"{hours}h {minutes}m"
    else:
        text = f"{minutes}m"
    return (f"-{text}" if breached else text, breached)
