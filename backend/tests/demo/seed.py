"""Demo data for presentations: two organizations with about three months of history.

    ./scripts/demo-data.sh create      reset the demo data if it exists, then build it again
    ./scripts/demo-data.sh reset       remove the demo data only
    ./scripts/demo-data.sh status      say whether it exists

What it builds (tests/demo/content.py holds the wording):
  * demo-uni ("Demo University Hostels"): 2 hostels x 2 buildings x 3 floors, 228 beds, about 200 students,
    and its one Administrator (D75, D76);
  * sunrise ("Sunrise Girls Hostel"): 36 beds, about 30 students, one Administrator, for showing tenant isolation;
  * a demo Platform Owner.

HOW it builds them, and why:
  * Through the application itself: the HTTP API in-process (the same routes the browser calls), and
    service functions for what the worker normally does (charge runs, late fees, closing old complaints).
    Never raw SQL. So every rule the application enforces also shaped this data: receipt numbers have
    no gaps, the ledger balances, the Administrator's own refunds are marked self-approved, and so on.
  * History is made by moving the APPLICATION clock back (time-machine) and replaying events in time
    order. Business rows follow that clock (app_now(), D67). Security rows never do: every audit entry
    of the demo data carries the real time it was written, because nothing can backdate the audit log.
  * Accounts and sessions are created at the real time, before any time travel: a session written
    with a clock behind the real one would break its own check constraints (D67).
  * It sends no email: notifications are still written in-app, their email jobs are not queued.

Stop the worker while this runs (scripts/demo-data.sh does): its real-time jobs (late fees, closing
old complaints, expiring reservations) would otherwise act on half-built history.

RESET deletes rows, including audit rows, which the application itself can never do: it connects as
the PostgreSQL superuser and switches triggers off for its own connection. It touches only the two
organizations named here, and only if they carry the demo marker, plus the demo Platform Owner.
"""

from __future__ import annotations

import argparse
import heapq
import io
import json
import random
import shutil
import sys
import time
import uuid
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import time_machine
from faker import Faker
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from psycopg import sql
from sqlalchemy import select

import app.job_handlers  # noqa: F401  the same handlers the worker has
from app import cli
from app.core import jobs, passwords, tokens
from app.core.config import get_settings
from app.core.db import transaction
from app.main import create_app
from app.modules.allocations import service as allocations
from app.modules.announcements.router import fanout
from app.modules.complaints.router import auto_close
from app.modules.finance import service as finance
from app.modules.identity import emails, sessions
from app.modules.identity.models import User, UserSession
from app.modules.notifications import service as notifications
from app.modules.organizations.models import Organization
from tests.demo import content
from tests.demo.content import MARKER, HostelSpec, OrgSpec

SEED = 20261110  # the same people and the same story on every run
IST = ZoneInfo("Asia/Kolkata")  # the organizations' time zone (the default, Q48)
ORGS = (content.DEMO_UNIVERSITY, content.SUNRISE)

# Demo logins (decided 12 November): the same passwords on every run, read from a file in the
# secrets folder (HMS_SECRETS_DIR, mounted at --out), never stored in the project. `create` loads and
# checks them before it removes anything; demo-logins.md repeats them for the presenter.
PASSWORDS_FILE = "demo-passwords.env"
PASSWORD_KEYS = {
    "HMS_DEMO_ADMIN_PASSWORD": "the Administrators",
    "HMS_DEMO_OWNER_PASSWORD": "the Platform Owner",
    "HMS_DEMO_STUDENT_PASSWORD": "every student",
}
STAFF_PASSWORD = ""  # set by use_passwords() from the file; empty until then
STUDENT_PASSWORD = ""
OWNER_PASSWORD = ""
ADMIN_UNREAD_LEFT = 8  # the Administrator's bell after the seed: a handful, not 99+
OWNER_EMAIL = "platform.owner@demo-university.example"
OWNER_NAME = "Demo Platform Owner"

AVATAR_COLOURS = ((30, 64, 175), (4, 120, 87), (180, 83, 9), (157, 23, 77), (91, 33, 182), (15, 118, 110), (55, 65, 81))


class SeedError(RuntimeError):
    pass


def local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, datetime.min.time(), IST) + timedelta(hours=hour, minutes=minute)


def money(value: Any) -> Decimal:
    return Decimal(str(value))


# --- acting as people -------------------------------------------------------------------------------


@dataclass
class Actor:
    """Someone the seed acts as: a user and one of their sessions. A fresh access token is made for
    every call, at the current (possibly travelled) clock, exactly as a browser's refresh would."""

    org_id: uuid.UUID
    user_id: uuid.UUID
    session_id: uuid.UUID

    def headers(self) -> dict[str, str]:
        token = tokens.issue(audience="hms-api", typ="access", sub=self.user_id, sid=self.session_id, org=self.org_id)
        return {"Authorization": f"Bearer {token}"}


class Api:
    """The application's HTTP API, called in-process."""

    def __init__(self) -> None:
        self.client = TestClient(create_app(run_checks=False), base_url="https://testserver")
        self.calls = 0

    def send(self, method: str, path: str, who: Actor | None = None, **kw: Any) -> tuple[int, Any]:
        headers = {**kw.pop("headers", {}), **(who.headers() if who else {})}
        r = self.client.request(method, f"/api/v1{path}", headers=headers, **kw)
        self.calls += 1
        is_json = r.headers.get("content-type", "").startswith(("application/json", "application/problem+json"))
        return r.status_code, (r.json() if r.content and is_json else None)

    def call(self, method: str, path: str, who: Actor | None = None, **kw: Any) -> Any:
        status, body = self.send(method, path, who, **kw)
        if status >= 300:
            raise SeedError(f"{method} {path} -> {status}: {json.dumps(body)[:600]}")
        return body

    def get(self, path: str, who: Actor | None = None, **kw: Any) -> Any:
        return self.call("GET", path, who, **kw)

    def post(self, path: str, who: Actor | None = None, **kw: Any) -> Any:
        return self.call("POST", path, who, **kw)

    def patch(self, path: str, who: Actor | None = None, **kw: Any) -> Any:
        return self.call("PATCH", path, who, **kw)


# --- replaying history ------------------------------------------------------------------------------


class Timeline:
    """Events in time order. Each runs with the application clock moved to its moment."""

    def __init__(self, until: datetime) -> None:
        self.until = until
        self.now = datetime.min.replace(tzinfo=UTC)
        self._items: list[tuple[datetime, int, Callable[[], Any]]] = []
        self._count = 0
        self.ran = 0

    def at(self, when: datetime, fn: Callable[[], Any]) -> None:
        """Run `fn` at `when`. Anything at or after `until` (the real present) simply doesn't happen:
        that is how the data ends with requests still waiting and people still out."""
        when = max(when, self.now + timedelta(seconds=1))
        if when < self.until:
            heapq.heappush(self._items, (when, self._count, fn))
            self._count += 1

    def run(self) -> None:
        while self._items:
            when, _, fn = heapq.heappop(self._items)
            self.now = when
            with time_machine.travel(when, tick=True):
                fn()
            self.ran += 1
            if self.ran % 500 == 0:
                print(f"    {self.ran} events, now at {when.astimezone(IST):%d-%m-%Y}", flush=True)


# --- pictures and papers ----------------------------------------------------------------------------


def avatar(name: str) -> bytes:
    """An initials avatar, so no real person's photo is used."""
    initials = "".join(part[0] for part in name.split()[:2]).upper()
    colour = AVATAR_COLOURS[zlib.crc32(name.encode()) % len(AVATAR_COLOURS)]
    img = Image.new("RGB", (256, 256), colour)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=112)
    left, top, right, bottom = draw.textbbox((0, 0), initials, font=font)
    draw.text(((256 - (right - left)) / 2 - left, (256 - (bottom - top)) / 2 - top), initials, fill="white", font=font)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


