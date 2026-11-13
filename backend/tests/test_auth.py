"""Authentication for organization users (docs/04 §5; docs/05 AUTH-1..13, MT-11, MT-12).

Everything goes through the HTTP API, the worker and Mailpit, exactly as a browser would.
The client uses https://testserver so the browser-style cookie jar accepts Secure cookies.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.core import passwords
from app.core.db import transaction
from app.main import create_app
from app.modules.identity.models import User, UserSession
from app.modules.notifications.models import Notification
from tests.support import (
    CSRF,
    TEST_PASSWORD,
    Org,
    Person,
    add_person,
    drain_jobs,
    link_token,
    mail_to,
    make_org,
    reset_code,
)

Response = httpx.Response


@pytest.fixture
def client(app_db: str) -> TestClient:
    return TestClient(create_app(run_checks=False), base_url="https://testserver")


@pytest.fixture
def org(app_db: str) -> Org:
    o = make_org()
    add_person(o, "admin", roles=("super_admin",), code="E-0001")
    add_person(o, "student", kind="student", code="STU-2026-0001")
    add_person(o, "student2", kind="student", code="STU-2026-0002")
    return o


def login(
    client: TestClient, org: Org, identifier: str, password: str = TEST_PASSWORD, portal: str = "staff"
) -> Response:
    return client.post(
        f"/api/v1/o/{org.slug}/auth/login", json={"portal": portal, "identifier": identifier, "password": password}
    )


def student_login(client: TestClient, org: Org, p: Person, password: str = TEST_PASSWORD) -> Response:
    """A password-only sign-in (since D75 only students have one): the session starts at once."""
    return login(client, org, p.email, password, portal="student")


def sign_in(client: TestClient, org: Org, p: Person) -> dict[str, Any]:
    """D87: the password alone signs in. Returns the 'authenticated' body."""
    r = login(client, org, p.email, portal=p.kind)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    assert body["status"] == "authenticated"
    return body


def bearer(body: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {body['access_token']}"}


def sid(body: dict[str, Any]) -> str:
    return str(jwt.decode(body["access_token"], options={"verify_signature": False})["sid"])


def problem(r: Response, status: int, code: str) -> dict[str, Any]:
    assert r.status_code == status, r.text
    body: dict[str, Any] = r.json()
    assert body["code"] == code, body
    return body


# --- registration and Demo 0 (04 §5.2) ---------------------------------------------------------------


def test_register_verify_and_login(client: TestClient) -> None:
    slug = f"demo-{uuid.uuid4().hex[:8]}"
    admin_email = f"owner.{uuid.uuid4().hex[:6]}@example.com"
    assert client.get(f"/api/v1/public/slug-availability?slug={slug}").json() == {"available": True, "reason": None}
    r = client.post(
        "/api/v1/public/organizations",
        json={
            "organization": {"name": "Sunrise Hostels", "slug": slug, "contact_email": "office@example.com"},
            "super_admin": {"full_name": "Asha Rao", "email": admin_email, "password": "violet tram 4 eleven"},
        },
    )
    assert r.status_code == 202, r.text
    assert client.get(f"/api/v1/public/organizations/{slug}").status_code == 404  # not verified yet
    problem(
        login(client, Org(uuid.uuid4(), slug), admin_email, "violet tram 4 eleven"), 401, "invalid_credentials"
    )  # AUTH-11: an unverified organization can't log in

    token = link_token(admin_email)
    r = client.post(f"/api/v1/o/{slug}/auth/verify-registration", json={"token": token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "authenticated" and "mfa_token" not in body  # D87: signed in at once
    assert "hms_refresh" in client.cookies
    problem(client.post(f"/api/v1/o/{slug}/auth/verify-registration", json={"token": token}), 410, "link_expired")
    assert client.get(f"/api/v1/public/organizations/{slug}").json()["status"] == "active"

    me = client.get("/api/v1/me", headers=bearer(body)).json()
    assert me["roles"] == [{"role": "super_admin", "hostel_id": None}]
    assert me["code"] == "E-0001" and "totp_enabled" not in me  # Q85
    assert "audit.read" in me["permissions"]

    # Demo 0: the next sign-in is the email and the password, nothing else.
    org = Org(uuid.UUID(me["organization"]["id"]), slug)
    r = login(client, org, admin_email, "violet tram 4 eleven")
    assert r.status_code == 200 and r.json()["status"] == "authenticated"
    for gone in ("mfa/verify", "mfa/totp/setup", "mfa/totp/confirm", "mfa/email-otp", "mfa/recovery-codes/regenerate"):
        assert client.post(f"/api/v1/auth/{gone}", json={}, headers=bearer(body)).status_code in (404, 405)


def test_registration_rules(client: TestClient, org: Org) -> None:
    def register(slug: str, password: str = "violet tram 4 eleven") -> Response:  # noqa: S107
        return client.post(
            "/api/v1/public/organizations",
            json={
                "organization": {"name": "Some Hostels", "slug": slug, "contact_email": "o@example.com"},
                "super_admin": {"full_name": "Ravi K", "email": "ravi@example.com", "password": password},
            },
        )

    problem(register(org.slug), 409, "slug_taken")
    assert client.get(f"/api/v1/public/slug-availability?slug={org.slug}").json()["available"] is False
    assert client.get("/api/v1/public/slug-availability?slug=admin").json()["available"] is False
    body = problem(register(f"x-{uuid.uuid4().hex[:8]}", "password"), 422, "validation_error")
    assert body["errors"][0]["code"] == "too_common"
    r = client.post(
        "/api/v1/public/organizations",
        json={
            "organization": {
                "name": "X Hostels",
                "slug": "x-hostels-1",
                "contact_email": "o@example.com",
                "org_id": str(org.id),
            },
            "super_admin": {"full_name": "R", "email": "r@example.com", "password": "violet tram 4 eleven"},
        },
    )
    problem(r, 422, "validation_error")  # unknown field (MT-2 test_org_id_in_body_rejected)


def test_registration_rate_limited(client: TestClient) -> None:
    for i in range(4):
        r = client.post(
            "/api/v1/public/organizations",
            json={
                "organization": {
                    "name": "Spam Hostels",
                    "slug": f"spam-{uuid.uuid4().hex[:8]}",
                    "contact_email": "o@example.com",
                },
                "super_admin": {"full_name": "S", "email": f"s{i}@example.com", "password": "violet tram 4 eleven"},
            },
        )
        assert r.status_code == (202 if i < 3 else 429), r.text
    assert int(r.headers["retry-after"]) > 0


# --- login (04 §5.3) ---------------------------------------------------------------------------------


def test_login_by_email_code_and_portal(client: TestClient, org: Org) -> None:
    admin, student = org.people["admin"], org.people["student"]
    for identifier, portal, status in [
        (admin.email, "staff", "authenticated"),  # D87: no second step for anyone
        ("e-0001", "staff", "authenticated"),
        ("STU-2026-0001", "student", "authenticated"),
        (student.email, "student", "authenticated"),
    ]:
        r = login(client, org, identifier, portal=portal)
        assert r.status_code == 200 and r.json()["status"] == status, (identifier, r.text)
    problem(login(client, org, "STU-2026-0001", portal="staff"), 401, "invalid_credentials")  # wrong portal
    problem(login(client, org, admin.email, portal="student"), 401, "invalid_credentials")


def test_unknown_user_same_response_as_wrong_password(
    client: TestClient, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AUTH-2: same status, body and work (a dummy Argon2 run) whether or not the account exists."""
    calls: list[str] = []
    real = passwords.dummy_verify
    monkeypatch.setattr(passwords, "dummy_verify", lambda pw: (calls.append(pw), real(pw))[1])
    wrong = login(client, org, org.people["admin"].email, "wrong password here")
    unknown = login(client, org, "nobody@example.com", "wrong password here")
    strip = lambda r: {k: v for k, v in r.json().items() if k not in ("request_id",)}  # noqa: E731
    assert wrong.status_code == unknown.status_code == 401 and strip(wrong) == strip(unknown)
    assert calls == ["wrong password here"]  # only the unknown user needed the dummy hash
    problem(login(client, Org(uuid.uuid4(), "no-such-org"), "a@b.test", "x"), 401, "invalid_credentials")


