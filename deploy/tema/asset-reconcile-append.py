# =============================================================================
# OCTO-ASSETREC  —  Asset Register reconciliation aids   (TEMA India, Aug 2026)
#
# Appended to app/web/views.py by Apply-AssetReconcile.ps1. Everything here is
# additive: no existing function is rewritten, and every name is prefixed
# _octo_ so it cannot shadow anything in the build it lands on.
#
# Appending rather than editing in place is deliberate. Python resolves module
# globals when a function RUNS, not when the file is parsed, so a helper called
# from assets_index() higher up resolves fine from down here. That lets the
# in-place edits stay one line each, which is what makes them survivable on a
# build that has drifted from the repo.
#
# WRITTEN AGAINST THE CAPTURED PRODUCTION SOURCES (21 Aug 2026), not the repo.
# What that capture changed:
#
#   * This build already resolves the Location dropdown from Active Directory
#     via services/offices.ad_offices(), and already folds case and known
#     misspellings — including "acchad" -> "Achhad". It does that for the
#     DROPDOWN only. The row filter still compares the asset's raw location
#     string, so an asset stored as "Acchad" never matches the "Achhad" the
#     dropdown offers: it vanishes from the site count while still counting in
#     the total. That is the most likely home of Dipesh's missing three, and
#     the fix is to route both sides through this build's own
#     offices.canonical_office() rather than to invent a second rule here.
#
#   * This build splits endpoints into THREE lists — rows, discovered_rows and
#     manual_rows (manually added assets, typically MacBooks). All three are
#     sorted and all three are exported.
#
# Marker for the idempotency check and the rollback: OCTO-ASSETREC
# =============================================================================

# Sentinel meaning "no location recorded at all". Not a legal location name, so
# it cannot collide with real data.
NO_LOCATION = "__none__"


def _octo_natural_key(value):
    """Sort key that orders embedded numbers by value, not by first digit.

    "TEMA-PC-2" sorts before "TEMA-PC-10", the way the site's own spreadsheet
    is ordered. Names that are missing or blank sort last: those rows are the
    least actionable and should not push real endpoints out of view.
    """
    import re as _re
    s = (value or "").strip()
    if not s:
        return ((2, 0, ""),)
    parts = []
    for chunk in _re.split(r"(\d+)", s):
        if not chunk:
            continue
        if chunk.isdigit():
            # int() drops leading zeros: "PC-007" and "PC-7" are one position.
            parts.append((0, int(chunk), ""))
        else:
            parts.append((1, 0, chunk.lower()))
    return tuple(parts)


# Resolved once, not per call. `False` means "not looked up yet"; None means
# "looked up and unavailable", which is recorded loudly below rather than
# silently degrading the location fix into a no-op -- an invisible no-op here
# would leave the reported bug in place while appearing to have fixed it.
_OCTO_OFFICES_MOD = False


def _octo_offices_mod():
    """This build's services/offices module, or None if it is not present."""
    global _OCTO_OFFICES_MOD
    if _OCTO_OFFICES_MOD is False:
        try:
            from ..services import offices as _offices
            _OCTO_OFFICES_MOD = _offices
        except Exception:
            _OCTO_OFFICES_MOD = None
            import logging
            logging.getLogger(__name__).warning(
                "OCTO-ASSETREC: services.offices unavailable - location filtering "
                "falls back to exact match, so assets stored under a misspelled "
                "office (e.g. 'Acchad' vs 'Achhad') will not be counted at that site."
            )
    return _OCTO_OFFICES_MOD


def _octo_office_list(db, user):
    """This build's Active Directory office list, or [] if unavailable.

    Resolved once per request and passed down, rather than per row: ad_offices
    runs a query, and the register renders hundreds of rows.
    """
    mod = _octo_offices_mod()
    if mod is None:
        return []
    try:
        return mod.ad_offices(db, user.tenant_id)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "OCTO-ASSETREC: ad_offices() failed; location filtering falls back "
            "to exact match.", exc_info=True)
        return []


def _octo_canon(value, office_list):
    """Fold a raw location onto its canonical AD office.

    Delegates to this build's services/offices.canonical_office so the filter
    agrees with the dropdown by construction -- including OFFICE_ALIASES, which
    already maps the "Acchad" misspelling onto the "Achhad" office.

    With that service unavailable this degrades to a trim, which is the old
    behaviour; _octo_offices_mod() has already logged why.
    """
    s = (value or "").strip()
    if not s:
        return ""
    mod = _octo_offices_mod()
    if mod is None:
        return s
    return (mod.canonical_office(s, office_list or []) or s).strip()


def _octo_fold_variants(values):
    """Collapse spellings that differ only by case or padding into one entry.

    Used for the Department dropdown. The Location dropdown does not need it:
    this build already resolves that list from Active Directory via
    services/offices.ad_offices(), which de-duplicates case-insensitively and
    applies OFFICE_ALIASES. Listing entries that each return identical rows
    reads as a broken filter, and invites the reader to assume their assets are
    split across them.

    Presentation only -- the stored spellings are left exactly as they are.
    """
    by_key = {}
    for raw in values:
        cleaned = (raw or "").strip()
        if not cleaned:
            continue
        by_key.setdefault(cleaned.lower(), []).append(cleaned)

    def _preferred(vs):
        # A capitalised spelling wins over an all-lowercase one; ties break
        # alphabetically so the list is stable between requests.
        return sorted(vs, key=lambda v: (v.islower(), v))[0]

    return sorted((_preferred(v) for v in by_key.values()), key=_octo_natural_key)


