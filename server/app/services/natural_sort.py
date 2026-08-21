"""Human-order sorting for asset identifiers.

The Asset Register sorted hostnames with a plain lexicographic comparison
(`ORDER BY hostname`), which orders digit runs by their first character rather
than their value:

    TEMA-PC-1, TEMA-PC-10, TEMA-PC-11, TEMA-PC-2, TEMA-PC-20, TEMA-PC-3

TEMA reconcile the register against a spreadsheet that is sorted the way a
person would sort it, so every comparison had to be done by eye. `key` produces
the ordering they expect:

    TEMA-PC-1, TEMA-PC-2, TEMA-PC-3, TEMA-PC-10, TEMA-PC-11, TEMA-PC-20

This is a *display* ordering only — nothing is renamed and no stored value is
rewritten. It deliberately does not try to parse asset tags into a scheme
(prefix + number), because the register holds names from three sources that do
not share one: agent-reported short names, Entra display names, and hostnames
typed by hand on the manual-add form.
"""
from __future__ import annotations

import re

# Split into digit and non-digit runs. Digits are compared as integers, the
# rest case-insensitively as text.
_CHUNK = re.compile(r"(\d+)")


def key(value: str | None) -> tuple:
    """Return a sort key that orders embedded numbers by value.

        key("TEMA-PC-2")  <  key("TEMA-PC-10")
        key("lap-01")     == ordering-equivalent to key("LAP-1")'s neighbours

    Each chunk becomes a `(0, number, "")` or `(1, 0, text)` pair. The leading
    flag keeps ints and strings in separate comparison lanes, so Python never
    has to compare an int to a str — which would raise TypeError on a mixed
    pair like "PC-1" vs "PCA".

    A missing or empty name sorts last rather than first: those rows are the
    ones a reader is least able to act on, and floating them to the top of the
    register pushes the real endpoints out of view.
    """
    s = (value or "").strip()
    if not s:
        return ((2, 0, ""),)
    parts = []
    for chunk in _CHUNK.split(s):
        if not chunk:
            continue
        if chunk.isdigit():
            # int() drops leading zeros, which is what we want: "PC-007" and
            # "PC-7" name the same position in a numeric sequence.
            parts.append((0, int(chunk), ""))
        else:
            parts.append((1, 0, chunk.lower()))
    return tuple(parts)