def test_login_lockout_after_5_failures(client: TestClient, org: Org) -> None:
    p = add_person(org, "locky", kind="student", code="STU-2026-0094")
    for _ in range(5):
        problem(student_login(client, org, p, "not the password"), 401, "invalid_credentials")
    r = student_login(client, org, p)  # even the right password is refused now
    problem(r, 429, "too_many_attempts")
    assert 800 < int(r.headers["retry-after"]) <= 901
    with transaction(org.id) as db:
        db.execute(update(User).where(User.id == p.user_id).values(locked_until=datetime.now(UTC)))
    assert student_login(client, org, p).status_code == 200


def test_ip_throttle_after_20_failures(client: TestClient, org: Org) -> None:
    for i in range(20):
        problem(login(client, org, f"guess{i}@example.com", "x"), 401, "invalid_credentials")
    problem(login(client, org, org.people["admin"].email), 429, "too_many_attempts")


def test_login_always_creates_new_session(client: TestClient, org: Org) -> None:
    w = org.people["student2"]
    a, b = student_login(client, org, w).json(), student_login(client, org, w).json()
    assert sid(a) != sid(b)


def test_state_revealed_only_after_correct_password(client: TestClient, app_db: str) -> None:
    o = make_org()
    w = add_person(o, "w", roles=("super_admin",))
    gone = add_person(o, "gone", status="deactivated")  # a closed staff account (no role)
    problem(login(client, o, gone.email), 401, "invalid_credentials")  # no password set any more
    with transaction(o.id) as db:
        db.execute(
            update(User).where(User.id == gone.user_id).values(password_hash=passwords.hash_password(TEST_PASSWORD))
        )
    problem(login(client, o, gone.email), 403, "account_deactivated")
    with transaction(None, role="hms_platform") as db:
        db.execute(
            text(
                "UPDATE organizations SET status = 'suspended', suspended_at = now(), "
                "suspension_reason = 'test' WHERE id = :id"
            ),
            {"id": o.id},
        )
    problem(login(client, o, w.email, "wrong password"), 401, "invalid_credentials")
    problem(login(client, o, w.email), 403, "organization_suspended")