def _octo_dim_excluded(value, wanted):
    """True when `value` should be filtered out for the pick `wanted`.

    Both sides are trimmed and lowercased. Comparing untrimmed let a stray
    trailing space behave as a different department entirely.
    """
    w = (wanted or "").strip().lower()
    return bool(w) and (value or "").strip().lower() != w


def _octo_loc_excluded(value, wanted, office_list=None):
    """Location filter, comparing canonical AD offices on both sides.

    Also serves the "no location set" pick. An asset with no location on the
    device and none on its assigned user matched no location filter at all,
    while still counting in the register total — invisible on exactly the
    per-site view a site owner uses.
    """
    w = (wanted or "").strip()
    if w.lower() == NO_LOCATION:
        return bool((value or "").strip())
    if not w:
        return False
    return _octo_canon(value, office_list).lower() != _octo_canon(w, office_list).lower()


@router.get("/assets/export.csv")
def octo_assets_export_csv(
    q: str = "",
    department: str = "",
    location: str = "",
    compliant: str = "",
    online: str = "",
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """The Asset Register as currently filtered, as a CSV download.

    Exists so a site owner can diff OctoAssist against their own spreadsheet by
    hostname and serial, instead of comparing two totals and being told only
    that they differ.

    Self-contained on purpose: it imports what it needs locally and re-derives
    its rows, so it does not depend on this build's import list or on the
    internals of assets_index(). It applies the same filter rules the page now
    applies, and covers all three lists — agent-managed, Entra-discovered and
    manually added — because an asset missing from a site count can be in any
    of them.
    """
    import csv as _csv
    import io as _io
    import re as _re
    from datetime import timedelta as _td, timezone as _tz
    from fastapi.responses import StreamingResponse as _Streaming

    IST = _tz(_td(hours=5, minutes=30), name="IST")
    needle = (q or "").strip().lower()
    office_list = _octo_office_list(db, user)

    def _plain(value):
        # A spreadsheet treats the table's em-dash placeholder as a value: it
        # sorts, it fails an ISBLANK and it survives a de-duplicate. Blank it.
        v = (value or "").strip()
        return "" if v in {"—", "-"} else v

    def _fmt(dt):
        return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if dt else ""

    def _matches(*fields):
        if not needle:
            return True
        return needle in " | ".join((f or "").lower() for f in fields)

    out = []

    agents = (db.query(Agent)
                .filter(Agent.tenant_id == user.tenant_id,
                        Agent.uninstall_pending.is_(False))
                .all())
    managed = set()
    for a in agents:
        h = (a.hostname or "").strip().lower()
        if h:
            managed.add(h)
        pu = a.primary_user
        loc = a.location or (pu.location if pu else None)
        dept = pu.department if pu else None
        who = (pu.full_name if pu else None) or (pu.email if pu else None)
        if _octo_dim_excluded(dept, department):            continue
        if _octo_loc_excluded(loc, location, office_list):   continue
        if not _matches(a.hostname, who, dept, loc):
            continue
        out.append(["agent", a.hostname, "", _plain(who), _plain(dept),
                    _plain(loc), _plain(_octo_canon(loc, office_list)),
                    "", _fmt(getattr(a, "last_seen_at", None))])

    # Entra-discovered and manually-added assets share one table here; the
    # manual ones carry a 'manual-' device id and an admin-entered location.
    # A missing asset can be in either, so both are exported and labelled.
    try:
        for d in db.query(EntraDevice).filter(
                EntraDevice.tenant_id == user.tenant_id).all():
            if (d.display_name or "").strip().lower() in managed:
                continue
            pu = d.primary_user
            dept = pu.department if pu else None
            loc = d.location or (pu.location if pu else None)
            who = (pu.full_name if pu else None) or (pu.email if pu else None)
            if _octo_dim_excluded(dept, department):            continue
            if _octo_loc_excluded(loc, location, office_list):   continue
            if not _matches(d.display_name, who, dept, loc,
                            getattr(d, "serial_number", None)):
                continue
            is_manual = (getattr(d, "entra_device_id", "") or "").startswith("manual-")
            out.append(["manual" if is_manual else "entra-discovered",
                        d.display_name, _plain(getattr(d, "serial_number", None)),
                        _plain(who), _plain(dept), _plain(loc),
                        _plain(_octo_canon(loc, office_list)),
                        _plain(getattr(d, "operating_system", None)),
                        _fmt(getattr(d, "approx_last_signin_at", None))])
    except Exception:
        pass

    out.sort(key=lambda r: _octo_natural_key(r[1]))

    buf = _io.StringIO()
    w = _csv.writer(buf)
    # location_raw is what is stored; location_office is what the filter and
    # the dropdown match on. Showing both is how a mis-spelled site becomes
    # visible in the spreadsheet instead of just going missing from a count.
    w.writerow(["source", "hostname", "serial", "assigned_to", "department",
                "location_raw", "location_office", "operating_system",
                "last_seen_at"])
    for r in out:
        w.writerow(r)
    buf.seek(0)

    # Name the file after the filter, so a folder of exports for several sites
    # is still readable a week later.
    slug = _re.sub(r"[^A-Za-z0-9]+", "-", (location or "all-locations")).strip("-").lower()
    return _Streaming(
        iter([buf.read()]),
        media_type="text/csv",
        headers={"Content-Disposition":
                 'attachment; filename="octoassist-assets-%s.csv"' % (slug or "all")},
    )
