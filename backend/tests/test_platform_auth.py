"""Platform Owner authentication (04 §5.9; 05 AUTH-5, AUTH-6). D87: email and password only, no second
step; the console resets a forgotten password; there is no authenticator reset for Administrators."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import cli
from app.main import create_app
from tests.support import CSRF, Org

PASSWORD = "quiet harbor lamp 77"


@pytest.fixture
def client(app_db: str) -> TestClient:
    return TestClient(create_app(run_checks=False), base_url="https://testserver")


def new_owner() -> str:
    email = f"po.{uuid.uuid4().hex[:6]}@example.com"
    cli.create_platform_owner(email, "Pat Owner", PASSWORD)
    return email


def sign_in(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    r = client.post("/api/v1/platform/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200 and r.json()["status"] == "authenticated", r.text
    return dict(r.json())


def bearer(body: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {body['access_token']}"}


def test_cli_rules() -> None:
    with pytest.raises(cli.CliError):
        cli.create_platform_owner(f"x{uuid.uuid4().hex[:4]}@example.com", "X", "password")  # a common password
    with pytest.raises(cli.CliError, match="at least 8"):
        cli.create_platform_owner(f"x{uuid.uuid4().hex[:4]}@example.com", "X", "short7!")
    email = new_owner()
    with pytest.raises(cli.CliError, match="already exists"):
        cli.create_platform_owner(email.upper(), "Again", PASSWORD)


def test_platform_sign_in_is_email_and_password(client: TestClient) -> None:
    """D87: the right password signs in at once; there are no second-step routes any more."""
    email = new_owner()
    body = sign_in(client, email)
    assert set(body) == {"status", "access_token", "token_type", "expires_in"}
    assert client.cookies.get("hms_platform_refresh") and "hms_refresh" not in client.cookies
    assert client.get("/api/v1/platform/me", headers=bearer(body)).json()["email"] == email
    wrong = client.post("/api/v1/platform/auth/login", json={"email": email, "password": "not " + PASSWORD})
    assert wrong.status_code == 401 and wrong.json()["code"] == "invalid_credentials"
    unknown = client.post("/api/v1/platform/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert unknown.status_code == 401 and unknown.json()["code"] == "invalid_credentials"
    for gone in ("mfa/verify", "mfa/totp/setup", "mfa/totp/confirm"):
        assert client.post(f"/api/v1/platform/auth/{gone}", json={}).status_code in (404, 405)


def test_wrong_passwords_lock_the_account(client: TestClient) -> None:
    email = new_owner()
    for _ in range(5):
        r = client.post("/api/v1/platform/auth/login", json={"email": email, "password": "wrong " + PASSWORD})
        assert r.status_code == 401
    assert client.post("/api/v1/platform/auth/login", json={"email": email, "password": PASSWORD}).status_code == 429


def test_tokens_dont_cross_audiences(client: TestClient, org_a: Org) -> None:
    """AUTH-5: org tokens fail on platform routes and the reverse."""
    body = sign_in(client, new_owner())
    assert client.get("/api/v1/me", headers=bearer(body)).status_code == 401
    assert client.get("/api/v1/platform/me", headers=org_a.people["admin"].headers()).status_code == 401


def test_platform_refresh_and_logout(client: TestClient) -> None:
    sign_in(client, new_owner())
    assert client.post("/api/v1/platform/auth/refresh").status_code == 403  # CSRF headers missing
    r = client.post("/api/v1/platform/auth/refresh", headers=CSRF)
    assert r.status_code == 200
    assert client.get("/api/v1/platform/me", headers=bearer(r.json())).status_code == 200
    assert client.post("/api/v1/platform/auth/logout", headers=CSRF).status_code == 204
    assert client.get("/api/v1/platform/me", headers=bearer(r.json())).status_code == 401


def test_console_password_reset_ends_every_session(client: TestClient) -> None:
    """A forgotten Platform Owner password is reset on the server (cli), never by email."""
    email = new_owner()
    body = sign_in(client, email)
    with pytest.raises(cli.CliError, match="at least 8"):
        cli.reset_platform_owner_password(email, "short1!")
    cli.reset_platform_owner_password(email, "a brand new secret passphrase")
    assert client.get("/api/v1/platform/me", headers=bearer(body)).status_code == 401
    old = client.post("/api/v1/platform/auth/login", json={"email": email, "password": PASSWORD})
    assert old.status_code == 401
    sign_in(client, email, "a brand new secret passphrase")
    with pytest.raises(cli.CliError, match="no platform user"):
        cli.reset_platform_owner_password("nobody@example.com", "a brand new secret passphrase")


def test_no_authenticator_reset_for_administrators(client: TestClient, org_a: Org) -> None:
    """D87: nothing to reset any more; the route and the database function are gone."""
    po = sign_in(client, new_owner())
    r = client.post(
        f"/api/v1/platform/organizations/{org_a.id}/super-admin-mfa-reset",
        headers=bearer(po),
        json={"email": org_a.people["admin"].email, "reason": "lost phone, ID checked by call"},
    )
    assert r.status_code in (404, 405)