def test_login_with_other_org_slug_fails(client: TestClient, org: Org, org_b: Org) -> None:
    """MT-11: org B's credentials don't work at org A's address."""
    problem(login(client, org, org_b.people["student"].email, portal="student"), 401, "invalid_credentials")


# --- MFA (04 §5.4-5.5) -----------------------------------------------------------------------------


def refresh(client: TestClient, headers: dict[str, str] | None = None) -> Response:
    return client.post("/api/v1/auth/refresh", headers=CSRF if headers is None else headers)


def refresh_with(client: TestClient, cookie: str) -> Response:
    """Send exactly this refresh cookie (an old or tampered one), bypassing the cookie jar."""
    client.cookies.clear()
    return client.post("/api/v1/auth/refresh", headers={**CSRF, "Cookie": f"hms_refresh={cookie}"})


def test_refresh_rotates_token(client: TestClient, org: Org) -> None:
    body = student_login(client, org, org.people["student2"]).json()
    first = client.cookies["hms_refresh"]
    r = refresh(client)
    assert r.status_code == 200, r.text
    assert client.cookies["hms_refresh"] != first
    assert sid(r.json()) != sid(body)
    assert client.get("/api/v1/me", headers=bearer(r.json())).status_code == 200


def test_refresh_reuse_within_grace_does_not_revoke(client: TestClient, org: Org) -> None:
    student_login(client, org, org.people["student2"])
    old = client.cookies["hms_refresh"]
    assert refresh(client).status_code == 200
    newer = client.cookies["hms_refresh"]
    problem(refresh_with(client, old), 401, "refresh_superseded")
    assert refresh_with(client, newer).status_code == 200  # the family is still alive


