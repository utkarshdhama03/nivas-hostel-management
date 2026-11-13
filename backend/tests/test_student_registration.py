"""Student registration requests (D74, with the 11 November update; docs/04 §12.7a).

* The organization's switch (off by default); an unknown organization looks the same as one that is off.
* Register -> verify the email -> the Administrator approves (the office's own admission code) or rejects.
* No status page: Student sign-in with the request's email and CORRECT password says "unverified",
  "waiting for approval" or "rejected: <reason>"; a wrong password gets the usual answer.
* Refusals, the same answer for known emails, the honeypot, rate limits, the purge, other tenants.
"""

from __future__ import annotations

import io
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db import transaction
from app.main import create_app
from app.modules.registration import service as registration
from tests.support import (
    Org,
    Person,
    add_person,
    drain_jobs,
    jpeg_bytes,
    link_token,
    mail_to,
    make_hostel,
    make_org,
    pdf_bytes,
)

API = "/api/v1"
PASSWORD = "my own long passphrase"


@pytest.fixture
def client(app_db: str) -> TestClient:
    return TestClient(create_app(run_checks=False), base_url="https://testserver")


def ok(r: httpx.Response, status: int = 200) -> Any:
    assert r.status_code == status, r.text
    return r.json() if r.content else None


def problem(r: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert r.status_code == status, r.text
    body: dict[str, Any] = r.json()
    assert body["code"] == code, body
    return body


class W:
    """An organization with its Administrator, a boys' hostel, a current session, requests switched on."""

    def __init__(self, client: TestClient, *, accepting: bool = True) -> None:
        self.org: Org = make_org()
        self.admin: Person = add_person(self.org, "admin", roles=("super_admin",), code="E-0001")
        self.hostel = make_hostel(client, self.admin, "H1", resident_type="boys")
        session = ok(
            client.post(
                f"{API}/admin/academic-sessions",
                headers=self.admin.headers(),
                json={
                    "name": "2026-27",
                    "starts_on": "2026-06-01",
                    "ends_on": "2027-05-31",
                },  # support.add_person reuses it
            ),
            201,
        )
        ok(client.post(f"{API}/admin/academic-sessions/{session['id']}/make-current", headers=self.admin.headers()))
        if accepting:
            self.switch(client, True)

    def switch(self, client: TestClient, on: bool) -> None:
        version = ok(client.get(f"{API}/admin/organization", headers=self.admin.headers()))["version"]
        ok(
            client.patch(
                f"{API}/admin/organization",
                headers=self.admin.headers(),
                json={"version": version, "accepts_student_requests": on},
            )
        )


def registration_limit() -> Any:
    from app.core import ratelimit

    return ratelimit.STUDENT_REQUESTS_PER_IP


def _email() -> str:
    return f"applicant.{uuid.uuid4().hex[:8]}@example.com"


def _form(w: W, email: str, **over: Any) -> dict[str, Any]:
    return {
        "hostel_id": w.hostel["id"],
        "full_name": "Arjun Mehta",
        "email": email,
        "phone": "+91 98765 11111",
        "gender": "male",
        "date_of_birth": "2007-04-01",
        "program": "B.Tech",
        "year_of_study": "1",
        "guardian_name": "Rakesh Mehta",
        "guardian_relationship": "father",
        "guardian_phone": "+91 98765 22222",
        "password": PASSWORD,
        **over,
    }


def submit(client: TestClient, slug: str, form: dict[str, Any], photo: bytes | None = None) -> httpx.Response:
    return client.post(
        f"{API}/public/organizations/{slug}/student-registration",
        data=form,
        files={"photo": ("me.jpg", photo if photo is not None else jpeg_bytes((1600, 1200)), "image/jpeg")},
    )


def verify(client: TestClient, slug: str, email: str) -> httpx.Response:
    return client.post(f"{API}/o/{slug}/auth/student-registration/verify", json={"token": link_token(email)})


def sign_in(client: TestClient, slug: str, identifier: str, password: str = PASSWORD) -> httpx.Response:
    return client.post(
        f"{API}/o/{slug}/auth/login", json={"portal": "student", "identifier": identifier, "password": password}
    )


def _request_id(org: Org, email: str) -> str:
    with transaction(org.id) as db:
        return str(
            db.scalar(
                text(
                    "SELECT id FROM student_registration_requests WHERE lower(email) = lower(:e) "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"e": email},
            )
        )


def _pending(client: TestClient, w: W, email: str | None = None) -> tuple[str, str]:
    email = email or _email()
    assert submit(client, w.org.slug, _form(w, email)).status_code == 202
    assert verify(client, w.org.slug, email).status_code == 204
    return email, _request_id(w.org, email)


def _actions(org: Org) -> list[str]:
    with transaction(org.id) as db:
        return list(db.scalars(text("SELECT action FROM audit_logs WHERE org_id = :o ORDER BY id"), {"o": org.id}))


# --- the switch -------------------------------------------------------------------------------------


def test_switch_is_off_by_default_and_an_unknown_organization_looks_the_same(client: TestClient) -> None:
    w = W(client, accepting=False)
    closed = {"accepts_requests": False, "organization_name": None, "hostels": []}
    assert ok(client.get(f"{API}/public/organizations/{w.org.slug}/student-registration")) == closed
    assert ok(client.get(f"{API}/public/organizations/no-such-org-x/student-registration")) == closed
    problem(submit(client, w.org.slug, _form(w, _email())), 404, "not_accepting_requests")
    problem(submit(client, "no-such-org-x", _form(w, _email())), 404, "not_accepting_requests")
    w.switch(client, True)
    info = ok(client.get(f"{API}/public/organizations/{w.org.slug}/student-registration"))
    assert info["accepts_requests"] and info["organization_name"] == "Test Hostels"
    assert [h["id"] for h in info["hostels"]] == [w.hostel["id"]]
    assert "organization.updated" in _actions(w.org)  # the switch is audited


# --- the whole flow ----------------------------------------------------------------------------------


def test_register_verify_wait_approve_then_sign_in(client: TestClient) -> None:
    w = W(client)
    email = _email()
    assert ok(submit(client, w.org.slug, _form(w, email.upper())), 202) == {"status": "accepted"}
    with transaction(w.org.id) as db:
        row = db.execute(
            text(
                "SELECT status, password_hash, photo, photo_mime FROM student_registration_requests WHERE org_id = :o"
            ),
            {"o": w.org.id},
        ).one()
    assert (
        row.status == "unverified" and row.password_hash.startswith("$argon2id$") and PASSWORD not in row.password_hash
    )
    with Image.open(io.BytesIO(row.photo)) as img:  # re-encoded: small, JPEG, no EXIF
        assert img.format == "JPEG" and max(img.size) <= 800 and not img.getexif()
    assert row.photo_mime == "image/jpeg" and len(row.photo) <= 400_000

    # Not verified yet: the correct password says so; a wrong one says what any wrong password says.
    problem(sign_in(client, w.org.slug, email), 403, "registration_unverified")
    problem(sign_in(client, w.org.slug, email, "a wrong long passphrase"), 401, "invalid_credentials")

    assert verify(client, w.org.slug, email.upper()).status_code == 204
    problem(
        client.post(
            f"{API}/o/{w.org.slug}/auth/student-registration/verify", json={"token": link_token(email.upper())}
        ),
        410,
        "link_expired",
    )  # once
    with transaction(w.org.id) as db:
        titles = list(db.scalars(text("SELECT title FROM notifications WHERE user_id = :u"), {"u": w.admin.user_id}))
    assert "New student registration request" in titles

    # Waiting: only the right password reveals it.
    waiting = problem(sign_in(client, w.org.slug, email), 403, "registration_pending")
    assert "waiting for the administrator" in waiting["title"].lower()
    problem(sign_in(client, w.org.slug, email, "a wrong long passphrase"), 401, "invalid_credentials")

    rid = _request_id(w.org, email)
    listed = ok(client.get(f"{API}/admin/registration-requests", headers=w.admin.headers()))
    assert [i["id"] for i in listed["items"]] == [rid]
    detail = ok(client.get(f"{API}/admin/registration-requests/{rid}", headers=w.admin.headers()))
    assert detail["full_name"] == "Arjun Mehta" and detail["date_of_birth"] == "2007-04-01" and detail["has_photo"]
    assert "password_hash" not in detail and "photo" not in detail
    photo = client.get(f"{API}/admin/registration-requests/{rid}/photo", headers=w.admin.headers())
    assert (
        photo.status_code == 200
        and photo.headers["content-type"] == "image/jpeg"
        and photo.content[:3] == b"\xff\xd8\xff"
    )

    approved = ok(client.post(f"{API}/admin/registration-requests/{rid}/approve", headers=w.admin.headers()))
    assert approved["student_code"].startswith("STU-2026-")
    problem(
        client.post(f"{API}/admin/registration-requests/{rid}/approve", headers=w.admin.headers()),
        409,
        "request_already_decided",
    )
    student = ok(client.get(f"{API}/admin/students/{approved['student_id']}", headers=w.admin.headers()))
    assert student["full_name"] == "Arjun Mehta" and student["email"].lower() == email.lower()
    assert student["user_status"] == "active" and student["guardians"][0]["full_name"] == "Rakesh Mehta"
    assert student["admission_date"] == "2050-06-15"  # the organization's today (frozen test clock)
    with transaction(w.org.id) as db:
        row = db.execute(
            text("SELECT status, password_hash, photo, student_id FROM student_registration_requests WHERE id = :i"),
            {"i": rid},
        ).one()
        verified = db.scalar(text("SELECT email_verified_at FROM users WHERE id = :u"), {"u": student["user_id"]})
    assert row.status == "approved" and row.password_hash is None and row.photo is None
    assert str(row.student_id) == approved["student_id"] and verified is not None

    # Approved: the request's own password signs in, by email or by the new Student ID.
    assert ok(sign_in(client, w.org.slug, email))["status"] == "authenticated"
    assert ok(sign_in(client, w.org.slug, approved["student_code"]))["status"] == "authenticated"
    drain_jobs()
    welcome = [m for m in mail_to(email) if "approved" in m["Subject"]]
    assert welcome and approved["student_code"] in welcome[0]["Text"]
    actions = _actions(w.org)
    for action in (
        "student_request.submitted",
        "student_request.verified",
        "student_request.approved",
        "student.admitted",
    ):
        assert action in actions


def test_rejection_shows_the_reason_on_sign_in(client: TestClient) -> None:
    w = W(client)
    email, rid = _pending(client, w)
    url = f"{API}/admin/registration-requests/{rid}/reject"
    problem(client.post(url, headers=w.admin.headers(), json={"reason": "no"}), 422, "validation_error")
    rejected = ok(client.post(url, headers=w.admin.headers(), json={"reason": "No rooms left for first-years"}))
    assert rejected["status"] == "rejected" and rejected["reject_reason"] == "No rooms left for first-years"
    answer = problem(sign_in(client, w.org.slug, email), 403, "registration_rejected")
    assert "rejected" in answer["title"].lower() and "No rooms left for first-years" in answer["detail"]
    problem(sign_in(client, w.org.slug, email, "a wrong long passphrase"), 401, "invalid_credentials")
    drain_jobs()
    assert any("No rooms left for first-years" in m["Text"] for m in mail_to(email))
    assert "student_request.rejected" in _actions(w.org)
    problem(
        client.post(f"{API}/admin/registration-requests/{rid}/approve", headers=w.admin.headers()),
        409,
        "request_already_decided",
    )
    # They may ask again; sign-in then tells about the newest request.
    assert submit(client, w.org.slug, _form(w, email)).status_code == 202
    assert verify(client, w.org.slug, email).status_code == 204
    problem(sign_in(client, w.org.slug, email), 403, "registration_pending")


# --- refusals --------------------------------------------------------------------------------------


def test_the_form_is_checked(client: TestClient) -> None:
    w = W(client)
    slug = w.org.slug
    original_limit = registration_limit().limit
    registration_limit().limit = 100  # more than 5 tries in this one test
    weak = problem(submit(client, slug, _form(w, _email(), password="short")), 422, "validation_error")
    assert weak["errors"][0]["field"] == "password"
    problem(submit(client, slug, _form(w, _email(), gender="female")), 422, "gender_hostel_mismatch")
    problem(submit(client, slug, _form(w, _email(), date_of_birth="2051-01-01")), 422, "validation_error")
    problem(submit(client, slug, _form(w, _email(), hostel_id=str(uuid.uuid4()))), 422, "validation_error")
    problem(submit(client, slug, _form(w, "not-an-email")), 422, "validation_error")
    problem(submit(client, slug, _form(w, _email()), photo=pdf_bytes()), 415, "unsupported_file_type")
    problem(submit(client, slug, _form(w, _email()), photo=b"\xff\xd8\xff" + b"x" * 100), 415, "unsupported_file_type")
    with transaction(w.org.id) as db:
        assert db.scalar(text("SELECT count(*) FROM student_registration_requests")) == 0
    registration_limit().limit = original_limit


def test_a_known_email_gets_the_same_answer_and_an_email_instead(client: TestClient) -> None:
    w = W(client)
    # D80: a student's email (in any organization) is refused outright; staff of this organization and
    # open requests still get "check your email", and the owner of the address is told by email.
    student = add_person(w.org, "already", kind="student", code="STU-2026-0900")
    problem(submit(client, w.org.slug, _form(w, student.email)), 409, "student_email_taken")
    existing = add_person(w.org, "officeworker", status="deactivated")
    assert ok(submit(client, w.org.slug, _form(w, existing.email)), 202) == {"status": "accepted"}
    email, _ = _pending(client, w)
    assert ok(submit(client, w.org.slug, _form(w, email.upper())), 202) == {"status": "accepted"}
    with transaction(w.org.id) as db:
        assert db.scalar(text("SELECT count(*) FROM student_registration_requests")) == 1
    drain_jobs()
    assert any("already has an account" in m["Text"] for m in mail_to(existing.email))
    assert any("already open" in m["Text"] for m in mail_to(email))


def test_the_honeypot_stores_nothing(client: TestClient) -> None:
    w = W(client)
    email = _email()
    assert ok(submit(client, w.org.slug, _form(w, email, website="http://spam.example")), 202) == {"status": "accepted"}
    drain_jobs()
    with transaction(w.org.id) as db:
        assert db.scalar(text("SELECT count(*) FROM student_registration_requests")) == 0
    assert mail_to(email, wait=0.5) == []


def test_rate_limits_per_ip_and_per_organization(client: TestClient) -> None:
    w = W(client)
    for _ in range(5):
        assert submit(client, w.org.slug, _form(w, _email())).status_code == 202
    problem(submit(client, w.org.slug, _form(w, _email())), 429, "too_many_attempts")  # 5 an hour per IP

    other = W(client)
    from app.core import ratelimit

    ratelimit.STUDENT_REQUESTS_PER_IP.reset()
    with transaction(other.org.id) as db:  # 50 already today
        db.execute(
            text(
                "INSERT INTO student_registration_requests (org_id, hostel_id, full_name, email, gender, "
                "date_of_birth, guardian_name, guardian_relationship, guardian_phone, password_hash, photo, "
                "photo_mime) SELECT :o, :h, 'Spam', 'spam' || g || '@example.com', 'male', '2007-01-01', 'G', "
                "'father', '+91 98765 00000', 'x', 'x', 'image/jpeg' FROM generate_series(1, 50) g"
            ),
            {"o": other.org.id, "h": other.hostel["id"]},
        )
    problem(submit(client, other.org.slug, _form(other, _email())), 429, "too_many_attempts")


# --- the purge, the database, other tenants ----------------------------------------------------------


def test_purge_deletes_unverified_after_a_day_and_rejected_personal_data_after_30_days(client: TestClient) -> None:
    w = W(client)
    stale = _email()
    assert submit(client, w.org.slug, _form(w, stale)).status_code == 202
    link_token(stale)  # the verification link exists
    fresh = _email()
    assert submit(client, w.org.slug, _form(w, fresh)).status_code == 202
    gone_email, gone = _pending(client, w)
    ok(
        client.post(
            f"{API}/admin/registration-requests/{gone}/reject",
            headers=w.admin.headers(),
            json={"reason": "Incomplete details"},
        )
    )
    _, waiting = _pending(client, w)
    with transaction(w.org.id) as db:
        db.execute(
            text(
                "UPDATE student_registration_requests SET created_at = now() - interval '25 hours' "
                "WHERE lower(email) = lower(:e)"
            ),
            {"e": stale},
        )
        db.execute(
            text("UPDATE student_registration_requests SET decided_at = now() - interval '31 days' WHERE id = :i"),
            {"i": gone},
        )
    with transaction(w.org.id, role="hms_worker") as db:
        registration.purge(db)
    with transaction(w.org.id) as db:
        rows = {
            str(r.id): r
            for r in db.execute(
                text(
                    "SELECT id, status, email, full_name, photo, password_hash, personal_data_deleted_at, "
                    "reject_reason FROM student_registration_requests"
                )
            )
        }
    assert _request_id(w.org, stale) == "None"  # deleted, with its link
    assert rows[_request_id(w.org, fresh)].status == "unverified"  # less than a day old: kept
    assert rows[waiting].status == "pending"
    kept = rows[gone]
    assert kept.status == "rejected" and kept.reject_reason == "Incomplete details"
    assert kept.email is None and kept.full_name is None and kept.photo is None and kept.password_hash is None
    assert kept.personal_data_deleted_at is not None
    problem(sign_in(client, w.org.slug, gone_email), 401, "invalid_credentials")  # nothing left to tell


def test_the_database_keeps_one_open_request_per_email(client: TestClient) -> None:
    w = W(client)
    email, rid = _pending(client, w)
    with pytest.raises(IntegrityError) as refused, transaction(w.org.id) as db:
        db.execute(
            text(
                "INSERT INTO student_registration_requests (org_id, hostel_id, full_name, email, gender, "
                "date_of_birth, guardian_name, guardian_relationship, guardian_phone, password_hash, photo, "
                "photo_mime) VALUES (:o, :h, 'Twin', upper(:e), 'male', '2007-01-01', 'G', 'father', "
                "'+91 98765 00000', 'x', 'x', 'image/jpeg')"
            ),
            {"o": w.org.id, "h": w.hostel["id"], "e": email},
        )
    assert refused.value.orig.diag.constraint_name == "student_requests_one_open_per_email"  # type: ignore[union-attr]
    with pytest.raises(IntegrityError), transaction(w.org.id) as db:  # approved keeps no password or photo
        db.execute(
            text(
                "UPDATE student_registration_requests SET status = 'approved', decided_by = :u, decided_at = now(), "
                "student_id = (SELECT id FROM students LIMIT 1) WHERE id = :i"
            ),
            {"u": w.admin.user_id, "i": rid},
        )
    with pytest.raises(IntegrityError), transaction(w.org.id) as db:  # a rejection needs a reason
        db.execute(
            text(
                "UPDATE student_registration_requests SET status = 'rejected', decided_by = :u, decided_at = now() "
                "WHERE id = :i"
            ),
            {"u": w.admin.user_id, "i": rid},
        )


def test_other_organizations_and_students_can_not_reach_requests(client: TestClient) -> None:
    a, b = W(client), W(client)
    _, rid = _pending(client, a)
    for path, method, body in (
        (f"/admin/registration-requests/{rid}", "GET", None),
        (f"/admin/registration-requests/{rid}/photo", "GET", None),
        (f"/admin/registration-requests/{rid}/approve", "POST", None),
        (f"/admin/registration-requests/{rid}/reject", "POST", {"reason": "Not from here"}),
    ):
        problem(client.request(method, f"{API}{path}", headers=b.admin.headers(), json=body), 404, "not_found")
    assert ok(client.get(f"{API}/admin/registration-requests", headers=b.admin.headers()))["items"] == []
    with transaction(b.org.id) as db:  # RLS, the second layer
        assert db.scalar(text("SELECT count(*) FROM student_registration_requests WHERE id = :i"), {"i": rid}) == 0
    student = add_person(a.org, "nosy", kind="student", code="STU-2026-0901")
    problem(client.get(f"{API}/admin/registration-requests", headers=student.headers()), 403, "wrong_portal")
    # A token from organization A's link doesn't work under organization B's address.
    email = _email()
    assert submit(client, a.org.slug, _form(a, email)).status_code == 202
    problem(
        client.post(f"{API}/o/{b.org.slug}/auth/student-registration/verify", json={"token": link_token(email)}),
        410,
        "link_expired",
    )
