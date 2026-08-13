"""Device compliance lookup and filtering for the Asset Register.

Entra is the only source of a compliance verdict. `EntraDevice.is_compliant`
carries it; `Agent` has no equivalent column and the agent reports no such
signal. A managed laptop still has an Entra record — the register just hides
it from the "discovered, no agent yet" list — so compliance for a managed
endpoint is read back out of that record rather than being unknown.

Kept out of web/views so the conflict rule below can be exercised directly.
"""
from __future__ import annotations

from typing import Iterable, Protocol

from .hostnames import normalise


class _HasCompliance(Protocol):
    display_name: str | None
    is_compliant: bool | None


# Accepted values of the Compliance filter. "" means no filter.
PICKS = ("", "yes", "no", "unknown")


def by_hostname(devices: Iterable[_HasCompliance]) -> dict[str, bool | None]:
    """Map normalised hostname -> compliance verdict.

    Keyed on services.hostnames.normalise so an agent reporting "LAP-01" finds
    the Entra record filed under "lap-01.tema.local". Devices whose name
    normalises to empty are dropped: that key means "unknown" and must never
    match an endpoint.

    Two records can normalise to one key when a machine is registered in Entra
    under both its short name and its FQDN. A definite verdict beats None, and
    two conflicting verdicts resolve to False — reporting a device compliant
    while one of its own records says otherwise is the one error a compliance
    view must not make.
    """
    out: dict[str, bool | None] = {}
    for d in devices:
        key = normalise(getattr(d, "display_name", None))
        if not key:
            continue
        if key not in out:
            out[key] = d.is_compliant
        elif out[key] is None:
            out[key] = d.is_compliant
        elif d.is_compliant is False:
            out[key] = False
    return out


def excluded(pick: str, value: bool | None) -> bool:
    """True when `value` is filtered out by Compliance filter `pick`.

    None is a real state, not a missing one — it means Entra has not reported
    on the device — so it is reachable through "unknown" rather than being
    silently lumped in with non-compliant.
    """
    if pick == "yes":     return value is not True
    if pick == "no":      return value is not False
    if pick == "unknown": return value is not None
    return False
