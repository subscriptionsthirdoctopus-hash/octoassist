# =============================================================================
# OCTO-ASSETREC  —  Asset Register reconciliation aids   (TEMA India, Aug 2026)
#
# Appended to app/web/views.py by Apply-AssetReconcile.ps1. Everything here is
# additive: no existing function is rewritten, and each name is prefixed
# _octo_ so it cannot shadow anything in the build it lands on.
#
# Appending rather than editing in place is deliberate. Python resolves module
# globals when a function RUNS, not when the file is parsed, so a helper called
# from assets_index() higher up resolves fine from down here. That lets the
# in-place edits stay one line each, which is what makes them survivable on a
# build that has drifted from the repo.
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


def _octo_dim_excluded(value, wanted):
    """True when `value` should be filtered out for the pick `wanted`.

    Both sides are trimmed and lowercased. Comparing untrimmed is what let
    "Achhad" and "Achhad " behave as two different places, splitting one
    site's count across two dropdown entries that each disagreed with the
    site's real total.
    """
    w = (wanted or "").strip().lower()
    return bool(w) and (value or "").strip().lower() != w


def _octo_loc_excluded(value, wanted):
    """Location variant of the above, plus the "no location set" pick.

    An asset with no location on the device and none on its assigned user
    matched no location filter at all, while still counting in the register
    total. That is invisible on a per-site count -- which is exactly the view
    a site owner uses -- so the sentinel makes those rows selectable.
    """
    if (wanted or "").strip().lower() == NO_LOCATION:
        return bool((value or "").strip())
    return _octo_dim_excluded(value, wanted)


def _octo_fold_variants(values):
    """One dropdown entry per place, not one per spelling.

    The filters now compare trimmed and lowercased, so "Achhad", "achhad" and
    "Achhad " all select the same endpoints. Listing three entries that each
    return identical rows reads as a broken filter, and invites the reader to
    assume their assets are split across them.

    Presentation only -- the stored spellings are left exactly as they are,
    because the location routing rules read those same columns and correcting
    them is TEMA's call, not this patch's.
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

    Exists so a site owner can diff OctoAssist against their own spreadsheet
    by hostname, instead of comparing two totals and being told only that they
    differ.

    Self-contained on purpose: it imports what it needs locally and re-derives
    its rows, so it does not depend on this build's import list or on the
    internals of assets_index(). It applies the same filter rules the page now
    applies. Once TEMA's build is reconciled into the repo, the shared
    _collect_assets() there supersedes this and the two cannot drift.
    """
    import csv as _csv
    import io as _io
    import re as _re
    from datetime import timedelta as _td, timezone as _tz
    from fastapi.responses import StreamingResponse as _Streaming

    IST = _tz(_td(hours=5, minutes=30), name="IST")
    needle = (q or "").strip().lower()

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

    rows = []

    agents = (db.query(Agent)
                .filter(Agent.tenant_id == user.tenant_id,
                        Agent.uninstall_pending.is_(False))
                .all())
    for a in agents:
        pu = a.primary_user
        loc = a.location or (pu.location if pu else None)
        dept = pu.department if pu else None
        who = (pu.full_name if pu else None) or (pu.email if pu else None)
        if _octo_dim_excluded(dept, department):  continue
        if _octo_loc_excluded(loc, location):     continue
        if not _matches(a.hostname, who, dept, loc):
            continue
        rows.append(("agent", a.hostname, getattr(a, "machine_id", "") or "",
                     _plain(who), _plain(dept), _plain(loc),
                     "", _fmt(getattr(a, "last_seen_at", None))))

    # Entra-discovered endpoints with no agent reporting. Wrapped because a
    # build without the Entra connector need not have this model wired up, and
    # a missing optional table must not take the whole export down.
    try:
        managed = set()
        for a in agents:
            h = (a.hostname or "").strip().rstrip(".").lower()
            if h:
                managed.add(h.split(".", 1)[0])
        for d in db.query(EntraDevice).filter(
                EntraDevice.tenant_id == user.tenant_id).all():
            key = (d.display_name or "").strip().rstrip(".").lower().split(".", 1)[0]
            if key and key in managed:
                continue
            pu = d.primary_user
            dept = getattr(d, "department", None) or (pu.department if pu else None)
            loc = getattr(d, "location", None) or (pu.location if pu else None)
            who = (pu.full_name if pu else None) or (pu.email if pu else None)
            if _octo_dim_excluded(dept, department):  continue
            if _octo_loc_excluded(loc, location):     continue
            if not _matches(d.display_name, who, dept, loc):
                continue
            rows.append(("entra-discovered", d.display_name, "",
                         _plain(who), _plain(dept), _plain(loc),
                         _plain(getattr(d, "operating_system", "")),
                         _fmt(getattr(d, "approx_last_signin_at", None))))
    except Exception:
        pass

    rows.sort(key=lambda r: _octo_natural_key(r[1]))

    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["source", "hostname", "machine_id", "assigned_to",
                "department", "location", "operating_system", "last_seen_at"])
    for r in rows:
        w.writerow(list(r))
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