def test_refresh_reuse_revokes_family(client: TestClient, org: Org) -> None:
    student_login(client, org, org.people["student2"])
    old = client.cookies["hms_refresh"]
    r = refresh(client)
    newest_access = r.json()
    newer = client.cookies["hms_refresh"]
    _, old_sid, _ = old.split(".")
    with transaction(org.id) as db:  # pretend the rotation happened a minute ago
        db.execute(
            update(UserSession)
            .where(UserSession.id == uuid.UUID(old_sid))
            .values(revoked_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    problem(refresh_with(client, old), 401, "session_invalid")
    problem(refresh_with(client, newer), 401, "session_invalid")  # the thief's AND the user's session are gone
    problem(client.get("/api/v1/me", headers=bearer(newest_access)), 401, "session_invalid")


def test_refresh_requires_custom_header_and_origin(client: TestClient, org: Org) -> None:
    student_login(client, org, org.people["student2"])
    problem(refresh(client, {"Origin": CSRF["Origin"]}), 403, "csrf_failed")
    problem(refresh(client, {**CSRF, "Origin": "https://evil.example"}), 403, "csrf_failed")
    problem(refresh(client, {"X-Requested-With": "hms"}), 403, "csrf_failed")
    assert refresh(client).status_code == 200


def test_refresh_cookie_org_swap_rejected(client: TestClient, org: Org, org_b: Org) -> None:
    """MT-12: the org ID in the cookie only picks the tenant; with it swapped nothing matches."""
    student_login(client, org, org.people["student2"])
    _, session_id, secret = client.cookies["hms_refresh"].split(".")
    problem(refresh_with(client, f"{org_b.id}.{session_id}.{secret}"), 401, "session_invalid")
    assert refresh_with(client, f"{org.id}.{session_id}.{secret}").status_code == 200  # the real one works


def test_logout(client: TestClient, org: Org) -> None:
    body = student_login(client, org, org.people["student2"]).json()
    r = client.post("/api/v1/auth/logout", headers=CSRF)
    assert r.status_code == 204 and "hms_refresh" not in client.cookies
    problem(client.get("/api/v1/me", headers=bearer(body)), 401, "session_invalid")  # AUTH-3
    problem(refresh(client), 401, "session_invalid")
    assert client.post("/api/v1/auth/logout", headers=CSRF).status_code == 204  # idempotent


# --- sessions and passwords ---------------------------------------------------------------------------


def test_sessions_list_and_revoke(client: TestClient, org: Org) -> None:
    p = add_person(org, "multi", kind="student", code="STU-2026-0097")
    a, b, c = (student_login(client, org, p).json() for _ in range(3))
    listed = client.get("/api/v1/auth/sessions", headers=bearer(c)).json()["sessions"]
    ids = {s["id"] for s in listed}
    assert {sid(a), sid(b), sid(c)} <= ids and [s["current"] for s in listed if s["id"] == sid(c)] == [True]
    assert client.post(f"/api/v1/auth/sessions/{sid(a)}/revoke", headers=bearer(c)).status_code == 204
    problem(client.get("/api/v1/me", headers=bearer(a)), 401, "session_invalid")
    other = student_login(client, org, org.people["student2"]).json()
    problem(client.post(f"/api/v1/auth/sessions/{sid(other)}/revoke", headers=bearer(c)), 404, "not_found")
    assert client.post("/api/v1/auth/sessions/revoke-others", headers=bearer(c)).json()["revoked"] >= 1
    problem(client.get("/api/v1/me", headers=bearer(b)), 401, "session_invalid")
    assert client.get("/api/v1/me", headers=bearer(c)).status_code == 200
    assert client.get("/api/v1/me", headers=bearer(other)).status_code == 200


def test_password_change(client: TestClient, org: Org) -> None:
    p = add_person(org, "changer", kind="student", code="STU-2026-0096")
    old, current = student_login(client, org, p).json(), student_login(client, org, p).json()
    url = "/api/v1/auth/password/change"
    problem(
        client.post(url, headers=bearer(current), json={"current_password": "nope", "new_password": "x" * 20}),
        401,
        "invalid_credentials",
    )
    body = problem(
        client.post(url, headers=bearer(current), json={"current_password": TEST_PASSWORD, "new_password": "short7!"}),
        422,
        "validation_error",
    )
    assert body["errors"][0]["code"] == "too_short"  # 15 without 2FA (AUTH-1)
    body = problem(
        client.post(
            url,
            headers=bearer(current),
            json={"current_password": TEST_PASSWORD, "new_password": "Changer is my name ok"},
        ),
        422,
        "validation_error",
    )
    assert body["errors"][0]["code"] == "contains_personal_info"
    passphrase = "मेरा लंबा पासवर्ड with spaces and no symbols at all " * 2  # long, Unicode, no rules
    assert (
        client.post(
            url, headers=bearer(current), json={"current_password": TEST_PASSWORD, "new_password": passphrase}
        ).status_code
        == 204
    )
    problem(client.get("/api/v1/me", headers=bearer(old)), 401, "session_invalid")  # other sessions end
    assert client.get("/api/v1/me", headers=bearer(current)).status_code == 200
    assert student_login(client, org, p, passphrase).status_code == 200
    with transaction(org.id) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == p.user_id, Notification.category == "security")
            )
            == 1
        )


