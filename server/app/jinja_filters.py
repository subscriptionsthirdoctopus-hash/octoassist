"""Jinja2 filters — timezone-aware display helpers.

The database stores every timestamp in UTC (`DateTime(timezone=True)`). Templates
should never show raw UTC to the user; they should always render in IST
(Asia/Kolkata, UTC+5:30) because that's where Third Octopus + every current
customer operates.

Filters
-------
    ist        — "YYYY-MM-DD HH:MM IST"
    istdate    — "YYYY-MM-DD" (the IST calendar date)
    isttime    — "HH:MM" (IST time only)
    ist_input  — "YYYY-MM-DDTHH:MM" for <input type="datetime-local"> prefill

Form-input helpers
------------------
    parse_ist_input(s) — read an IST-naive `<input type="datetime-local">`
                         value, return a UTC-aware datetime (or None).

Both naive and tz-aware datetimes are handled. Naive values are assumed to
already be in UTC (which matches how SQLAlchemy hydrates them on read).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.templating import Jinja2Templates


# Asia/Kolkata is a fixed UTC+5:30 offset year-round (no DST).
IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def _to_ist(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        # Naive — SQLAlchemy returns naive UTC datetimes by default.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST)


def ist(value: Any) -> str:
    # India-only deployment — IST is the implicit default everywhere user-
    # visible. No suffix on the rendered string (was 'YYYY-MM-DD HH:MM IST').
    dt = _to_ist(value)
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"


def istdate(value: Any) -> str:
    dt = _to_ist(value)
    return dt.strftime("%Y-%m-%d") if dt else "—"


def isttime(value: Any) -> str:
    dt = _to_ist(value)
    return dt.strftime("%H:%M") if dt else "—"


# Date shapes our collectors actually emit. Deliberately excludes dd/mm/yyyy
# and mm/dd/yyyy: those two are indistinguishable for days 1-12, so guessing
# would silently render a wrong date. Anything unrecognised falls through to
# the raw string instead.
_DATE_FORMATS = (
    "%Y-%m-%d",   # agent OS install_date (Win32_OperatingSystem, dpkg, macOS)
    "%Y%m%d",     # HKLM\...\Uninstall\*\InstallDate — Windows registry format
    "%Y/%m/%d",   # occasional vendor variant in the same registry key
)


def dmy(value: Any) -> str:
    """Render a date or ISO date string as dd/mm/yyyy (Indian / UK locale).

    This is the single date presentation used by both the Asset and the
    Software modules — the two report install dates from different sources
    (Win32_OperatingSystem vs the HKLM Uninstall keys, which use the
    undelimited yyyyMMdd form) and used to render them in different shapes.

    Accepts:
      - "yyyy-MM-dd" (the format the agent emits for OS install_date)
      - "yyyyMMdd"   (the format Windows writes into Uninstall\\*\\InstallDate)
      - full ISO timestamps — the date portion is used
      - datetime objects (any tz; date portion only)
      - empty / None / "" / "—" → "—"
    """
    if value is None or value == "":
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    s = str(value).strip()
    if not s or s == "—":
        return "—"
    # Full ISO timestamps ("2026-03-15T10:22:33" / "2026-03-15 10:22:33") —
    # keep the date portion only.
    head = s.replace("T", " ").split(" ", 1)[0]
    for fmt in _DATE_FORMATS:
        for candidate in (head, s):
            try:
                return datetime.strptime(candidate, fmt).strftime("%d/%m/%Y")
            except ValueError:
                continue
    try:
        return datetime.fromisoformat(s).strftime("%d/%m/%Y")
    except ValueError:
        return s  # last resort — show the raw string rather than hide it


def ist_input(value: Any) -> str:
    """Format for `<input type="datetime-local">` prefill (no seconds, no TZ)."""
    dt = _to_ist(value)
    return dt.strftime("%Y-%m-%dT%H:%M") if dt else ""


def parse_ist_input(s: str | None) -> datetime | None:
    """Parse an IST-naive datetime-local form value to a UTC-aware datetime.

    The user types wall-clock IST in the browser; we store UTC in the DB.
    """
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    try:
        naive = datetime.fromisoformat(s)
    except ValueError:
        return None
    if naive.tzinfo is None:
        naive = naive.replace(tzinfo=IST)
    return naive.astimezone(timezone.utc)


def install_on(templates: Jinja2Templates) -> Jinja2Templates:
    """Register all IST filters on a Jinja2Templates instance.

    Idempotent — re-registering overwrites the same callable.
    """
    env = templates.env
    env.filters["ist"]       = ist
    env.filters["istdate"]   = istdate
    env.filters["isttime"]   = isttime
    env.filters["ist_input"] = ist_input
    env.filters["dmy"]       = dmy

    # Register CSRF token generator global helper
    from .csrf import get_or_create_token
    env.globals["csrf_token"] = get_or_create_token
    return templates
