"""Hostname normalisation for matching one endpoint across sources.

The same physical machine is reported by more than one source, and they do not
agree on spelling:

  - the OctoAssist agent sends whatever the OS reports — usually the short
    name, often uppercase: "LAP-FIN-021"
  - Microsoft Graph /devices reports displayName, which on domain-joined or
    Entra-joined machines is frequently the FQDN: "lap-fin-021.tema.local"

The Asset Register compared these case-insensitively but not FQDN-aware, so a
laptop with an agent installed still appeared a second time in the
"discovered, unmanaged" table — the same endpoint listed under two names.

`normalise` produces the key those comparisons should use. It is a *matching*
key only: nothing is renamed, no row is rewritten, and each source keeps
displaying the name it actually reported. That keeps the fix reversible and
avoids picking a canonical form (short name vs FQDN) — a decision that belongs
to the customer, not to a comparison helper.
"""
from __future__ import annotations

import re

# Dotted-quad IPv4. Splitting one on "." would collapse every address in a
# subnet to its first octet and match unrelated machines to each other.
_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def normalise(hostname: str | None) -> str:
    """Return the comparison key for a hostname.

    Trims, lowercases, drops a trailing root dot, then keeps the first DNS
    label so a short name and its FQDN collapse together.

        "LAP-FIN-021"                -> "lap-fin-021"
        "lap-fin-021.tema.local."    -> "lap-fin-021"
        "  LAP-FIN-021  "            -> "lap-fin-021"
        "10.0.0.5"                   -> "10.0.0.5"   (left intact)
        None / "" / "." / "   "      -> ""

    An empty key means "unknown" and must never be treated as a match; callers
    should drop it from any lookup set. Input that is only dots or whitespace
    normalises to that key rather than to a token that could collide.
    """
    if not hostname:
        return ""
    s = hostname.strip().rstrip(".").lower()
    if not s:
        return ""
    if _IPV4.match(s):
        return s
    head = s.split(".", 1)[0]
    # A name that is nothing but dots leaves an empty first label — fall back to
    # the full string rather than silently returning the "unknown" key.
    return head or s