RESET = "/api/v1/o/{slug}/auth/password-reset/{step}"


def _reset_request(client: TestClient, org: Org, identifier: str) -> Any:
    return client.post(RESET.format(slug=org.slug, step="request"), json={"identifier": identifier})


def _reset_confirm(
    client: TestClient, org: Org, identifier: str, code: str, new: str = "a whole new passphrase"
) -> Any:
    return client.post(
        RESET.format(slug=org.slug, step="confirm"), json={"identifier": identifier, "code": code, "new_password": new}
    )


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def test_password_reset_flow(client: TestClient, org: Org) -> None:
    """D77: organization + email or ID -> a 6-digit code by email -> code + new password."""
    p = add_person(org, "forgetful", kind="student", code="STU-2026-0042")
    sessions_before = student_login(client, org, p).json()
    for identifier in (p.email, "nobody@example.com", "E-9999"):  # AUTH-2: always 202
        assert _reset_request(client, org, identifier).status_code == 202
    code = reset_code(p.email)
    message = mail_to(p.email)[0]
    assert code not in message["Subject"]  # subjects show on locked phone screens
    assert "/reset-password" not in message["Text"] and "#token=" not in message["Text"]  # no link any more
    problem(_reset_confirm(client, org, p.email, code, "password"), 422, "validation_error")  # weak password
    assert _reset_confirm(client, org, p.email, code).status_code == 204  # the code survived the weak password
    problem(_reset_confirm(client, org, p.email, code, "another new passphrase"), 400, "invalid_reset_code")  # once
    problem(client.get("/api/v1/me", headers=bearer(sessions_before)), 401, "session_invalid")  # AUTH-9
    problem(student_login(client, org, p), 401, "invalid_credentials")
    assert login(client, org, "STU-2026-0042", "a whole new passphrase", portal="student").status_code == 200
    with transaction(org.id) as db:
        row = db.execute(
            text("SELECT code_hash, consumed_at FROM email_otps WHERE user_id = :u AND purpose = 'password_reset'"),
            {"u": p.user_id},
        ).one()
        assert row.consumed_at is not None and code not in row.code_hash  # only a hash is stored
        assert db.scalar(
            text("SELECT count(*) FROM audit_logs WHERE action = 'user.password_reset' AND entity_id = :u"),
            {"u": p.user_id},
        )


