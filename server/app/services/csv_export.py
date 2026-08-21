"""One CSV response builder, shared by every view that offers an export.

Lifted out of views_reports when the Asset Register grew its own export. Two
modules building CSV responses independently is how exports end up disagreeing
about quoting, about the header row, or about the Content-Disposition that
decides whether a browser downloads the file or renders it as text.
"""
from __future__ import annotations

import csv
import io

from fastapi.responses import StreamingResponse


def stream(rows_iter, headers: list[str], filename: str) -> StreamingResponse:
    """Serialise `rows_iter` beneath `headers` as a downloadable CSV."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for row in rows_iter:
        w.writerow(row)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
