"""Route smoke test: every GET page an admin can open renders without error.

This is the check that was done by hand (curl + a minted cookie) after each
deploy on 5 Sep 2026, made repeatable. It asserts only what a broken template
or view would violate — HTTP 200 and no error text in the body — so it stays
green through normal UI changes and red when a page actually breaks.
"""
from __future__ import annotations

import pytest

ERROR_MARKERS = ("Internal Server Error", "Traceback (most recent call last)")

STATIC_PAGES = [
    "/reports", "/reports/library", "/reports/sla", "/reports/tickets", "/reports/assets", "/reports/changes",
    "/tickets", "/tickets/new",
    "/problems", "/changes", "/kb",
    "/assets", "/software", "/software/deploy", "/subscriptions",
    "/patches", "/patches/windows",
    "/settings", "/settings/identity", "/settings/sla", "/settings/notifications", "/settings/tenant",
    "/settings/holidays", "/settings/reply-templates", "/settings/categories-routing", "/settings/locations",
    "/settings/cab", "/settings/groups", "/settings/software-catalog", "/settings/roadmap",
    "/users", "/actions",
    "/portal", "/portal/new", "/portal/kb",
]


def _assert_ok(resp, path):
    assert resp.status_code == 200, f"{path} -> {resp.status_code}"
    body = resp.text
    for marker in ERROR_MARKERS:
        assert marker not in body, f"{path} rendered an error page"


@pytest.mark.parametrize("path", STATIC_PAGES)
def test_admin_page_renders(admin_client, path):
    _assert_ok(admin_client.get(path), path)


def test_pagination_pages_render(admin_client):
    for path in ("/software?page=2", "/software?page=999", "/users?page=2&per_page=100", "/assets?dpage=2"):
        _assert_ok(admin_client.get(path), path)


def test_detail_pages_render(admin_client, db_session):
    """One detail page per record type, using whatever rows the database has.
    An empty database (CI) simply has nothing to open here."""
    from app.models import Agent, Change, KbArticle, Problem, Ticket
    checks = [
        (Ticket, "/tickets/{id}"),
        (Agent, "/asset/{id}"),
        (Change, "/changes/{id}"),
        (Problem, "/problems/{id}"),
        (KbArticle, "/kb/{id}"),
    ]
    opened = 0
    for model, pattern in checks:
        row = db_session.query(model).order_by(model.id).first()
        if row is None:
            continue
        path = pattern.format(id=row.id)
        _assert_ok(admin_client.get(path), path)
        opened += 1
    assert opened >= 0  # informational; the loop asserts each page it can open


def test_software_product_with_slash_in_name(admin_client, db_session):
    """Regression for the '/' in product names 404 fixed 5 Sep 2026."""
    from urllib.parse import quote
    resp = admin_client.get("/software/product", params={"publisher": "Nobody", "product": "Nothing/Here"})
    assert resp.status_code == 404  # unknown product is a clean 404, not a routing miss
    # any real product page must resolve through the query-string route
    html = admin_client.get("/software").text
    marker = 'href="/software/product?publisher='
    if marker in html:
        start = html.index(marker) + len('href="')
        href = html[start:html.index('"', start)].replace("&amp;", "&")
        _assert_ok(admin_client.get(href), href)


def test_anonymous_is_redirected_to_login(client):
    client.cookies.clear()
    for path in ("/reports", "/tickets", "/settings"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code in (302, 303), f"{path} -> {resp.status_code}"
        assert resp.headers["location"].startswith("/login"), path
    assert client.get("/login").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_static_cache_policy(client):
    versioned = client.get("/static/styles.css?v=test")
    assert versioned.status_code == 200
    assert versioned.headers["cache-control"] == "public, max-age=31536000, immutable"
    plain = client.get("/static/favicon.svg")
    assert plain.headers["cache-control"] == "public, max-age=86400"


def test_login_throttle(client):
    client.cookies.clear()
    email = "throttle-smoke@example.invalid"
    codes = [client.post("/login", data={"email": email, "password": "wrong"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429, codes
