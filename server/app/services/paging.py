"""Page slicing for list views that already hold the full row set in memory.

The list views build and filter their rows in Python (fleet software, the
asset register, users), then hand the lot to a template. That was fine at a
few dozen rows; at 669 products or 407 users the page is hundreds of KB.
This slices the finished list — KPIs, charts and CSV exports keep using the
full set, only the table is paged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
from urllib.parse import urlencode

DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 2000


@dataclass
class Page:
    items: Sequence
    page: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))

    @property
    def start(self) -> int:
        return 0 if self.total == 0 else (self.page - 1) * self.per_page + 1

    @property
    def end(self) -> int:
        return min(self.page * self.per_page, self.total)

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    def window(self, radius: int = 2) -> list[int | None]:
        """Page numbers to render: first, last, and a band around the current
        page; None marks a gap. Never more than ~9 entries."""
        if self.pages <= 9:
            return list(range(1, self.pages + 1))
        keep = {1, self.pages} | {n for n in range(self.page - radius, self.page + radius + 1) if 1 <= n <= self.pages}
        out: list[int | None] = []
        for n in range(1, self.pages + 1):
            if n in keep:
                out.append(n)
            elif out and out[-1] is not None:
                out.append(None)
        return out


def clamp(page: int | None, per_page: int | None) -> tuple[int, int]:
    p = max(1, int(page or 1))
    pp = int(per_page or DEFAULT_PER_PAGE)
    pp = min(MAX_PER_PAGE, max(10, pp))
    return p, pp


def paginate(items: Sequence, page: int | None, per_page: int | None = None) -> Page:
    p, pp = clamp(page, per_page)
    total = len(items)
    pages = max(1, -(-total // pp))
    p = min(p, pages)  # a stale ?page= past the end shows the last page, not nothing
    return Page(items=items[(p - 1) * pp : p * pp], page=p, per_page=pp, total=total)


def page_url(request, param: str = "page", page: int | None = None, **extra) -> str:
    """Current path + query with `param` (and any extras) replaced. Registered
    as a template global so the pager macro can build links that keep the
    active filters."""
    params = dict(request.query_params)
    if page is not None:
        params[param] = str(page)
    for k, v in extra.items():
        if v is None:
            params.pop(k, None)
        else:
            params[k] = str(v)
    qs = urlencode(params)
    return f"{request.url.path}?{qs}" if qs else request.url.path
