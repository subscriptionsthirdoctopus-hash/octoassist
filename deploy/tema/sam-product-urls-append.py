# =============================================================================
# OCTO-SAMURL  —  SAM product pages addressed by query string
#                  TEMA India, Aug 2026
#
# Appended to app/web/views_software.py by Fix-SamProductUrls.ps1.
# Additive only: the existing path routes are left registered and working, so
# any bookmark or emailed link still resolves.
#
# WHY. A product name is arbitrary vendor text, and putting it in a URL PATH
# means every character it contains has to survive the web server's path
# filtering. TEMA's catalogue holds 2,419 distinct names, of which:
#
#     125 contain "+"  (Microsoft Visual C++ ...)   -> IIS request filtering
#                                                      rejected %2B as double
#                                                      escaping. Unblocked on
#                                                      21 Aug by allowing that
#                                                      on the site.
#      31 contain "/"  (Microsoft Visual Basic/C++ Runtime, Python Tcl/Tk
#                       Support, the Windows Driver Package family)
#                                                   -> %2F in a path is
#                                                      rejected by http.sys,
#                                                      BELOW request filtering.
#                                                      No site-level setting
#                                                      reaches it.
#      17 contain "?", "#" or "%"                   -> same class of hazard.
#
# A query string has none of those constraints: it is not a path, so it is not
# path-filtered, and the value is delimited rather than parsed into segments.
# Addressing the page as
#
#     /software/product?publisher=...&product=...
#
# fixes all three groups at once and removes the need for the IIS setting,
# rather than chasing one character class at a time at the web-server layer.
#
# HOW. These routes do not reimplement anything. They re-encode their values
# and hand off to the existing handlers, which begin with unquote(). Passing
# quote(value, safe="") means that unquote() reconstructs exactly the string
# that arrived -- including a literal "%" in a product name, which a bare
# hand-off would have let unquote() eat.
#
# Marker for the idempotency check and the rollback: OCTO-SAMURL
# =============================================================================


def _octo_repath(value):
    """Re-encode a decoded query value for a handler that will unquote() it.

    FastAPI has already percent-decoded the query string, and both existing
    handlers start by calling unquote() on what they are given. Handing them a
    decoded value would unquote it a second time, which silently corrupts any
    name containing a percent sign. Encoding it back first makes the round trip
    exact for every input.
    """
    from urllib.parse import quote as _quote
    return _quote(value or "", safe="")


@router.get("/software/product", response_class=HTMLResponse)
def octo_software_product_detail_q(
    request: Request,
    publisher: str = "",
    product: str = "",
    flash: str | None = None,
    error: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Product detail, addressed by query string instead of by path.

    Delegates to the existing handler so the two forms cannot diverge: there is
    one implementation of the page, reachable two ways.
    """
    return software_product_detail(
        publisher=_octo_repath(publisher),
        product=_octo_repath(product),
        request=request, flash=flash, error=error, user=user, db=db,
    )


@router.get("/software/product/export.csv")
def octo_software_product_export_q(
    publisher: str = "",
    product: str = "",
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Endpoint CSV for one product, addressed by query string.

    Three path segments, against the five of the route it complements
    (/software/product/{publisher}/{product}/export.csv), so the two cannot
    shadow one another however they are ordered.
    """
    return software_product_export(
        publisher=_octo_repath(publisher),
        product=_octo_repath(product),
        user=user, db=db,
    )
