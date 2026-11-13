"""D78: Organization sign-in with only the email and the password.

* Administrator emails are unique across the platform, case-insensitive, in the database.
* The organization is found through administrator_org(), a narrow function; the table behind it is
  out of reach of the application roles, and no other RLS changes.
* An unknown email answers exactly like a wrong password; the limits stay.
* Forgot password with only the email. The old /o/{name}/login links and Student sign-in are unchanged.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.db import transaction
from app.main import create_app
from tests.support import TEST_PASSWORD, Org, Person, add_person, drain_jobs, make_org, reset_code

API = "/api/v1"


@pytest.fixture
def client(app_db: str) -> TestClient:
    return TestClient(create_app(run_checks=False), base_url="https://testserver")


def problem(r: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert r.status_code == status, r.text
    body: dict[str, Any] = r.json()
    assert body["code"] == code, body
    return body


def admin_of(org: Org, key: str = "admin") -> Person:
    return add_person(org, key, roles=("super_admin",), code="E-0001")


def sign_in(client: TestClient, email: str, password: str = TEST_PASSWORD) -> httpx.Response:
    return client.post(f"{API}/auth/login", json={"email": email, "password": password})


def test_email_and_password_find_the_organization(client: TestClient) -> None:
    org = make_org()
    admin = admin_of(org)
    first = sign_in(client, admin.email.upper())  # any case
    assert first.status_code == 200, first.text
    done = first.json()  # D87: signed in at once, no second step
    assert done["status"] == "authenticated" and done["user"]["kind"] == "staff" and "mfa_token" not in done
    me = client.get(f"{API}/me", headers={"Authorization": f"Bearer {done['access_token']}"}).json()
    assert me["organization"]["slug"] == org.slug


def test_unknown_email_and_wrong_password_answer_the_same(client: TestClient) -> None:
    org = make_org()
    admin = admin_of(org)
    wrong = sign_in(client, admin.email, "a wrong long passphrase")
    unknown = sign_in(client, f"nobody.{uuid.uuid4().hex[:6]}@example.com")
    student = add_person(org, "learner", kind="student", code="STU-2026-0501")
    as_student = sign_in(client, student.email)  # students are not found by this form
    for r in (wrong, unknown, as_student):
        problem(r, 401, "invalid_credentials")
    assert {(r.json()["title"], r.json().get("detail")) for r in (wrong, unknown, as_student)} == {
        (wrong.json()["title"], wrong.json().get("detail"))
    }


def test_limits_stay(client: TestClient) -> None:
    org = make_org()
    admin = admin_of(org)
    for _ in range(5):  # per account: 5 wrong passwords lock it
        problem(sign_in(client, admin.email, "a wrong long passphrase"), 401, "invalid_credentials")
    problem(sign_in(client, admin.email), 429, "too_many_attempts")
    for i in range(20):  # per IP
        sign_in(client, f"guess{i}@example.com")
    problem(sign_in(client, "guess@example.com"), 429, "too_many_attempts")


def test_administrator_emails_are_unique_across_the_platform(client: TestClient) -> None:
    first = make_org()
    admin = admin_of(first)
    taken = client.post(
        f"{API}/public/organizations",
        json={
            "organization": {
                "name": "Second Hostels",
                "slug": f"two-{uuid.uuid4().hex[:8]}",
                "contact_email": "o@example.com",
            },
            "super_admin": {
                "full_name": "Second Admin",
                "email": admin.email.upper(),
                "password": "a long enough phrase 1",
            },
        },
    )
    problem(taken, 409, "admin_email_taken")
    second = make_org()
    with pytest.raises(IntegrityError) as refused, transaction(second.id) as db:  # the database refuses it too
        db.execute(
            text("INSERT INTO users (org_id, kind, full_name, email) VALUES (:o, 'staff', 'Twin', :e)"),
            {"o": second.id, "e": admin.email.title()},
        )
    assert refused.value.orig.diag.constraint_name == "administrator_emails_pkey"  # type: ignore[union-attr]
    # Students are not Administrators: another organization's student may use the same address.
    with transaction(second.id) as db:
        db.execute(
            text("INSERT INTO users (org_id, kind, full_name, email) VALUES (:o, 'student', 'Same', :e)"),
            {"o": second.id, "e": admin.email},
        )


def test_the_lookup_is_narrow_and_rls_is_unchanged(client: TestClient) -> None:
    org = make_org()
    admin = admin_of(org)
    with transaction(None) as db:  # the application role, no tenant: the function answers one email
        assert db.scalar(text("SELECT administrator_org(:e)"), {"e": f"  {admin.email.upper()} "}) == org.id
        assert db.scalar(text("SELECT administrator_org('nobody@example.com')")) is None
    with pytest.raises(DBAPIError, match="permission denied"), transaction(None) as db:
        db.execute(text("SELECT * FROM administrator_emails"))
    with pytest.raises(DBAPIError, match="permission denied"), transaction(org.id) as db:
        db.execute(text("SELECT * FROM administrator_emails"))
    with transaction(None) as db:  # users stay invisible without a tenant
        assert db.scalar(text("SELECT count(*) FROM users")) == 0


def test_a_changed_email_follows(client: TestClient) -> None:
    org = make_org()
    admin = admin_of(org)
    new = f"renamed.{uuid.uuid4().hex[:6]}@example.com"
    with transaction(org.id) as db:
        db.execute(text("UPDATE users SET email = :e WHERE id = :u"), {"e": new, "u": admin.user_id})
    with transaction(None) as db:
        assert db.scalar(text("SELECT administrator_org(:e)"), {"e": admin.email}) is None
        assert db.scalar(text("SELECT administrator_org(:e)"), {"e": new}) == org.id


def test_an_unverified_organization_can_not_sign_in(client: TestClient) -> None:
    slug = f"pend-{uuid.uuid4().hex[:8]}"
    email = f"pending.{uuid.uuid4().hex[:6]}@example.com"
    r = client.post(
        f"{API}/public/organizations",
        json={
            "organization": {"name": "Pending Hostels", "slug": slug, "contact_email": "o@example.com"},
            "super_admin": {"full_name": "Pending Admin", "email": email, "password": "a long enough phrase 1"},
        },
    )
    assert r.status_code == 202, r.text
    problem(sign_in(client, email, "a long enough phrase 1"), 401, "invalid_credentials")


def test_forgot_password_with_only_the_email(client: TestClient) -> None:
    org = make_org()
    admin = admin_of(org)
    for email in (admin.email, "nobody@example.com"):  # always 202
        assert client.post(f"{API}/auth/password-reset/request", json={"email": email}).status_code == 202
    code = reset_code(admin.email)
    problem(
        client.post(
            f"{API}/auth/password-reset/confirm",
            json={"email": "nobody@example.com", "code": code, "new_password": "a whole new passphrase"},
        ),
        400,
        "invalid_reset_code",
    )
    done = client.post(
        f"{API}/auth/password-reset/confirm",
        json={"email": admin.email, "code": code, "new_password": "a whole new passphrase"},
    )
    assert done.status_code == 204, done.text
    assert sign_in(client, admin.email, "a whole new passphrase").json()["status"] == "authenticated"
    drain_jobs()


def test_old_links_and_student_sign_in_are_unchanged(client: TestClient) -> None:
    org = make_org()
    admin_of(org)
    student = add_person(org, "learner", kind="student", code="STU-2026-0502")
    by_code = client.post(
        f"{API}/o/{org.slug}/auth/login", json={"portal": "staff", "identifier": "E-0001", "password": TEST_PASSWORD}
    )
    assert by_code.json()["status"] == "authenticated"
    as_student = client.post(
        f"{API}/o/{org.slug}/auth/login",
        json={"portal": "student", "identifier": "STU-2026-0502", "password": TEST_PASSWORD},
    )
    assert as_student.json()["status"] == "authenticated" and student.code == "STU-2026-0502"
