"""D80: Student sign-in with only the email and the password.

* Every student has an email, unique across the platform (case-insensitive), in the database;
  admission and registration refuse one any student uses.
* The organization is found through student_org(), a narrow function; the tables behind it are out
  of reach of the application roles. An unknown email answers exactly like a wrong password.
* A registered student's pending or rejected request is still told after the correct password (D74).
* Forgot password with only the email. The old /o/{name}/login with the Student ID still works.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import transaction
from app.main import create_app
from tests.support import TEST_PASSWORD, add_person, make_org, reset_code
from tests.test_student_registration import W, _form, _pending, submit

API = "/api/v1"


@pytest.fixture
def client(app_db: str) -> TestClient:
    return TestClient(create_app(run_checks=False), base_url="https://testserver")


def problem(r: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert r.status_code == status, r.text
    body: dict[str, Any] = r.json()
    assert body["code"] == code, body
    return body


def sign_in(client: TestClient, email: str, password: str = TEST_PASSWORD) -> httpx.Response:
    return client.post(f"{API}/auth/login", json={"email": email, "password": password, "portal": "student"})


def test_a_student_signs_in_with_email_and_password(client: TestClient) -> None:
    org = make_org()
    student = add_person(org, "learner", kind="student", code="STU-2026-0601")
    r = sign_in(client, student.email.upper())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "authenticated" and body["user"]["kind"] == "student"
    me = client.get(f"{API}/me", headers={"Authorization": f"Bearer {body['access_token']}"}).json()
    assert me["organization"]["slug"] == org.slug


def test_unknown_email_and_wrong_password_answer_the_same(client: TestClient) -> None:
    org = make_org()
    student = add_person(org, "learner", kind="student", code="STU-2026-0602")
    admin = add_person(org, "admin", roles=("super_admin",), code="E-0001")
    answers = [
        sign_in(client, student.email, "a wrong long passphrase"),
        sign_in(client, f"nobody.{uuid.uuid4().hex[:6]}@example.com"),
        sign_in(client, admin.email),  # an Administrator is not found by Student sign-in
    ]
    for r in answers:
        problem(r, 401, "invalid_credentials")
    assert len({(r.json()["title"], r.json().get("detail")) for r in answers}) == 1


def test_limits_stay(client: TestClient) -> None:
    org = make_org()
    student = add_person(org, "learner", kind="student", code="STU-2026-0603")
    for _ in range(5):
        problem(sign_in(client, student.email, "a wrong long passphrase"), 401, "invalid_credentials")
    problem(sign_in(client, student.email), 429, "too_many_attempts")


def test_a_registered_student_is_told_how_the_request_stands(client: TestClient) -> None:
    w = W(client)
    email, rid = _pending(client, w)
    from tests.test_student_registration import PASSWORD

    problem(sign_in(client, email, PASSWORD), 403, "registration_pending")
    problem(sign_in(client, email, "a wrong long passphrase"), 401, "invalid_credentials")
    rejected = client.post(
        f"{API}/admin/registration-requests/{rid}/reject",
        headers=w.admin.headers(),
        json={"reason": "No rooms left for first-years"},
    )
    assert rejected.status_code == 200, rejected.text
    told = problem(sign_in(client, email, PASSWORD), 403, "registration_rejected")
    assert "No rooms left for first-years" in told["detail"]


def test_student_emails_are_unique_across_the_platform(client: TestClient) -> None:
    first = make_org()
    student = add_person(first, "learner", kind="student", code="STU-2026-0604")
    w = W(client)
    problem(submit(client, w.org.slug, _form(w, student.email.upper())), 409, "student_email_taken")
    with pytest.raises(Exception, match="student_emails_pkey"), transaction(w.org.id) as db:
        db.execute(
            text("INSERT INTO users (org_id, kind, full_name, email) VALUES (:o, 'student', 'Twin', :e)"),
            {"o": w.org.id, "e": student.email.title()},
        )


def test_the_lookup_is_narrow_and_rls_is_unchanged(client: TestClient) -> None:
    org = make_org()
    student = add_person(org, "learner", kind="student", code="STU-2026-0605")
    with transaction(None) as db:
        assert db.scalar(text("SELECT student_org(:e)"), {"e": f" {student.email.upper()} "}) == org.id
        assert db.scalar(text("SELECT student_org('nobody@example.com')")) is None
        assert db.scalar(text("SELECT student_email_taken(:e)"), {"e": student.email}) is True
    for table in ("student_emails", "student_request_emails"):
        with pytest.raises(DBAPIError, match="permission denied"), transaction(org.id) as db:
            db.execute(text(f"SELECT * FROM {table}"))  # noqa: S608 - fixed names


def test_forgot_password_with_only_the_email(client: TestClient) -> None:
    org = make_org()
    student = add_person(org, "learner", kind="student", code="STU-2026-0606")
    for email in (student.email, "nobody@example.com"):
        r = client.post(f"{API}/auth/password-reset/request", json={"email": email, "portal": "student"})
        assert r.status_code == 202
    code = reset_code(student.email)
    done = client.post(
        f"{API}/auth/password-reset/confirm",
        json={"email": student.email, "code": code, "new_password": "a whole new passphrase", "portal": "student"},
    )
    assert done.status_code == 204, done.text
    assert sign_in(client, student.email, "a whole new passphrase").json()["status"] == "authenticated"


def test_the_old_link_with_the_student_id_still_works(client: TestClient) -> None:
    org = make_org()
    add_person(org, "learner", kind="student", code="STU-2026-0607")
    r = client.post(
        f"{API}/o/{org.slug}/auth/login",
        json={"portal": "student", "identifier": "STU-2026-0607", "password": TEST_PASSWORD},
    )
    assert r.json()["status"] == "authenticated"
