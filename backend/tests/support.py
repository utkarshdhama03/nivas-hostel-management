"""Helpers for Phase 1 tests: build organizations, users and sessions through the APP role
(hms_app, with tenant context, under RLS), and mint access tokens for them.

Everything is created the way the application itself would create it, so tests can't
accidentally pass because the setup used a superuser that skips RLS.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text, update

from app.core import jobs, passwords, tokens
from app.core.db import set_tenant, transaction
from app.modules.identity.models import RoleAssignment, StaffMember, User, UserSession
from app.modules.organizations.models import Organization, OrganizationProfile

# --- The test clock (D67) ------------------------------------------------------------------------
# Every test runs on a clock that starts at a fixed instant and then ticks normally, so results never
# depend on when the suite runs (test_local_outing_rules once failed after 23:29 IST). The database
# follows: app/core/db.py copies the application's clock into app.now for business time.
#
# Why 2050: security timestamps (sessions, tokens, audit...) are written with the database's REAL
# now() so no code can backdate them (D67), while their expiry times are computed by the application
# clock. With the test clock BEHIND the real one, an expiry could fall before its own creation time
# (a check constraint rejects it); AHEAD of it, everything stays consistent (the whole suite passed
# with the clock 5 and 24 years ahead). 2050 stays ahead of the real clock for decades.
# HMS_TEST_NOW overrides the instant (an instant ahead of today's date).
TEST_NOW = datetime.fromisoformat(os.environ.get("HMS_TEST_NOW", "2050-06-15T05:30:00+00:00"))  # Wed 11:00 IST
TEST_TODAY = TEST_NOW.astimezone(ZoneInfo("Asia/Kolkata")).date()  # the test organizations' local date

TEST_PASSWORD = "correct horse battery staple 42"  # gitleaks:allow (test-only, not a real secret)


@lru_cache(maxsize=1)
def password_hash() -> str:
    return passwords.hash_password(TEST_PASSWORD)


@dataclass
class Person:
    org_id: uuid.UUID
    user_id: uuid.UUID
    session_id: uuid.UUID
    email: str
    kind: str
    roles: tuple[str, ...] = ()
    code: str | None = None  # Employee ID / Student ID

    def token(self, **kw: object) -> str:
        return tokens.issue(
            audience="hms-api", typ="access", sub=self.user_id, sid=self.session_id, org=self.org_id, **kw
        )  # type: ignore[arg-type]

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}"}


@dataclass
class Org:
    id: uuid.UUID
    slug: str
    people: dict[str, Person] = field(default_factory=dict)


def make_org(slug: str | None = None, name: str = "Test Hostels") -> Org:
    slug = slug or f"p1-{uuid.uuid4().hex[:10]}"
    with transaction(None) as db:
        org = Organization(slug=slug, name=name)  # status pending_verification (the RLS insert rule)
        db.add(org)
        db.flush()
        set_tenant(db, org.id)
        db.execute(
            update(Organization)
            .where(Organization.id == org.id)
            .values(status="active", email_verified_at=datetime.now(UTC), version=Organization.version + 1)
        )
        db.add(OrganizationProfile(org_id=org.id, contact_email=f"office@{slug}.example.com"))  # as registration does
        return Org(id=org.id, slug=slug)


def add_person(
    org: Org,
    key: str,
    *,
    kind: str = "staff",
    roles: tuple[str, ...] = (),
    status: str = "active",
    code: str | None = None,
    hostel_id: uuid.UUID | None = None,
) -> Person:
    email = f"{key}.{uuid.uuid4().hex[:6]}@example.com"
    now = datetime.now(UTC)
    with transaction(org.id) as db:
        user = User(
            org_id=org.id,
            kind=kind,
            full_name=key.title(),
            email=email,
            status=status,
            password_hash=password_hash() if status in ("active", "read_only") else None,
            read_only_until=(now + timedelta(days=90)).date() if status == "read_only" else None,
        )
        db.add(user)
        db.flush()
        if code and kind == "staff":
            db.add(StaffMember(org_id=org.id, user_id=user.id, employee_code=code))
        elif code:
            _student_row(db, org.id, user.id, code)
        for role in roles:
            db.add(RoleAssignment(org_id=org.id, user_id=user.id, role=role))
        session = UserSession(
            org_id=org.id,
            user_id=user.id,
            family_id=uuid.uuid4(),
            refresh_token_hash=tokens.hash_secret(tokens.new_secret()),
            expires_at=now + timedelta(days=7),
            absolute_expires_at=now + timedelta(days=30),
        )
        db.add(session)
        db.flush()
        person = Person(org.id, user.id, session.id, email, kind, roles, code=code)
    org.people[key] = person
    return person


def _student_row(db: Any, org_id: uuid.UUID, user_id: uuid.UUID, code: str) -> None:
    """The minimum a students row needs (Phase 3 builds the real admission flow): a hostel,
    an academic session and a photo file, created once per organization."""
    hostel = db.execute(text("SELECT id FROM hostels WHERE code = 'T1'")).scalar()
    if hostel is None:
        hostel = db.execute(
            text(
                "INSERT INTO hostels (org_id, code, name, resident_type) "
                "VALUES (:o, 'T1', 'Test Hostel', 'boys') RETURNING id"
            ),
            {"o": org_id},
        ).scalar()
    session = db.execute(text("SELECT id FROM academic_sessions WHERE name = '2026-27'")).scalar()
    if session is None:
        session = db.execute(
            text(
                "INSERT INTO academic_sessions (org_id, name, starts_on, ends_on, is_current) "
                "VALUES (:o, '2026-27', '2026-06-01', '2027-05-31', true) RETURNING id"
            ),
            {"o": org_id},
        ).scalar()
    fid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO files (id, org_id, category, storage_key, original_name, mime_type, size_bytes, "
            "sha256, uploaded_by) VALUES (:id, :o, 'student_photo', :key, 'photo.jpg', 'image/jpeg', 1234, "
            ":sha, :u)"
        ),
        {"id": fid, "o": org_id, "key": f"{org_id}/student_photo/2026/11/{fid}", "sha": fid.bytes * 2, "u": user_id},
    )
    db.execute(
        text(
            "INSERT INTO students (org_id, user_id, student_code, hostel_id, admission_session_id, gender, "
            "date_of_birth, photo_file_id, admission_date, created_by) VALUES (:o, :u, :c, :h, :s, 'male', "
            ":dob, :f, :adm, :u)"
        ),
        {
            "o": org_id,
            "u": user_id,
            "c": code,
            "h": hostel,
            "s": session,
            "dob": date(2007, 1, 1),
            "f": fid,
            "adm": date(2026, 6, 15),
        },
    )


# --- HTTP helpers --------------------------------------------------------------------------------

ORIGIN = "http://localhost:5173"  # settings.hms_public_url's origin
CSRF = {"X-Requested-With": "hms", "Origin": ORIGIN}
MAILPIT = "http://mailpit:8025/api/v1"


def drain_jobs(limit: int = 50) -> None:
    """Run queued jobs until none is due (emails reach Mailpit)."""
    import app.job_handlers  # noqa: F401  the same handlers the worker has

    for _ in range(limit):
        if jobs.work_once("test-worker") is None:
            return


def mail_to(address: str, wait: float = 3.0) -> list[dict[str, Any]]:
    """Every message Mailpit holds for `address`, newest first, each with its Text body."""
    deadline = time.monotonic() + wait
    while True:
        r = httpx.get(f"{MAILPIT}/search", params={"query": f'to:"{address}"'}, timeout=5)
        r.raise_for_status()
        found: list[dict[str, Any]] = r.json()["messages"]
        if found or time.monotonic() > deadline:
            break
        time.sleep(0.2)
    out = []
    for m in found:
        full = httpx.get(f"{MAILPIT}/message/{m['ID']}", timeout=5).json()
        out.append({**m, "Text": full["Text"], "HTML": full["HTML"]})
    return out


def link_token(address: str) -> str:
    """The one-time token from the newest link email to `address` (it travels in #token=...)."""
    drain_jobs()
    for message in mail_to(address):
        match = re.search(r"#token=([A-Za-z0-9_-]+)", message["Text"])
        if match:
            return match.group(1)
    raise AssertionError(f"no link email for {address}")


def reset_code(address: str) -> str:
    """The 6-digit code from the newest password-reset email to `address` (D77)."""
    drain_jobs()
    for message in mail_to(address):
        if message.get("Subject") == "Your password reset code":
            match = re.search(r"^\s*(\d{6})\s*$", message["Text"], re.MULTILINE)
            if match:
                return match.group(1)
    raise AssertionError(f"no reset code email for {address}")


def activation_token(client: Any, headers: dict[str, str], student: dict[str, Any]) -> str:
    """D80: the activation email sent again (there are no copied links), and the token from it."""
    r = client.post(f"/api/v1/admin/users/{student['user_id']}/activation-email", headers=headers)
    assert r.status_code == 202, r.text
    return link_token(student["email"])


def make_hostel(client: Any, sa: Person, code: str, resident_type: str = "boys") -> dict[str, Any]:
    """A hostel with one room type, building, floor, room and bed, created through the API."""

    def ok(r: Any, status: int) -> Any:
        assert r.status_code == status, r.text
        return r.json()

    h = ok(
        client.post(
            "/api/v1/admin/hostels",
            headers=sa.headers(),
            json={"code": code, "name": f"Hostel {code}", "resident_type": resident_type},
        ),
        201,
    )
    rt = ok(
        client.post(
            f"/api/v1/admin/hostels/{h['id']}/room-types",
            headers=sa.headers(),
            json={"name": "Double", "beds_per_room": 2},
        ),
        201,
    )
    b = ok(client.post(f"/api/v1/admin/hostels/{h['id']}/buildings", headers=sa.headers(), json={"name": "A"}), 201)
    f = ok(client.post(f"/api/v1/admin/buildings/{b['id']}/floors", headers=sa.headers(), json={"level": 1}), 201)
    room = ok(
        client.post(
            f"/api/v1/admin/floors/{f['id']}/rooms",
            headers=sa.headers(),
            json={"number": "101", "room_type_id": rt["id"]},
        ),
        201,
    )
    bed = ok(client.post(f"/api/v1/admin/rooms/{room['id']}/beds", headers=sa.headers(), json={"label": "A"}), 201)
    return {**h, "room_type": rt, "building": b, "floor": f, "room": room, "bed": bed}


# --- files ---------------------------------------------------------------------------------------


def jpeg_bytes(size: tuple[int, int] = (40, 30), exif: bool = True) -> bytes:
    """A real JPEG, by default carrying EXIF with a GPS-ish marker the pipeline must strip."""
    import io

    from PIL import Image

    img = Image.new("RGB", size, (200, 120, 40))
    out = io.BytesIO()
    if exif:
        e = Image.Exif()
        e[0x010F] = "SecretCameraMaker"  # Make
        e[0x010E] = "Taken at 12.9716N 77.5946E"  # ImageDescription with a location
        img.save(out, format="JPEG", exif=e.tobytes())
    else:
        img.save(out, format="JPEG")
    return out.getvalue()


def png_bytes() -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (10, 10), (0, 0, 0)).save(out, format="PNG")
    return out.getvalue()