def sample_pdf(title: str, lines: list[str]) -> bytes:
    """A one-page PDF that says in large letters that it is not a real document."""
    from weasyprint import HTML  # imported here: it is slow to load and only this function needs it

    body = "".join(f"<p>{line}</p>" for line in lines)
    html = f"""<html><body style="font-family: sans-serif; padding: 40px">
      <h1 style="color:#b91c1c; border:4px solid #b91c1c; padding:12px; text-align:center">SAMPLE – DEMO DATA</h1>
      <p style="text-align:center">Not a real document. Made up for a software demonstration.</p>
      <h2>{title}</h2>{body}</body></html>"""
    return bytes(HTML(string=html).write_pdf())


# --- one organization -------------------------------------------------------------------------------


@dataclass
class Student:
    id: str
    user_id: str
    code: str
    name: str
    hostel: str  # hostel code
    room_type: str  # Double | Triple
    bed_id: str
    actor: Actor
    profile: str = "prompt"  # prompt | upi | late | behind | defaulter
    pay_day: int = 5
    busy_until: datetime | None = None  # not asked for another leave before this
    gone_at: datetime | None = None  # checked out: no more events
    role: str | None = None  # a demo student's story, for demo-logins.md
    email: str = ""  # D80: the student signs in with it


@dataclass
class Hostel:
    spec: HostelSpec
    id: str
    room_types: dict[str, str]  # name -> id
    beds: list[dict[str, Any]]  # sorted; each: id, room_id, room_type, label, room
    fee: dict[str, str] = field(default_factory=dict)  # rent_double, rent_triple, mess -> fee structure id
    students: list[Student] = field(default_factory=list)