def test_password_reset_by_student_id(client: TestClient, org: Org) -> None:
    p = add_person(org, "idonly", kind="student", code="STU-2026-0043")
    assert _reset_request(client, org, "stu-2026-0043").status_code == 202
    assert _reset_confirm(client, org, "STU-2026-0043", reset_code(p.email)).status_code == 204
    assert login(client, org, "STU-2026-0043", "a whole new passphrase", portal="student").status_code == 200


def test_password_reset_code_expires_after_ten_minutes(client: TestClient, org: Org) -> None:
    p = add_person(org, "slow", kind="student", code="STU-2026-0093")
    before = datetime.now(UTC)  # the test clock (it starts at TEST_NOW and keeps ticking)
    _reset_request(client, org, p.email)
    code = reset_code(p.email)
    after = datetime.now(UTC)
    with transaction(org.id) as db:
        expires = db.scalar(text("SELECT expires_at FROM email_otps WHERE user_id = :u"), {"u": p.user_id})
        # The worker sets it from its own clock when it sends the email: 10 minutes later.
        assert before + timedelta(minutes=10) <= expires <= after + timedelta(minutes=10)
        db.execute(
            text(
                "UPDATE email_otps SET created_at = now() - interval '11 minutes', "
                "expires_at = now() - interval '1 minute' WHERE user_id = :u"
            ),
            {"u": p.user_id},
        )
    problem(_reset_confirm(client, org, p.email, code), 400, "invalid_reset_code")


def test_password_reset_code_cancelled_after_five_wrong_attempts(client: TestClient, org: Org) -> None:
    p = add_person(org, "guesser", kind="student", code="STU-2026-0094")
    _reset_request(client, org, p.email)
    code = reset_code(p.email)
    for _ in range(5):
        problem(_reset_confirm(client, org, p.email, _wrong(code)), 400, "invalid_reset_code")
    problem(_reset_confirm(client, org, p.email, code), 400, "invalid_reset_code")  # even the right code now
    with transaction(org.id) as db:
        row = db.execute(
            text("SELECT attempts, cancelled_at, consumed_at FROM email_otps WHERE user_id = :u"), {"u": p.user_id}
        ).one()
        assert row.attempts == 5 and row.cancelled_at is not None and row.consumed_at is None
    assert student_login(client, org, p).status_code == 200  # the old password still works


def test_password_reset_new_code_cancels_the_old_one(client: TestClient, org: Org) -> None:
    p = add_person(org, "twice", kind="student", code="STU-2026-0095")
    _reset_request(client, org, p.email)
    first = reset_code(p.email)
    with transaction(org.id) as db:  # past the 1-a-minute limit
        db.execute(
            text("UPDATE email_otps SET created_at = created_at - interval '2 minutes' WHERE user_id = :u"),
            {"u": p.user_id},
        )
    _reset_request(client, org, p.email)
    second = reset_code(p.email)
    if first != second:  # (one chance in a million they are equal)
        problem(_reset_confirm(client, org, p.email, first), 400, "invalid_reset_code")
    assert _reset_confirm(client, org, p.email, second).status_code == 204
    with transaction(org.id) as db:
        assert (
            db.scalar(
                text("SELECT count(*) FROM email_otps WHERE user_id = :u AND cancelled_at IS NOT NULL"),
                {"u": p.user_id},
            )
            == 1
        )


def test_password_reset_unknown_account_gets_the_same_answer(client: TestClient, org: Org) -> None:
    """05 AUTH-2: a wrong code for a real account, any code for an account or organization that
    doesn't exist, and a code for an account without a pending reset all answer the same."""
    p = add_person(org, "realone", kind="student", code="STU-2026-0096")
    _reset_request(client, org, p.email)
    code = reset_code(p.email)
    other = add_person(org, "noreset", kind="student", code="STU-2026-0097")
    answers = [
        _reset_confirm(client, org, p.email, _wrong(code)),
        _reset_confirm(client, org, "nobody@example.com", "123456"),
        _reset_confirm(client, org, "STU-2026-9999", "123456"),
        _reset_confirm(client, org, other.email, "123456"),
        client.post(
            "/api/v1/o/no-such-org/auth/password-reset/confirm",
            json={"identifier": p.email, "code": "123456", "new_password": "a whole new passphrase"},
        ),
    ]
    bodies = {(r.status_code, r.json()["code"], r.json()["title"], r.json()["detail"]) for r in answers}
    assert len(bodies) == 1 and next(iter(bodies))[:2] == (400, "invalid_reset_code")


