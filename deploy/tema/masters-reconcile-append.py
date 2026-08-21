# =============================================================================
# OCTO-MASTERS  —  Asset Management (Masters) reconciliation aids
#                  TEMA India, Aug 2026
#
# Appended to app/web/views_asset_mgmt.py by Apply-MastersReconcile.ps1.
# Additive only: no existing function is rewritten, and every name is prefixed
# _octo_ so it cannot shadow anything in this build.
#
# Appending works because Python resolves module globals when a function RUNS,
# not when the file is parsed, so helpers added here are visible to
# assets_list() above them. That keeps each in-place edit to a single line.
#
# WHY THIS EXISTS. Dipesh Panchal, 21 Aug 2026: Achhad shows 110 assets here
# against 113 in his spreadsheet, and he cannot tell which three differ.
# Confirmed against the production database:
#
#   * 110 is exactly what this table holds for Achhad — 106 in_use, 3 stock,
#     1 retired. The number is not wrong, it is just not reconcilable.
#
#   * The list was ordered by `ManagedAsset.id DESC` — the order rows were
#     entered, which is no order at all to a reader holding a spreadsheet.
#     That is the "numeric order" he asked for.
#
#   * 13 rows have `location_id IS NULL`. They are counted in the total but
#     match no location filter, and the Location dropdown is built only from
#     assets that HAVE a location, so there was no way to select them. Exactly
#     one of them resolves to Achhad (serial PG047VPT, AU-TIPL-LAP-027), so it
#     is one of his three. The rest of his gap is rows never entered here at
#     all — which the CSV export is what makes findable.
#
# The unlocated rows are a DATA problem: TEMA need to set a location on them.
# This patch's job is to stop the books hiding them.
#
# Marker for the idempotency check and the rollback: OCTO-MASTERS
# =============================================================================

# Sentinel meaning "no location recorded". Not a legal location name, so it
# cannot collide with a real master row.
NO_LOCATION = "__none__"


def _octo_natural_key(value):
    """Sort key that orders embedded numbers by value, not by first digit.

    "TIPL-LAP-2" sorts before "TIPL-LAP-10", the way the site's own
    spreadsheet is ordered. Missing or blank names sort last: those rows are
    the least actionable and should not push real assets out of view.
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
            # int() drops leading zeros: "LAP-007" and "LAP-7" are one position.
            parts.append((0, int(chunk), ""))
        else:
            parts.append((1, 0, chunk.lower()))
    return tuple(parts)


def _octo_row_label(asset, hosts):
    """The identity this row is displayed under, so the sort matches the page.

    asset_list.html shows `hosts.get(a.id) or product name or #id` in the first
    column. Sorting on anything else would order the table by a value the
    reader cannot see, which is its own kind of unreadable.
    """
    name = (hosts or {}).get(asset.id)
    if name:
        return name
    if getattr(asset, "product", None) and asset.product.name:
        return asset.product.name
    return "#%s" % asset.id


def _octo_masters_sort(assets, hosts):
    """Order the list the way a person reads it: hostname, then serial.

    Rows with neither a hostname nor a product name are shown as "#<id>" and
    are sorted to the END rather than by that label. Left to sort naturally
    they lead the table -- "#" precedes every letter -- so the least
    identifiable rows would push the real assets out of the first screen.
    """
    def _key(a):
        named = bool((hosts or {}).get(a.id)) or bool(
            getattr(a, "product", None) and a.product.name)
        return (0 if named else 1,
                _octo_natural_key(_octo_row_label(a, hosts)),
                _octo_natural_key(getattr(a, "serial_number", None)))
    assets.sort(key=_key)
    return assets


def _octo_loc_match(asset, wanted):
    """Location filter that can also select the rows having no location.

    Previously `if location:` tested `a.location and a.location.name ...`, so a
    row with location_id NULL failed every location pick while still counting
    in the unfiltered total — present in the books, absent from every view a
    site owner actually uses.
    """
    w = (wanted or "").strip()
    if w.lower() == NO_LOCATION:
        return asset.location is None
    return bool(asset.location) and (asset.location.name or "").strip().lower() == w.lower()


@router.get("/settings/asset-management/assets/export.csv")
def octo_masters_export_csv(
    status: str = "",
    q: str = "",
    oem: str = "",
    location: str = "",
    vendor: str = "",
    age: str = "",
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """The Masters asset list as currently filtered, as a CSV download.

    Exists so a site owner can diff these books against their own spreadsheet
    by hostname and serial, instead of comparing two totals and being told only
    that they differ.

    Deliberately mirrors assets_list()'s filter order rather than sharing code
    with it: this is appended to a build that is not a git checkout, and an
    edit to the view that forgot this route would be worse than a little
    duplication. The columns are the ones an asset sheet is keyed on.
    """
    import csv as _csv
    import io as _io
    import re as _re
    from fastapi.responses import StreamingResponse as _Streaming
    from ..models import AssetAllocation  # noqa: F401  (parity of imports)

    query = db.query(ManagedAsset).filter(ManagedAsset.tenant_id == user.tenant_id)
    if status in {s.value for s in AssetStatus}:
        query = query.filter(ManagedAsset.status == AssetStatus(status))
    assets = query.all()

    needle = (q or "").strip().lower()
    if needle:
        def _hit(a):
            hay = " ".join(filter(None, [
                a.po_number, a.serial_number, a.invoice_number, a.notes,
                a.product.name if a.product else None,
                a.product.oem if a.product else None,
                a.vendor.name if a.vendor else None,
                a.location.name if a.location else None,
            ])).lower()
            return needle in hay
        assets = [a for a in assets if _hit(a)]
    if oem:
        assets = [a for a in assets if a.product and (a.product.oem or "").lower() == oem.lower()]
    if location:
        assets = [a for a in assets if _octo_loc_match(a, location)]
    if vendor:
        assets = [a for a in assets if a.vendor and a.vendor.name.lower() == vendor.lower()]
    if age in AGE_BRACKETS:
        assets = [a for a in assets if _age_bracket(a.purchase_date) == age]

    hosts = _hostnames_for(db, assets)
    _octo_masters_sort(assets, hosts)

    def _s(v):
        return "" if v is None else str(v)

    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["hostname", "serial", "status", "location", "product", "oem",
                "vendor", "po_number", "invoice_number", "purchase_date",
                "cost", "asset_id"])
    for a in assets:
        w.writerow([
            _s(hosts.get(a.id)),
            _s(a.serial_number),
            a.status.value if a.status else "",
            _s(a.location.name if a.location else ""),
            _s(a.product.name if a.product else ""),
            _s(a.product.oem if a.product else ""),
            _s(a.vendor.name if a.vendor else ""),
            _s(a.po_number),
            _s(a.invoice_number),
            a.purchase_date.isoformat() if a.purchase_date else "",
            _s(a.cost),
            a.id,
        ])
    buf.seek(0)

    # Name the file after the site, so a folder of exports stays readable.
    tag = "no-location" if (location or "").strip().lower() == NO_LOCATION else (location or "all")
    slug = _re.sub(r"[^A-Za-z0-9]+", "-", tag).strip("-").lower() or "all"
    return _Streaming(
        iter([buf.read()]),
        media_type="text/csv",
        headers={"Content-Disposition":
                 'attachment; filename="tema-assets-%s.csv"' % slug},
    )