def pdf_bytes(text: str = "masked aadhaar") -> bytes:
    nl = bytes([10])  # a newline
    return b"%PDF-1.4" + nl + b"1 0 obj<</Type/Catalog>>endobj" + nl + b"% " + text.encode() + nl + b"%%EOF" + nl


def upload(client: Any, who: Person, category: str, data: bytes, name: str = "file.bin") -> Any:
    return client.post(
        "/api/v1/files",
        headers=who.headers(),
        data={"category": category},
        files={"file": (name, data, "application/octet-stream")},
    )


def admit(client: Any, who: Person, hostel_id: str, **overrides: Any) -> dict[str, Any]:
    """Admit a student through the API (photo uploaded first, as the UI does)."""
    photo = upload(client, who, "student_photo", jpeg_bytes())
    assert photo.status_code == 201, photo.text
    body = {
        "full_name": "Ravi Kumar",
        "email": f"student.{uuid.uuid4().hex[:10]}@example.com",  # D80: required, unique platform-wide
        "hostel_id": hostel_id,
        "gender": "male",
        "date_of_birth": "2007-04-01",
        "photo_file_id": photo.json()["file_id"],
        "admission_date": "2026-06-15",
        "guardians": [
            {"full_name": "Sunil Kumar", "relationship": "father", "phone": "+91 98765 00001", "is_primary": True}
        ],
        "send_activation_email": False,
        **overrides,
    }
    r = client.post("/api/v1/admin/students", headers=who.headers(), json=body)
    assert r.status_code == 201, r.text
    return dict(r.json())