class Builder:
    def __init__(self, spec: OrgSpec, api: Api, now: datetime) -> None:
        self.spec, self.api, self.real_now = spec, api, now
        self.rng = random.Random(SEED + zlib.crc32(spec.slug.encode()))  # noqa: S311  demo data, not security
        self.fake = Faker("en_IN")
        self.fake.seed_instance(SEED + zlib.crc32(spec.slug.encode()))
        self.today = now.astimezone(IST).date()
        month = self.today.replace(day=1)
        self.months: list[date] = []  # the 1st of each month of history, oldest first; the last is this month
        for _ in range(4):
            self.months.insert(0, month)
            month = (month - timedelta(days=1)).replace(day=1)
        self.h0 = self.months[0]
        self.timeline = Timeline(until=now - timedelta(minutes=2))
        self.org_id = uuid.UUID(int=0)
        # D76: one Administrator per organization (who registers it) does everything.
        self.admin: Actor
        self.staff_logins: list[dict[str, str]] = []
        self.hostels: dict[str, Hostel] = {}
        self.students: dict[str, Student] = {}  # by student id
        self.categories: dict[str, str] = {}
        self.seed_sessions: list[uuid.UUID] = []
        self.used_names: set[str] = set()
        self.demo: list[Student] = []
        self.applicants: list[dict[str, str]] = []  # D74: pending registration requests
        self.admitted = 0
        self.counts: dict[str, int] = {}

    def count(self, what: str, n: int = 1) -> None:
        self.counts[what] = self.counts.get(what, 0) + n

    def tx(self, worker: bool = False) -> Any:
        return transaction(self.org_id, role="hms_worker" if worker else "hms_app")

    # ---- accounts (at the real time) -------------------------------------------------------------

    def register(self) -> None:
        s = self.spec
        email = f"admin@{s.domain}"
        self.api.post(
            "/public/organizations",
            json={
                "organization": {"name": s.name, "slug": s.slug, "contact_email": f"office@{s.domain}"},
                "super_admin": {"full_name": s.admin_name, "email": email, "password": STAFF_PASSWORD},
            },
        )
        with transaction(None) as db:
            self.org_id = db.scalars(select(Organization.id).where(Organization.slug == s.slug)).one()
        # The confirmation link normally travels by email. The seed makes the link itself, with the same
        # function the email job uses, so it needs neither the worker nor the mailbox.
        with self.tx() as db:
            user = db.scalars(select(User).where(User.email == email)).one()
            admin_id = user.id
            link_token = emails._new_link_token(db, user, "org_registration", emails.REGISTRATION_TTL)
        # Confirming the email signs the new Administrator in (D87: no authenticator to set up).
        done = self.api.post(f"/o/{s.slug}/auth/verify-registration", json={"token": link_token})
        claims = tokens.verify(done["access_token"], audience="hms-api", typ="access")
        self.admin = Actor(self.org_id, admin_id, uuid.UUID(claims["sid"]))
        self.seed_sessions.append(self.admin.session_id)
        self.count("administrators")
        self.staff_logins.append({"role": "Administrator", "name": s.admin_name, "id": email})

    def activate(self, user_id: str, password: str) -> Actor:
        """Set the password through the activation link (as the person would), then open a session."""
        self.set_password(user_id, password)
        with self.tx() as db:
            issued = sessions.start(db, org_id=self.org_id, user_id=uuid.UUID(user_id))
            actor = Actor(self.org_id, uuid.UUID(user_id), issued.row.id)
        self.seed_sessions.append(actor.session_id)
        return actor

    def set_password(self, user_id: str, password: str) -> None:
        """The activation link travels by email (D80: no copied links). The seed makes it with the same
        function the email job uses, so it needs neither the worker nor the mailbox."""
        with self.tx() as db:
            user = db.get(User, uuid.UUID(user_id))
            assert user is not None
            token = emails._new_link_token(db, user, "activation", emails.ACTIVATION_TTL)
        self.api.post(f"/o/{self.spec.slug}/auth/activate", json={"token": token, "password": password})

    # ---- the organization's set-up ----------------------------------------------------------------

    def set_up(self) -> None:
        s, api, a = self.spec, self.api, self.admin
        org = api.get("/admin/organization", a)
        api.patch(
            "/admin/organization",
            a,
            json={
                "version": org["version"],
                "legal_name": s.legal_name,
                "address_line1": "Hostel Office, Main Campus Road",
                "city": s.city,
                "state": s.state,
                "postal_code": s.postal_code,
                "contact_phone": "+91 00000 10000",
                "upi_id": s.upi_id,
                "upi_payee_name": s.name,
            },
        )
        start = (self.h0 - timedelta(days=45)).replace(day=1)
        end = start.replace(year=start.year + 1) - timedelta(days=1)
        session = api.post(
            "/admin/academic-sessions",
            a,
            json={"name": f"{start.year}-{str(end.year)[2:]}", "starts_on": str(start), "ends_on": str(end)},
        )
        api.post(f"/admin/academic-sessions/{session['id']}/make-current", a)
        half = start + timedelta(days=183)
        for name, lo, hi in (("Term 1", start, half), ("Term 2", half + timedelta(days=1), end)):
            api.post(
                f"/admin/academic-sessions/{session['id']}/terms",
                a,
                json={"name": name, "starts_on": str(lo), "ends_on": str(hi)},
            )
        for name in content.COMPLAINT_CATEGORIES:
            self.categories[name] = api.post("/admin/complaint-categories", a, json={"name": name})["id"]
        for h in s.hostels:
            self.hostels[h.code] = self._hostel(h)

    def _hostel(self, h: HostelSpec) -> Hostel:
        api, a, s = self.api, self.admin, self.spec
        hostel = api.post(
            "/admin/hostels",
            a,
            json={"code": h.code, "name": h.name, "resident_type": h.resident_type, "address": f"{h.name}, {s.city}"},
        )
        types = {
            name: api.post(f"/admin/hostels/{hostel['id']}/room-types", a, json={"name": name, "beds_per_room": n})[
                "id"
            ]
            for name, n in (("Double", 2), ("Triple", 3))
        }
        rows = ["building,floor,room,room_type,bed"]
        for building in h.buildings:
            for floor in range(1, h.floors + 1):
                for n in range(1, h.doubles_per_floor + h.triples_per_floor + 1):
                    kind = "Double" if n <= h.doubles_per_floor else "Triple"
                    for label in "AB" if kind == "Double" else "ABC":
                        rows.append(f"{building},{floor},{building}{floor}{n:02d},{kind},{label}")
        api.post(
            f"/admin/hostels/{hostel['id']}/imports/rooms-beds",
            a,
            files={"file": ("rooms.csv", "\n".join(rows).encode(), "text/csv")},
        )
        board = api.get(f"/admin/hostels/{hostel['id']}/bed-board", a)
        beds = [
            {"id": b["id"], "label": b["label"], "room_id": r["id"], "room": r["number"], "room_type": r["room_type"]}
            for r in sorted(board["rooms"], key=lambda r: r["number"])
            for b in sorted(r["beds"], key=lambda b: b["label"])
        ]
        if len(beds) != h.beds:
            raise SeedError(f"{h.code}: expected {h.beds} beds, the import made {len(beds)}")
        out = Hostel(h, hostel["id"], types, beds)
        base = {"hostel_id": hostel["id"], "billing_cycle": "monthly", "due_days": 10}
        late = {"late_fee_type": "flat", "late_fee_amount": s.late_fee, "grace_days": 5}
        for key, name, amount, extra in (
            ("rent_double", "Hostel rent, double room", s.rent_double, {"room_type_id": types["Double"], **late}),
            ("rent_triple", "Hostel rent, triple room", s.rent_triple, {"room_type_id": types["Triple"], **late}),
            ("mess", "Mess fee", s.mess_fee, {"fee_kind": "other"}),
        ):
            body = {"name": f"{name} ({h.code})", "fee_kind": "rent", "amount": amount, **base, **extra}
            out.fee[key] = api.post("/admin/fee-structures", a, json=body)["id"]
        api.post(
            "/admin/fee-structures",
            a,
            json={
                "name": f"Security deposit ({h.code})",
                "fee_kind": "deposit",
                "amount": s.deposit,
                "billing_cycle": "one_time",
                "hostel_id": hostel["id"],
            },
        )
        self.count("hostels")
        self.count("beds", len(beds))
        return out

    # ---- students (at the real time; the dates they carry are in the past) ------------------------

    def person_name(self, gender: str) -> tuple[str, content.NameGroup]:
        """A new full name from the hand-picked lists (content.NAME_GROUPS), and its group, so the
        parents' names fit too. First name and surname always come from the same group."""
        groups = content.NAME_GROUPS
        for _ in range(500):
            group = self.rng.choices(groups, weights=[g.weight for g in groups])[0]
            first = self.rng.choice(group.female if gender == "female" else group.male)
            name = f"{first} {self.rng.choice(group.surnames)}"
            if name not in self.used_names:
                self.used_names.add(name)
                return name, group
        raise SeedError("ran out of names")

    def admit(self, hostel: Hostel, bed: dict[str, Any] | None, admission: date, *, activate: bool = True) -> Student:
        api, m = self.api, self.admin
        gender = "female" if hostel.spec.resident_type == "girls" else "male"
        name, group = self.person_name(gender)
        self.admitted += 1
        n = self.admitted
        photo = api.post(
            "/files", m, data={"category": "student_photo"}, files={"file": ("photo.jpg", avatar(name), "image/jpeg")}
        )
        last = name.split()[1]
        made = api.post(
            "/admin/students",
            m,
            json={
                "full_name": name,
                "hostel_id": hostel.id,
                "gender": gender,
                "date_of_birth": str(
                    date(self.today.year - self.rng.randint(18, 23), self.rng.randint(1, 12), self.rng.randint(1, 28))
                ),
                "admission_date": str(admission),
                "photo_file_id": photo["file_id"],
                "email": f"{name.lower().replace(' ', '.')}.{n}@{self.spec.domain}",
                "phone": f"+91 00000 3{n:04d}",
                "program": self.rng.choice(content.PROGRAMS),
                "year_of_study": self.rng.randint(1, 4),
                "institution_roll_no": f"{self.today.year % 100}{hostel.spec.code}{n:04d}",
                "address_line1": f"{self.rng.randint(1, 240)}, {self.fake.street_name()}",
                "city": self.fake.city(),
                "state": self.spec.state,
                "postal_code": f"{self.rng.randint(110001, 855999)}",
                "guardians": [
                    {
                        "full_name": f"{self.rng.choice(group.male)} {last}",
                        "relationship": "father",
                        "phone": f"+91 00000 4{n:04d}",
                        "is_primary": True,
                    },
                    {
                        "full_name": f"{self.rng.choice(group.female)} {last}",
                        "relationship": "mother",
                        "phone": f"+91 00000 5{n:04d}",
                    },
                ],
                "send_activation_email": False,
            },
        )
        actor = (
            self.activate(made["user_id"], STUDENT_PASSWORD)
            if activate
            else Actor(self.org_id, uuid.UUID(made["user_id"]), uuid.UUID(int=0))
        )
        st = Student(
            made["id"],
            made["user_id"],
            made["student_code"],
            name,
            hostel.spec.code,
            bed["room_type"] if bed else "Double",
            bed["id"] if bed else "",
            actor,
            email=made["email"],
        )
        if bed:
            api.post(
                "/admin/allocations", m, json={"student_id": st.id, "bed_id": bed["id"], "start_date": str(admission)}
            )
            hostel.students.append(st)
            self.students[st.id] = st
        self.count("students")
        return st

    def admit_everyone(self) -> None:
        for hostel in self.hostels.values():
            for i in range(hostel.spec.students):
                self.admit(hostel, hostel.beds[i], self.h0 - timedelta(days=self.rng.randint(1, 5)))
            print(f"    {hostel.spec.code}: {len(hostel.students)} students admitted", flush=True)
        everyone = list(self.students.values())
        # The three students the presenter signs in as come first, with fixed stories.
        demo = self.hostels[self.spec.hostels[0].code].students[:3]
        demo[0].profile, demo[0].pay_day, demo[0].role = "prompt", 3, "Fully paid: every month paid on time."
        demo[1].profile, demo[1].role = "behind", "Overdue: owes last month's fees with a late fee, and this month's."
        demo[2].profile, demo[2].pay_day, demo[2].role = "prompt", 4, "On approved home leave now."
        for st in everyone:
            if st.role is None:
                st.profile = self.rng.choices(("prompt", "upi", "late", "behind", "defaulter"), (60, 13, 13, 9, 5))[0]
                st.pay_day = self.rng.randint(2, 10)
        self.demo = demo

    def documents(self) -> None:
        """Identity papers for some students: sample PDFs with masked numbers (Aadhaar rules, 04 §11.1)."""
        api, m = self.api, self.admin
        for hostel in self.hostels.values():
            for st in hostel.students[: max(6, hostel.spec.students // 5)]:
                last4 = f"{self.rng.randint(0, 9999):04d}"
                pdf = sample_pdf(
                    "Identity document (sample)", [f"Name: {st.name}", f"Aadhaar number (masked): XXXX XXXX {last4}"]
                )
                f = api.post(
                    "/files",
                    m,
                    data={"category": "identity_document"},
                    files={"file": ("aadhaar-sample.pdf", pdf, "application/pdf")},
                )
                doc = api.post(
                    f"/admin/students/{st.id}/documents",
                    m,
                    json={
                        "document_type": "identity",
                        "file_id": f["file_id"],
                        "id_type": "aadhaar",
                        "id_number_masked": f"XXXXXXXX{last4}",
                        "masked_aadhaar_attested": True,
                    },
                )
                if self.rng.random() < 0.7:
                    api.post(f"/admin/students/{st.id}/documents/{doc['id']}/verify", m)
                self.count("identity documents")

    def mess_menus(self) -> None:
        monday = self.h0 - timedelta(days=self.h0.weekday())
        week = 0
        while monday <= self.today + timedelta(days=7):
            for hostel in self.hostels.values():
                entries = [
                    {"menu_date": str(monday + timedelta(days=d)), "meal": meal, "items": items[(d + week) % 7]}
                    for d in range(7)
                    for meal, items in content.MENU.items()
                ]
                self.api.post(f"/admin/hostels/{hostel.id}/mess-menu/entries", self.admin, json={"entries": entries})
            monday += timedelta(days=7)
            week += 1
        self.count("weeks of mess menu", week)

    # ---- history: planning ------------------------------------------------------------------------

    def days(self) -> list[date]:
        first = self.h0 - timedelta(days=5)
        return [first + timedelta(days=i) for i in range((self.today - first).days + 1)]

    def plan(self) -> None:
        for day in self.days():
            self.timeline.at(local(day, 0, 20), self.nightly)
        for month in self.months:
            self.timeline.at(local(month, 6), lambda mo=month: self.charge_run(mo))
        self.plan_money()
        self.plan_leave()
        self.plan_complaints()
        self.plan_announcements()
        if self.spec.extras:
            self.plan_extras()

    def nightly(self) -> None:
        """What the worker does every night: late fees, old complaints, reservations."""
        with self.tx(worker=True) as db:
            self.count("late fees", finance.apply_late_fees(db, allocations.local_today(db)))
            auto_close(db)
            allocations.expire_reservations(db)

    def charge_run(self, month: date) -> None:
        by = self.admin.user_id
        with self.tx(worker=True) as db:
            for hostel in self.hostels.values():
                for fee_id in hostel.fee.values():
                    self.count("charges", finance.run_charges(db, uuid.UUID(fee_id), month, by))

    # ---- money --------------------------------------------------------------------------------------

    def office_time(self, day: date) -> datetime:
        return local(day, self.rng.randint(10, 16), self.rng.randint(0, 59))

    def plan_money(self) -> None:
        acc = self.admin
        last_full = self.months[-2]
        for st in self.students.values():
            deposit_day = self.h0 - timedelta(days=self.rng.randint(0, 1))
            if st.profile != "defaulter" or self.rng.random() < 0.5:
                self.timeline.at(
                    self.office_time(deposit_day), lambda s=st: self.pay(s, acc, deposit=self.spec.deposit)
                )
            for month in self.months:
                if st.profile == "prompt":
                    self.timeline.at(
                        self.office_time(month + timedelta(days=st.pay_day - 1)), lambda s=st: self.pay(s, acc)
                    )
                elif st.profile == "upi":
                    when = local(
                        month + timedelta(days=st.pay_day - 1), self.rng.randint(8, 22), self.rng.randint(0, 59)
                    )
                    self.timeline.at(when, lambda s=st, w=when: self.upi(s, w))
                elif st.profile == "late":
                    self.timeline.at(
                        self.office_time(month + timedelta(days=self.rng.randint(17, 25))),
                        lambda s=st: self.pay(s, acc),
                    )
                elif st.profile == "behind":
                    if month < last_full:
                        self.timeline.at(
                            self.office_time(month + timedelta(days=self.rng.randint(19, 26))),
                            lambda s=st: self.pay(s, acc),
                        )
                    elif month == last_full and self.rng.random() < 0.5:  # part of last month, nothing since
                        self.timeline.at(
                            self.office_time(month + timedelta(days=24)), lambda s=st: self.pay(s, acc, part="2000.00")
                        )
                elif month == self.months[0]:  # the defaulter paid once, late
                    self.timeline.at(self.office_time(month + timedelta(days=22)), lambda s=st: self.pay(s, acc))

    def dues(self, st: Student, who: Actor) -> Decimal:
        return money(self.api.get(f"/admin/students/{st.id}/balance", who)["dues"])

    def pay(self, st: Student, acc: Actor, *, deposit: str | None = None, part: str | None = None) -> str | None:
        """The first Administrator records a payment: everything owed right now, or the deposit, or a part."""
        if st.gone_at:
            return None
        owed = self.dues(st, acc)
        amount = Decimal("0") if deposit else min(owed, money(part)) if part else owed
        if amount <= 0 and not deposit:
            return None
        method = self.rng.choices(("cash", "upi", "bank_transfer"), (45, 35, 20))[0]
        reference = (
            None if method == "cash" else f"{'UTR' if method == 'upi' else 'NEFT'}{self.rng.randrange(10**11, 10**12)}"
        )
        made = self.api.post(
            "/admin/payments",
            acc,
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={
                "student_id": st.id,
                "method": method,
                "reference": reference,
                "received_on": str(self.timeline.now.astimezone(IST).date()),
                "dues_amount": str(amount),
                "deposit_amount": deposit or "0",
            },
        )
        self.count("payments")
        return str(made["id"])

    def upi(self, st: Student, when: datetime) -> None:
        """The student pays by UPI and submits the reference; an Administrator verifies it the next morning."""
        if st.gone_at:
            return
        owed = money(self.api.get("/portal/me/fees", st.actor)["balance"]["dues"])
        if owed <= 0:
            return
        sub = self.api.post(
            "/portal/me/payment-submissions",
            st.actor,
            json={
                "amount": str(owed),
                "utr": f"{self.rng.randrange(10**11, 10**12)}",
                "paid_on": str(when.astimezone(IST).date()),
            },
        )
        self.count("UPI submissions")
        acc = self.admin
        review = local(when.astimezone(IST).date() + timedelta(days=1), 10, self.rng.randint(0, 50))
        self.timeline.at(review, lambda: self.api.post(f"/admin/payment-submissions/{sub['id']}/verify", acc))

    # ---- leave (no gate passes since D75) ---------------------------------------------------------

    def plan_leave(self) -> None:
        monday = self.h0 - timedelta(days=self.h0.weekday())
        while monday <= self.today:
            for hostel in self.hostels.values():
                n = len(hostel.students)
                for _ in range(max(1, round(n * 0.045))):  # home for the weekend
                    start = (
                        local(monday + timedelta(days=4), 17)
                        if self.rng.random() < 0.6
                        else local(monday + timedelta(days=5), 8)
                    )
                    asked = local(
                        monday + timedelta(days=self.rng.randint(1, 3)),
                        self.rng.randint(9, 20),
                        self.rng.randint(0, 59),
                    )
                    self.leave(hostel, "home", asked, start, local(monday + timedelta(days=6), 19))
                for _ in range(max(1, round(n * 0.06))):  # a local outing on a weekday
                    day = monday + timedelta(days=self.rng.randint(0, 4))
                    asked = local(day, self.rng.randint(8, 10), self.rng.randint(0, 59))
                    self.leave(hostel, "local_outing", asked, local(day, 16), local(day, 19, 30))
            monday += timedelta(days=7)
        # Around today: people on leave right now, requests waiting for a decision, a leave from tomorrow.
        t = self.today
        first = self.hostels[self.spec.hostels[0].code]
        self.leave(
            first,
            "home",
            local(t - timedelta(days=2), 18),
            local(t - timedelta(days=1), 17),
            local(t + timedelta(days=2), 19),
            who=self.demo[2],
            sure=True,
        )
        for hostel in self.hostels.values():
            for _ in range(2):
                self.leave(
                    hostel,
                    "home",
                    local(t - timedelta(days=2), 11),
                    local(t - timedelta(days=1), 16),
                    local(t + timedelta(days=1), 20),
                    sure=True,
                )
            self.leave(
                hostel,
                "home",
                local(t - timedelta(days=1), 19),
                local(t + timedelta(days=1), 9),
                local(t + timedelta(days=3), 19),
                sure=True,
            )
            for _ in range(2):
                asked = self.timeline.until - timedelta(minutes=self.rng.randint(20, 200))
                self.leave(
                    hostel,
                    "home",
                    asked,
                    local(t + timedelta(days=3), 17),
                    local(t + timedelta(days=5), 19),
                    decide=False,
                )

    def leave(
        self,
        hostel: Hostel,
        kind: str,
        asked: datetime,
        start: datetime,
        end: datetime,
        *,
        who: Student | None = None,
        sure: bool = False,
        decide: bool = True,
    ) -> None:
        """Plan one leave: asked by the student, decided by an Administrator. `sure` leaves are always
        approved; the others include refusals and withdrawals."""
        rng = self.rng
        fate = "approved" if sure else rng.choices(("approved", "rejected", "withdrawn"), (91, 6, 3))[0]
        decided = asked + timedelta(minutes=rng.randint(20, 240))
        if kind == "local_outing":
            decided = min(decided, start - timedelta(hours=3))
        state: dict[str, Any] = {}

        def ask() -> None:
            candidates = [
                s
                for s in hostel.students
                if not s.gone_at and s.role is None and (s.busy_until is None or s.busy_until < asked)
            ]
            st = who or (rng.choice(candidates) if candidates else None)
            if st is None:
                return
            reason, destination = rng.choice(
                content.LEAVE_REASONS_HOME if kind == "home" else content.LEAVE_REASONS_LOCAL
            )
            body = {
                "leave_type": kind,
                "starts_at": start.isoformat(),
                "ends_at": end.isoformat(),
                "reason": reason,
                "destination": destination,
            }
            state["leave"] = self.api.post("/portal/me/leave-requests", st.actor, json=body)
            state["student"] = st
            st.busy_until = end + timedelta(hours=3)
            self.count("leave requests")

        def answer() -> None:
            if "leave" not in state or state["leave"]["status"] != "pending":
                return
            leave_id, st = state["leave"]["id"], state["student"]
            if fate == "withdrawn":
                self.api.post(f"/portal/me/leave-requests/{leave_id}/cancel", st.actor)
                st.busy_until = decided
            elif fate == "rejected":
                note = rng.choice(content.LEAVE_REJECTIONS)
                self.api.post(
                    f"/admin/leave-requests/{leave_id}/reject",
                    self.admin,
                    json={"note": note},
                )
                st.busy_until = decided
            else:
                state["leave"] = self.api.post(f"/admin/leave-requests/{leave_id}/approve", self.admin, json={})

        self.timeline.at(asked, ask)
        if decide:
            self.timeline.at(decided, answer)
            self.timeline.at(decided, lambda: self.count("leave approved") if self._approved(state) else None)

    @staticmethod
    def _approved(state: dict[str, Any]) -> bool:
        return bool(state.get("leave", {}).get("status") == "approved")

    # ---- complaints ---------------------------------------------------------------------------------

    def plan_complaints(self) -> None:
        span = (self.today - self.h0).days + 1
        for hostel in self.hostels.values():
            for _ in range(round(len(hostel.students) * 0.005 * span)):
                day = self.h0 + timedelta(days=self.rng.randrange(span))
                self.complaint(hostel, local(day, self.rng.randint(7, 22), self.rng.randint(0, 59)))

    def complaint(self, hostel: Hostel, raised: datetime) -> None:
        rng = self.rng
        category = rng.choice(content.COMPLAINT_CATEGORIES)
        title, description, fix = rng.choice(content.COMPLAINTS[category])
        priority = rng.choices(("low", "medium", "high", "urgent"), (20, 50, 22, 8))[0]
        days_allowed = {"low": 7, "medium": 4, "high": 2, "urgent": 1}[priority]
        assigner = worker = self.admin  # the one Administrator assigns the work to themselves (Q40, D76)
        assigned = raised + timedelta(minutes=rng.randint(30, 600))
        started = assigned + timedelta(minutes=rng.randint(60, 1500))
        resolved = max(
            started + timedelta(minutes=30), raised + timedelta(hours=24 * days_allowed * rng.uniform(0.25, 1.35))
        )
        stuck = rng.random() < 0.04  # never dealt with: the overdue list
        state: dict[str, Any] = {}

        def raise_it() -> None:
            people = [s for s in hostel.students if not s.gone_at]
            st = rng.choice(people)
            body = {
                "category_id": self.categories[category],
                "title": title,
                "description": description,
                "priority": priority,
            }
            state["id"], state["student"] = self.api.post("/portal/me/complaints", st.actor, json=body)["id"], st
            self.count("complaints")

        def step(action: str, who: Actor, body: dict[str, Any] | None = None) -> None:
            if "id" in state:
                self.api.post(f"/admin/complaints/{state['id']}/{action}", who, json=body or {})

        def reopen() -> None:
            if "id" in state:
                self.api.post(
                    f"/portal/me/complaints/{state['id']}/reopen",
                    state["student"].actor,
                    json={"comment": content.STILL_OPEN_NOTE},
                )

        self.timeline.at(raised, raise_it)
        if stuck:
            return
        self.timeline.at(assigned, lambda: step("assign", assigner, {"user_id": str(worker.user_id)}))
        self.timeline.at(started, lambda: step("start", worker))
        self.timeline.at(resolved, lambda: step("resolve", worker, {"note": fix}))
        if rng.random() < 0.08:
            again = resolved + timedelta(hours=rng.randint(8, 60))
            self.timeline.at(again, reopen)
            self.timeline.at(
                again + timedelta(hours=rng.randint(20, 50)),
                lambda: step("resolve", worker, {"note": f"Checked again. {fix}"}),
            )

    # ---- announcements ----------------------------------------------------------------------

    def plan_announcements(self) -> None:
        for days_ago, audience, hostel_index, title, body in content.ANNOUNCEMENTS:
            when = local(self.today - timedelta(days=days_ago), 10, self.rng.randint(0, 50))
            if when >= local(self.h0, 0):
                self.timeline.at(when, lambda a=audience, i=hostel_index, t=title, b=body: self.announce(a, i, t, b))

    def announce(self, audience: str, hostel_index: int | None, title: str, body: str) -> None:
        hostel = self.hostels[self.spec.hostels[hostel_index].code] if hostel_index is not None else None
        who = self.admin
        made = self.api.post(
            "/admin/announcements",
            who,
            json={"title": title, "body": body, "audience": audience, "hostel_id": hostel.id if hostel else None},
        )
        # The worker's fan-out job, run here so each notification carries the announcement's date. The
        # queued job still runs later and finds nothing left to do (it skips people who already have it).
        with self.tx(worker=True) as db:
            fanout(
                db,
                jobs.ClaimedJob(
                    uuid.uuid4(), self.org_id, "announcements.fanout", {"announcement_id": made["id"]}, 0, 1, 5
                ),
            )
        self.count("announcements")

    # ---- the fuller story of the main organization -------------------------------------------------

    def plan_extras(self) -> None:
        acc, acc2, m = self.admin, self.admin, self.admin
        m1, m2 = self.months[1], self.months[2]
        pool = [s for s in self.students.values() if s.role is None and s.profile == "prompt"]
        self.rng.shuffle(pool)
        scholars, fined, leavers, movers, mistaken = pool[:3], pool[3:6], pool[6:9], pool[9:11], pool[11]

        def waive(st: Student) -> None:
            charges = self.api.get(f"/admin/students/{st.id}/balance", acc)["open_charges"]
            rent = next((c for c in charges if c["description"].startswith("Hostel rent")), None)
            if rent:
                body = {
                    "target_entry_id": rent["entry_id"],
                    "amount": "1500.00",
                    "reason_code": "scholarship",
                    "reason_note": "Merit scholarship, office order 14",
                }
                self.api.post(f"/admin/students/{st.id}/waivers", acc, json=body)
                self.count("waivers")

        for st in scholars:
            for month in self.months[1:]:
                self.timeline.at(local(month, 9, 10), lambda s=st: waive(s))

        def fine(st: Student, what: tuple[str, str, str, str]) -> None:
            description, amount, code, note = what
            due = self.timeline.now.astimezone(IST).date() + timedelta(days=7)
            body = {
                "kind": "fine",
                "amount": amount,
                "description": description,
                "due_date": str(due),
                "reason_code": code,
                "reason_note": note,
            }
            self.api.post(f"/admin/students/{st.id}/charges", acc, json=body)
            self.count("fines")

        for st, what in zip(fined, content.FINES, strict=True):
            self.timeline.at(
                self.office_time(m1 + timedelta(days=self.rng.randint(12, 50))), lambda s=st, w=what: fine(s, w)
            )

        def wrong_payment() -> None:  # recorded against the wrong student, reversed, recorded correctly (Q31)
            victim = next((s for s in self.students.values() if s.profile == "behind" and s.role is None), None)
            if victim is None or self.dues(victim, acc) < money("1000"):
                return
            payment = self.pay(victim, acc, part="1000.00")
            self.api.post(
                f"/admin/payments/{payment}/reverse",
                acc,
                json={"reason": f"Recorded against the wrong student; it was paid by {mistaken.code}"},
            )
            self.count("payment reversals")

        self.timeline.at(local(m2 + timedelta(days=mistaken.pay_day - 1), 9, 40), wrong_payment)

        def check_out(st: Student) -> None:
            stays = self.api.get(f"/admin/students/{st.id}/allocations", m)
            active = next(a for a in stays if a["status"] == "active")
            self.api.post(f"/admin/allocations/{active['id']}/check-out", m)
            st.gone_at = self.timeline.now

        def refund(st: Student, amount: Decimal, *, approve: bool) -> None:
            body = {
                "student_id": st.id,
                "account": "deposit",
                "amount": str(amount),
                "reason": "Leaving the hostel: deposit refund",
            }
            request = self.api.post("/admin/refund-requests", acc, json=body)
            self.count("refund requests")
            if approve:  # the one Administrator approves their own request (self-approved, D75/D76), then pays it
                self.api.post(
                    f"/admin/refund-requests/{request['id']}/approve",
                    acc2,
                    json={"note": "Room inspected; nothing to deduct"},
                )
                self.api.post(
                    f"/admin/refund-requests/{request['id']}/mark-paid",
                    acc,
                    json={"method": "bank_transfer", "reference": f"NEFT{self.rng.randrange(10**11, 10**12)}"},
                )

        def leave_for_good(st: Student, reason: str, note: str | None) -> None:
            body = {"exited_on": str(self.timeline.now.astimezone(IST).date()), "exit_reason": reason}
            self.api.post(f"/admin/students/{st.id}/exit", m, json=body | ({"outstanding_note": note} if note else {}))
            self.count("exits")

        # 1. A clean exit: everything paid, the deposit refunded, then the exit.
        a, b, c = leavers
        day = m1 + timedelta(days=13)
        self.timeline.at(local(day, 10), lambda: check_out(a))
        self.timeline.at(local(day, 11), lambda: refund(a, money(self.spec.deposit), approve=True))
        self.timeline.at(local(day, 15), lambda: leave_for_good(a, "transferred_out", None))
        # 2. Leaves with the refund still waiting for approval: the exit needs a reason (Q93).
        day = self.today - timedelta(days=3)
        self.timeline.at(local(day, 10), lambda: check_out(b))
        self.timeline.at(local(day, 11), lambda: refund(b, money(self.spec.deposit), approve=False))
        self.timeline.at(
            local(day, 12),
            lambda: leave_for_good(b, "withdrawn", "Deposit refund requested; not approved yet"),
        )
        # 3. Leaves owing money: a fine first, then the exit with a reason; the dues stay on the account.
        day = m2 + timedelta(days=20)
        self.timeline.at(
            local(day, 9),
            lambda: fine(
                c, ("Damaged cupboard door", "800.00", "damage", "Found at the room inspection before leaving")
            ),
        )
        self.timeline.at(local(day, 10), lambda: check_out(c))
        self.timeline.at(
            local(day, 12),
            lambda: leave_for_good(c, "withdrawn", "Left mid-term; the family will pay the fine by bank transfer"),
        )

        def transfer(st: Student) -> None:
            hostel = self.hostels[st.hostel]
            board = self.api.get(f"/admin/hostels/{hostel.id}/bed-board", m)
            free = [
                bed["id"]
                for room in board["rooms"]
                if room["room_type"] == st.room_type
                for bed in room["beds"]
                if bed["state"] == "vacant"
            ]
            stays = self.api.get(f"/admin/students/{st.id}/allocations", m)
            active = next(x for x in stays if x["status"] == "active")
            if free:
                self.api.post(f"/admin/allocations/{active['id']}/transfer", m, json={"bed_id": free[-1]})
                self.count("room transfers")

        for st in movers:
            self.timeline.at(self.office_time(m1 + timedelta(days=self.rng.randint(5, 55))), lambda s=st: transfer(s))

    # ---- the present (at the real time again) ------------------------------------------------------

    def present(self) -> None:
        """Things that exist only now: beds reserved for students arriving next week, UPI payments no
        Administrator has looked at yet."""
        acc = self.admin
        for hostel in self.hostels.values():
            board = self.api.get(f"/admin/hostels/{hostel.id}/bed-board", self.admin)
            free = [bed for room in board["rooms"] for bed in room["beds"] if bed["state"] == "vacant"]
            for bed in free[-2:]:
                st = self.admit(hostel, None, self.today, activate=False)
                body = {
                    "student_id": st.id,
                    "bed_id": bed["id"],
                    "mode": "reserve",
                    "start_date": str(self.today + timedelta(days=5)),
                    "reservation_expires_at": (self.real_now + timedelta(days=6)).isoformat(),
                }
                self.api.post("/admin/allocations", self.admin, json=body)
                self.count("reservations")
            waiting = [
                s for s in hostel.students if s.profile in ("late", "behind") and not s.gone_at and s.role is None
            ][:3]
            for i, st in enumerate(waiting):
                owed = money(self.api.get("/portal/me/fees", st.actor)["balance"]["dues"])
                if owed > 0:
                    body = {
                        "amount": str(min(owed, money("5000"))),
                        "utr": f"{self.rng.randrange(10**11, 10**12)}",
                        "paid_on": str(self.today),
                    }
                    sub = self.api.post("/portal/me/payment-submissions", st.actor, json=body)
                    self.count("UPI submissions")
                    if i == 2:
                        self.api.post(
                            f"/admin/payment-submissions/{sub['id']}/reject",
                            acc,
                            json={"note": "No such reference on the bank statement; please check the UTR"},
                        )

    def registration_requests(self) -> None:
        """D74: the organization accepts registration requests, and some wait for the Administrator.
        Each is sent through the public form, as a student would; the email link is then followed with
        a token made by the email job's own function (no mailbox needed)."""
        from app.modules.registration import emails as request_emails
        from app.modules.registration.models import StudentRegistrationRequest

        s, api = self.spec, self.api
        if not s.pending_requests:
            return
        org = api.get("/admin/organization", self.admin)
        api.patch("/admin/organization", self.admin, json={"version": org["version"], "accepts_student_requests": True})
        hostels = {h["name"]: h for h in api.get("/admin/hostels", self.admin)}
        programs = ("B.Tech Computer Science", "B.Tech Mechanical", "B.Sc Physics", "BBA", "B.Com")
        for i in range(s.pending_requests):
            hostel = self.hostels[s.hostels[i % len(s.hostels)].code]
            gender = "female" if hostel.spec.resident_type == "girls" else "male"
            name, group = self.person_name(gender)
            first, last = name.split()[0], name.split()[1]
            email = f"{first.lower()}.{last.lower()}.applicant@example.com"
            api.post(
                f"/public/organizations/{s.slug}/student-registration",
                data={
                    "hostel_id": hostels[hostel.spec.name]["id"],
                    "full_name": name,
                    "email": email,
                    "phone": f"+91 9{self.rng.randrange(100000000, 999999999)}",
                    "gender": gender,
                    "date_of_birth": str(date(self.today.year - 18, 1 + i, 10 + i)),
                    "program": programs[i % len(programs)],
                    "year_of_study": "1",
                    "guardian_name": f"{self.rng.choice(group.male)} {last}",
                    "guardian_relationship": "father",
                    "guardian_phone": f"+91 9{self.rng.randrange(100000000, 999999999)}",
                    "password": STUDENT_PASSWORD,
                },
                files={"photo": ("photo.jpg", avatar(name), "image/jpeg")},
            )
            with self.tx() as db:
                row = db.scalars(
                    select(StudentRegistrationRequest).where(StudentRegistrationRequest.email == email)
                ).one()
                token = request_emails.new_request_token(db, row)
            api.post(f"/o/{s.slug}/auth/student-registration/verify", json={"token": token})
            self.applicants.append({"name": name, "email": email, "hostel": hostel.spec.name})
            self.count("registration requests waiting")

    def finish(self) -> None:
        with self.tx() as db:  # the seed's own sessions end here; the demo logins start their own
            for i in range(0, len(self.seed_sessions), 200):
                sessions.revoke(db, "logout", UserSession.id.in_(self.seed_sessions[i : i + 200]))

    def read_older_notifications(self) -> None:
        """A real administrator reads their notifications: three months of history would otherwise leave
        hundreds unread (the bell said 99+). The newest few stay unread, so the demo shows a real badge.
        Through the API, as the Administrator would: POST /me/notifications/{id}/read."""
        unread: list[str] = []
        cursor = ""
        while True:
            page = self.api.get(
                f"/me/notifications?unread_only=true&limit=200{f'&cursor={cursor}' if cursor else ''}", self.admin
            )
            unread += [n["id"] for n in page["items"]]  # newest first
            cursor = page["next_cursor"] or ""
            if not cursor:
                break
        for nid in unread[ADMIN_UNREAD_LEFT:]:
            self.api.post(f"/me/notifications/{nid}/read", self.admin)
        self.count("notifications the Administrator left unread", min(len(unread), ADMIN_UNREAD_LEFT))

    def build(self) -> None:
        steps: tuple[tuple[str, Callable[[], None]], ...] = (
            ("register the organization and its first Administrator", self.register),
            ("hostels, rooms and fees", self.set_up),
            ("admit and activate students", self.admit_everyone),
            ("identity documents", self.documents),
            ("mess menus", self.mess_menus),
            ("plan the history", self.plan),
            ("replay the history", self.timeline.run),
            ("the present", self.present),
            ("student registration requests", self.registration_requests),
            ("the Administrator reads older notifications", self.read_older_notifications),
            ("close the seed's sessions", self.finish),
        )
        print(f"\n{self.spec.name} ({self.spec.slug})", flush=True)
        for label, fn in steps:
            started = time.monotonic()
            fn()
            print(f"  {label}: {time.monotonic() - started:.0f}s", flush=True)


# --- the Platform Owner -----------------------------------------------------------------------------


def platform_owner(api: Api) -> None:
    """The demo Platform Owner, then one sign-in to prove the password works (D87: no authenticator)."""
    cli.create_platform_owner(OWNER_EMAIL, OWNER_NAME, OWNER_PASSWORD)
    api.post("/platform/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})


# --- reset --------------------------------------------------------------------------------------------


def existing(conn: psycopg.Connection[Any]) -> list[tuple[uuid.UUID, str, str, bool]]:
    """The organizations on the demo slugs: (id, slug, name, carries the demo marker)."""
    rows = conn.execute(
        "SELECT o.id, o.slug, o.name, coalesce(p.legal_name, '') LIKE %s FROM organizations o "
        "LEFT JOIN organization_profiles p ON p.org_id = o.id WHERE o.slug = ANY(%s) ORDER BY o.slug",
        (f"%{MARKER}%", [o.slug for o in ORGS]),
    ).fetchall()
    return [(r[0], r[1], r[2], bool(r[3])) for r in rows]


def superuser() -> psycopg.Connection[Any]:
    return psycopg.connect(get_settings().conninfo("postgres"))


def refuse_foreign(found: list[tuple[uuid.UUID, str, str, bool]]) -> None:
    foreign = [f"'{slug}' ({name})" for _, slug, name, marked in found if not marked]
    if foreign:
        raise SeedError(
            "An organization that the demo seed did NOT create already uses the address "
            + ", ".join(foreign)
            + ". Nothing was changed. Rename or remove it yourself first;"
            + " the seed never deletes an organization it didn't make."
        )


def reset() -> int:
    """Remove the demo organizations and the demo Platform Owner. Returns how many organizations went."""
    with superuser() as conn:
        found = existing(conn)
        refuse_foreign(found)
        ids = [org_id for org_id, *_ in found]
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'org_id' "
                "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r' ORDER BY c.relname"
            )
        ]
        # Append-only tables (ledger, payments, audit log) refuse DELETE through triggers, and foreign
        # keys would force a delete order. "replica" switches both off for THIS connection only. Every
        # statement below names the demo organizations' IDs (or the demo owner's ID) explicitly.
        conn.execute("SET session_replication_role = replica")
        if ids:
            for table in tables:
                conn.execute(sql.SQL("DELETE FROM {} WHERE org_id = ANY(%s)").format(sql.Identifier(table)), (ids,))
            conn.execute("DELETE FROM organizations WHERE id = ANY(%s)", (ids,))
        owner = conn.execute("SELECT id FROM platform_users WHERE lower(email) = %s", (OWNER_EMAIL,)).fetchone()
        if owner:
            conn.execute("DELETE FROM platform_sessions WHERE platform_user_id = %s", (owner[0],))
            conn.execute("UPDATE platform_settings SET updated_by = NULL WHERE updated_by = %s", (owner[0],))
            conn.execute("DELETE FROM audit_logs WHERE actor_platform_user_id = %s", (owner[0],))
            conn.execute("DELETE FROM platform_users WHERE id = %s", (owner[0],))
        conn.execute("SET session_replication_role = origin")
    uploads = Path(get_settings().hms_upload_dir)
    for org_id in ids:  # uploaded files live under <uploads>/<organization id>/
        shutil.rmtree(uploads / str(org_id), ignore_errors=True)
    return len(ids)


def status() -> None:
    with superuser() as conn:
        found = existing(conn)
        for org_id, slug, name, marked in found:
            n = conn.execute("SELECT count(*) FROM students WHERE org_id = %s", (org_id,)).fetchone()
            kind = "demo data" if marked else "NOT demo data (the seed will refuse to touch it)"
            print(f"{slug}: {name}, {n[0] if n else 0} students, {kind}")
        if not found:
            print("No demo data: neither demo organization exists.")
        owner = conn.execute("SELECT 1 FROM platform_users WHERE lower(email) = %s", (OWNER_EMAIL,)).fetchone()
        print(f"Demo Platform Owner: {'exists' if owner else 'does not exist'}")


# --- demo-logins.md -------------------------------------------------------------------------------------


def write_logins(out: Path, builders: list[Builder], took: float, now: datetime) -> None:
    lines = [
        "# Demo logins",
        "",
        f"Made by `./scripts/demo-data.sh create` on {now.astimezone(IST):%d-%m-%Y at %H:%M}"
        f" (it took {took / 60:.1f} minutes).",
        "Everything here is sample data. These passwords are for the demo on this computer only.",
        f"The passwords come from `{PASSWORDS_FILE}` in this folder and stay the same on every run;"
        " change them there. Sign-in is the email and the password only (D87: no two-step verification).",
        "",
        "Web app: <http://localhost:5173>",
        "",
    ]

    lines += [
        "## Platform Owner",
        "",
        "- Sign in at <http://localhost:5173/platform/login>",
        f"- Email: `{OWNER_EMAIL}`",
        f"- Password: `{OWNER_PASSWORD}`",
        "",
    ]
    for b in builders:
        s = b.spec
        lines += [
            f"## {s.name}",
            "",
            f"- Sign in at <http://localhost:5173/o/{s.slug}/login>",
            f"- The Administrator's password: `{STAFF_PASSWORD}`",
            f"- Every student password: `{STUDENT_PASSWORD}`",
            "",
            '### The Administrator ("Organization sign-in": the email and the password, D78)',
            "",
            "The organization's one Administrator (D76): every hostel, everything. A forgotten password is"
            " reset with the 6-digit code that Forgot password emails.",
            "",
            "| Role | Name | Email (Employee ID E-0001 on the old /o/ page) |",
            "|---|---|---|",
            *(f"| {p['role']} | {p['name']} | `{p['id']}` |" for p in b.staff_logins),
            "",
            '### Students ("Student sign-in": the email and the password, D80)',
            "",
            "| Email | Student ID | Name | Hostel | What to show |",
            "|---|---|---|---|---|",
            *(f"| `{st.email}` | `{st.code}` | {st.name} | {st.hostel} | {st.role} |" for st in b.demo),
            "",
            "Any other student's email from the Students page works with the same student password.",
            "",
            *(
                [
                    "### Registration requests waiting (D74; Student sign-in with the email shows they wait)",
                    "",
                    "Each applicant's password is the student password above. Approve or reject them under"
                    " Registration requests.",
                    "",
                    "| Name | Email | Hostel |",
                    "|---|---|---|",
                    *(f"| {a['name']} | `{a['email']}` | {a['hostel']} |" for a in b.applicants),
                    "",
                ]
                if b.applicants
                else []
            ),
            "### What was created",
            "",
            *(f"- {what}: {n}" for what, n in sorted(b.counts.items())),
            "",
        ]
    lines += [
        "## Good to know",
        "",
        "- Every audit entry of the demo data carries the time it was written (today), although the business"
        " dates go back three months. That is by design: nothing can backdate the audit log.",
        "- The worker keeps working on the demo data in real time (late fees, closing old complaints)."
        " Rebuild the data on the day of the presentation so it is fresh.",
        "",
    ]
    out.mkdir(parents=True, exist_ok=True)
    (out / "demo-logins.md").write_text("\n".join(lines), encoding="utf-8")


# --- command line -------------------------------------------------------------------------------------


def load_passwords(folder: Path) -> dict[str, str]:
    """The demo passwords from `<secrets folder>/demo-passwords.env` (KEY=value lines; # comments).

    Checked with the application's own password rules first (8 characters for everyone since D87; no
    common passwords; no word of an organization's or a person's name),
    so a bad file stops the run before the old demo data is removed."""
    path = folder / PASSWORDS_FILE
    if not path.is_file():
        raise SeedError(
            f"{PASSWORDS_FILE} is missing from the secrets folder. Create it there (never in the project) with:\n"
            + "\n".join(f"  {key}=...   # {who}" for key, who in PASSWORD_KEYS.items())
        )
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            found[key.strip()] = value.strip()
    missing = [key for key in PASSWORD_KEYS if not found.get(key)]
    if missing:
        raise SeedError(f"{PASSWORDS_FILE} has no value for: {', '.join(missing)}")
    org_words = [w for spec in ORGS for w in (spec.slug, *spec.name.split(), *spec.admin_name.split(), "admin")]
    checks = (
        ("HMS_DEMO_ADMIN_PASSWORD", org_words),
        ("HMS_DEMO_OWNER_PASSWORD", [*OWNER_NAME.split(), OWNER_EMAIL.split("@")[0], "platform"]),
        ("HMS_DEMO_STUDENT_PASSWORD", org_words),
    )
    for key, context in checks:
        problems = passwords.check_new_password(found[key], context=context)
        if problems:
            raise SeedError(f"{key} in {PASSWORDS_FILE} is refused: {problems[0].message}")
    return {key: found[key] for key in PASSWORD_KEYS}


def use_passwords(found: dict[str, str]) -> None:
    global STAFF_PASSWORD, STUDENT_PASSWORD, OWNER_PASSWORD
    STAFF_PASSWORD = found["HMS_DEMO_ADMIN_PASSWORD"]
    OWNER_PASSWORD = found["HMS_DEMO_OWNER_PASSWORD"]
    STUDENT_PASSWORD = found["HMS_DEMO_STUDENT_PASSWORD"]


def scaled(spec: OrgSpec, percent: int) -> OrgSpec:
    """A smaller organization, for trying the seed out quickly."""
    hostels = tuple(replace(h, students=max(14, h.students * percent // 100)) for h in spec.hostels)
    return replace(spec, hostels=hostels)


def create(out: Path, percent: int = 100) -> None:
    started = time.monotonic()
    use_passwords(load_passwords(out))  # before reset: a bad file must not cost the old demo data
    removed = reset()
    print(f"reset: removed {removed} demo organization(s)" if removed else "reset: nothing to remove")
    # History must not send thousands of emails: in-app notifications are still written, their email
    # jobs are not queued (this process only; the application itself is untouched).
    notifications.email_enabled = lambda db, user_id, category: False  # type: ignore[assignment]
    now = datetime.now(UTC)
    api = Api()
    builders = [Builder(scaled(spec, percent), api, now) for spec in ORGS]
    for b in builders:
        b.build()
    platform_owner(api)
    took = time.monotonic() - started
    write_logins(out, builders, took, now)
    print(f"\nDone in {took / 60:.1f} minutes, {api.calls} API calls. Logins: demo-logins.md in the secrets folder.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.demo.seed", description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("create", "reset", "status"))
    parser.add_argument(
        "--out", type=Path, default=Path("/demo-out"), help="folder for demo-logins.md (the secrets folder)"
    )
    parser.add_argument("--percent", type=int, default=100, help="students as a percentage of the full size (try-outs)")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            create(args.out, args.percent)
        elif args.command == "reset":
            print(f"removed {reset()} demo organization(s) and the demo Platform Owner")
        else:
            status()
    except SeedError as exc:
        print(f"\nSTOPPED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