def test_password_reset_requests_rate_limited(client: TestClient, org: Org) -> None:
    p = add_person(org, "spammed", kind="student", code="STU-2026-0092")
    for _ in range(5):  # per identifier: 3 per hour, the rest silently dropped (still 202)
        assert _reset_request(client, org, p.email).status_code == 202
    drain_jobs()
    assert len(mail_to(p.email)) == 1  # deduplicated while queued, then limited
    # Per ACCOUNT, whatever it is called: the Student ID is a new identifier, but the same person
    # already got a code this minute.
    assert _reset_request(client, org, "STU-2026-0092").status_code == 202
    drain_jobs()
    assert len(mail_to(p.email)) == 1
    for i in range(20):
        _reset_request(client, org, f"x{i}@example.com")
    problem(_reset_request(client, org, "y@example.com"), 429, "too_many_attempts")  # per IP


def test_password_reset_wrong_codes_rate_limited_per_ip(client: TestClient, org: Org) -> None:
    for i in range(20):
        problem(_reset_confirm(client, org, f"guess{i}@example.com", "123456"), 400, "invalid_reset_code")
    problem(_reset_confirm(client, org, "guess@example.com", "123456"), 429, "too_many_attempts")


def test_reset_links_are_gone_from_the_database(org: Org) -> None:
    p = add_person(org, "nolink", kind="student", code="STU-2026-0099")
    with pytest.raises(IntegrityError) as refused, transaction(org.id) as db:
        db.execute(
            text(
                "INSERT INTO auth_tokens (org_id, user_id, purpose, token_hash, delivery, expires_at) "
                "VALUES (:o, :u, 'password_reset', repeat('f', 64), 'email', now() + interval '1 hour')"
            ),
            {"o": org.id, "u": p.user_id},
        )
    assert refused.value.orig.diag.constraint_name == "auth_tokens_purpose_check"  # type: ignore[union-attr]
    # Five wrong tries without cancelling the code: refused.
    with transaction(org.id) as db:
        otp = db.scalar(
            text(
                "INSERT INTO email_otps (org_id, user_id, purpose, code_hash, expires_at) "
                "VALUES (:o, :u, 'password_reset', repeat('g', 64), now() + interval '10 minutes') RETURNING id"
            ),
            {"o": org.id, "u": p.user_id},
        )
    with pytest.raises(IntegrityError) as refused, transaction(org.id) as db:
        db.execute(text("UPDATE email_otps SET attempts = 5 WHERE id = :i"), {"i": otp})
    assert refused.value.orig.diag.constraint_name == "email_otps_cancelled_after_five"  # type: ignore[union-attr]
    # One live reset code per person.
    with pytest.raises(IntegrityError) as refused, transaction(org.id) as db:
        db.execute(
            text(
                "INSERT INTO email_otps (org_id, user_id, purpose, code_hash, expires_at) "
                "VALUES (:o, :u, 'password_reset', repeat('h', 64), now() + interval '10 minutes')"
            ),
            {"o": org.id, "u": p.user_id},
        )
    assert refused.value.orig.diag.constraint_name == "email_otps_one_live_reset"  # type: ignore[union-attr]


def test_me(client: TestClient, org: Org) -> None:
    body = login(client, org, "STU-2026-0001", portal="student").json()
    me = client.get("/api/v1/me", headers=bearer(body)).json()
    assert me["kind"] == "student" and me["code"] == "STU-2026-0001" and me["roles"] == []
    assert me["organization"]["slug"] == org.slug and me["permissions"] == []
