"""Throttle for the password login form.

Sliding-window counters of *failed* attempts, kept in memory. The app runs as a
single uvicorn process, so this needs no shared store; if it ever runs with
several workers each worker keeps its own count, which only makes the limit
more lenient, never lock anyone out early.

Two keys are tracked so neither attack shape slips through:
  - per client IP: one source trying many accounts;
  - per email:     many sources trying one account.
A successful login clears both, so a legitimate user who fumbles twice and
then gets it right is not carrying failures around.

The SSO path is not affected; only POST /login goes through here.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

WINDOW_SECONDS = 10 * 60
MAX_PER_EMAIL = 5
MAX_PER_IP = 10

_lock = threading.Lock()
_failures: dict[str, deque[float]] = defaultdict(deque)


def _prune(key: str, now: float) -> deque[float]:
    q = _failures[key]
    cutoff = now - WINDOW_SECONDS
    while q and q[0] < cutoff:
        q.popleft()
    if not q:
        _failures.pop(key, None)
    return q


def retry_after(ip: str, email: str, now: float | None = None) -> int:
    """Seconds until the caller may try again; 0 means not throttled."""
    now = now or time.time()
    with _lock:
        waits = []
        for key, limit in ((f"ip:{ip}", MAX_PER_IP), (f"email:{email}", MAX_PER_EMAIL)):
            q = _prune(key, now)
            if len(q) >= limit:
                waits.append(int(q[0] + WINDOW_SECONDS - now) + 1)
        return max(waits) if waits else 0


def record_failure(ip: str, email: str, now: float | None = None) -> None:
    now = now or time.time()
    with _lock:
        for key in (f"ip:{ip}", f"email:{email}"):
            _prune(key, now)
            _failures[key].append(now)


def clear(ip: str, email: str) -> None:
    with _lock:
        _failures.pop(f"ip:{ip}", None)
        _failures.pop(f"email:{email}", None)


def client_ip(request) -> str:
    """The address nginx saw. X-Real-IP is set by the reverse proxy from the
    socket peer; the app port is bound to loopback, so nothing else can reach
    this process to spoof it. Fall back to the socket for direct access."""
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
