"""SLA computation.

For Phase 2 we keep this simple: SLA targets come from the category, then are
adjusted per priority via a multiplier. We now support custom date-based
holidays and automatic weekend skipping!
"""
from datetime import datetime, timedelta, timezone
import zoneinfo
from sqlalchemy.orm import Session

from ..models import Category, TicketPriority, Holiday


# Multipliers applied to the category's stated SLA (response & resolution).
# Higher priority = tighter SLA = smaller multiplier.
_PRIORITY_MULTIPLIER: dict[TicketPriority, float] = {
    TicketPriority.critical: 0.25,
    TicketPriority.high:     0.50,
    TicketPriority.medium:   1.00,
    TicketPriority.low:      2.00,
}


def _add_business_minutes(db: Session, tenant_id: int, start_time: datetime, minutes_to_add: int) -> datetime:
    # 1. Fetch holidays from database for this tenant
    holidays = {h.holiday_date for h in db.query(Holiday).filter(Holiday.tenant_id == tenant_id).all()}
    
    current_time = start_time
    remaining_minutes = minutes_to_add
    
    # India Standard Time (IST) zone for local date calculations
    kolkata = zoneinfo.ZoneInfo("Asia/Kolkata")
    
    from datetime import time
    
    step_minutes = 15
    while remaining_minutes > 0:
        current_time += timedelta(minutes=step_minutes)
        local_dt = current_time.astimezone(kolkata)
        current_date_local = local_dt.date()
        
        # Weekend (Saturday=5, Sunday=6) or database holiday
        if current_date_local.weekday() >= 5 or current_date_local in holidays:
            continue
            
        # Working hours are 9:00 AM (09:00) to 7:00 PM (19:00) local time (IST)
        local_time = local_dt.time()
        if local_time <= time(9, 0) or local_time > time(19, 0):
            continue
            
        remaining_minutes -= step_minutes
        
    return current_time


def compute_sla(db: Session, tenant_id: int, category: Category, priority: TicketPriority, created_at: datetime | None = None) -> tuple[datetime, datetime]:
    """Return (due_response_at, due_resolution_at) based on category + priority, skipping weekends and custom holidays."""
    base = created_at or datetime.now(timezone.utc)
    mult = _PRIORITY_MULTIPLIER.get(priority, 1.0)
    
    response_minutes = max(1, int(round(category.sla_response_minutes * mult)))
    resolution_minutes = max(1, int(round(category.sla_resolution_minutes * mult)))
    
    due_response = _add_business_minutes(db, tenant_id, base, response_minutes)
    due_resolution = _add_business_minutes(db, tenant_id, base, resolution_minutes)
    
    return due_response, due_resolution


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
