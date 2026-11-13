"""Tenant-isolation plumbing (05 MT-4, MT-5) and the secret-leak test (05 AUTH-12)."""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core import db as core_db
from app.core.logging import JsonFormatter
from app.main import create_app
from tests.security.conftest import SecWorld
from tests.support import CSRF, drain_jobs, link_token, reset_code

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def test_only_core_db_creates_sessions() -> None:
    """MT-5: nothing outside app/core/db.py builds engines or sessions, so nothing can skip
    the tenant-context step. (Raw .connect() is allowed only for the startup role check and
    the scheduler's advisory-lock connection, which run no tenant queries.)"""
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        rel = path.relative_to(APP_DIR).as_posix()
        source = path.read_text(encoding="utf-8")
        if rel != "core/db.py" and re.search(r"\bSession\(|sessionmaker|create_engine\(", source):
            offenders.append(rel)
        if rel not in ("core/db.py", "core/startup.py", "core/jobs.py") and ".connect()" in source:
            offenders.append(rel)
    assert not offenders


def test_alternating_orgs_on_one_pooled_connection(
    world: SecWorld, settings: Any, app_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MT-4: with a pool of ONE connection, requests for A and B alternate on the same database
    connection; each sees only its own organization, and no setting survives a request."""
    tiny = create_engine(
        settings.db_url("hms_app", app_db), pool_size=1, max_overflow=0, pool_reset_on_return="rollback"
    )
    monkeypatch.setattr(core_db, "engine", lambda role="hms_app", database=None: tiny)
    client = TestClient(create_app(run_checks=False))
    try:
        for _ in range(3):
            for label in ("a", "b"):
                p = world.orgs[label].people["admin"]
                me = client.get("/api/v1/me", headers=p.headers()).json()
                assert me["organization"]["id"] == str(world.orgs[label].id)
                with tiny.connect() as conn:  # the same pooled connection, between requests
                    assert conn.execute(text("SELECT current_setting('app.org_id', true)")).scalar() in ("", None)
                    assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 0  # fail closed
    finally:
        tiny.dispose()


def test_no_secrets_in_logs_or_audit(world: SecWorld, caplog: pytest.LogCaptureFixture, su: Any) -> None:
    """AUTH-12: run every auth flow with recognisable secrets, then search all logs and the
    tables that could plausibly hold them."""
    client = TestClient(create_app(run_checks=False), base_url="https://testserver")
    secrets: list[str] = []
    password = f"sentinel-pass-{uuid.uuid4().hex[:8]}"
    new_password = f"sentinel-new-{uuid.uuid4().hex[:8]}"
    secrets += [password, new_password]
    slug = f"leak-{uuid.uuid4().hex[:6]}"
    email = f"leak.{uuid.uuid4().hex[:6]}@example.com"
    with caplog.at_level(logging.DEBUG):
        client.post(
            "/api/v1/public/organizations",
            json={
                "organization": {"name": "Leak Test", "slug": slug, "contact_email": email},
                "super_admin": {"full_name": "Lee Kay", "email": email, "password": password},
            },
        )
        reg_token = link_token(email)
        done = client.post(f"/api/v1/o/{slug}/auth/verify-registration", json={"token": reg_token}).json()
        cookie = client.cookies["hms_refresh"]
        secrets += [
            reg_token,
            done["access_token"],
            cookie.split(".")[2],
        ]
        refreshed = client.post("/api/v1/auth/refresh", headers=CSRF).json()
        secrets += [refreshed["access_token"], client.cookies["hms_refresh"].split(".")[2]]
        login = client.post(
            f"/api/v1/o/{slug}/auth/login", json={"portal": "staff", "identifier": email, "password": password}
        ).json()
        secrets.append(login["access_token"])
        auth = {"Authorization": f"Bearer {login['access_token']}"}
        client.post(
            "/api/v1/auth/password/change",
            headers=auth,
            json={"current_password": password, "new_password": new_password},
        )
        client.post(f"/api/v1/o/{slug}/auth/password-reset/request", json={"identifier": email})
        # D77: the reset code. It is only 6 digits, so it isn't searched for below (a timestamp's
        # microseconds could match by chance); the new password it sets is.
        client.post(
            f"/api/v1/o/{slug}/auth/password-reset/confirm",
            json={"identifier": email, "code": reset_code(email), "new_password": password + "-again"},
        )
        secrets.append(password + "-again")
        client.post(
            f"/api/v1/o/{slug}/auth/login",
            json={"portal": "staff", "identifier": email, "password": "wrong-" + password},
        )
        drain_jobs()

    log_text = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    tables = {}
    for table in (
        "audit_logs",
        "jobs",
        "notifications",
        "login_attempts",
        "sessions",
        "auth_tokens",
        "users",
        "email_otps",
    ):
        rows = su.execute(f"SELECT row_to_json(t)::text FROM {table} t").fetchall()  # noqa: S608
        tables[table] = "\n".join(r[0] for r in rows)

    def leaked(secret: str, haystack: str) -> bool:
        # A 6-digit TOTP code is too short to be a unique sentinel: thousands of rows contain UUIDs,
        # hashes and microsecond timestamps where any 6 digits occur by chance. It counts as leaked
        # only as a standalone number (not inside a longer run of hex digits, or after a "." as in
        # "17:12:34.123456"). Every longer secret is matched as a plain substring.
        if len(secret) <= 8 and secret.isdigit():
            return re.search(rf"(?<![0-9a-fA-F.]){secret}(?![0-9a-fA-F])", haystack) is not None
        return secret in haystack

    for secret in secrets:
        assert not leaked(secret, log_text), f"secret in logs: {secret[:6]}..."
        for table, dump in tables.items():
            assert not leaked(secret, dump), f"secret in {table}: {secret[:6]}..."
    assert caplog.records, "flows must have produced log records"
