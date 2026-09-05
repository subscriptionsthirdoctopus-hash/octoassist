"""Shared fixtures for the server tests.

These tests need a real Postgres (the models use JSONB and the app upgrades
its own schema on startup), so they run only when OCTOASSIST_DATABASE_URL
points at one — an empty database is fine, the app seeds its admin user on
startup. Point it at a *copy* of production, never production itself:
deploy/droplet/smoke-test.sh does exactly that.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

DB_URL = os.environ.get("OCTOASSIST_DATABASE_URL")

# Keep test runs deterministic and self-contained.
os.environ.setdefault("OCTOASSIST_SESSION_SECRET_KEY", "test-only-session-secret-not-for-production")
os.environ.setdefault("OCTOASSIST_ADMIN_PASSWORD", "test-only-admin-password")
os.environ.setdefault("OCTOASSIST_COOKIE_SECURE", "false")


def pytest_collection_modifyitems(config, items):
    if DB_URL:
        return
    skip = pytest.mark.skip(reason="OCTOASSIST_DATABASE_URL not set — smoke tests need a Postgres")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, base_url="http://testserver") as c:  # runs startup: create_all + seed
        yield c


@pytest.fixture(scope="session")
def db_session():
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _session_cookie(user_id: int) -> str:
    """Mint the same cookie Starlette's SessionMiddleware would after login."""
    from itsdangerous import TimestampSigner
    from app.config import settings
    payload = base64.b64encode(json.dumps({"user_id": user_id}).encode())
    return TimestampSigner(settings.session_secret_key).sign(payload).decode()


@pytest.fixture(scope="session")
def admin_client(client, db_session):
    from app.models import User, UserRole
    admin = (db_session.query(User)
             .filter(User.role == UserRole.admin, User.is_active.is_(True))
             .order_by(User.id).first())
    assert admin is not None, "no active admin in the database (startup seed should have created one)"
    client.cookies.set("octoassist_session", _session_cookie(admin.id))
    return client
