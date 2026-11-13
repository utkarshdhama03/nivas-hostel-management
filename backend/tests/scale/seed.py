"""Scale test, part 1: synthetic data at the design target (Q3: 50 organizations x 5,000 students;
Q79: Faker for names). Run it through scripts/scale-test.sh.

It writes to a SEPARATE database (hms_scale), never hms or hms_test, as the postgres superuser.
That is acceptable for load data (the security suite proves the access rules); every constraint
and trigger in the schema still applies, so the data is shaped like real data.

Per organization (defaults): 4 hostels, 1 building x 10 floors each, triple rooms with about 10%
spare beds; 5,000 students (about 96% with an active bed, 80% activated accounts); rent charged
monthly for August to November; one payment per paid month with a receipt and an allocation (about
86% of students pay 1 to 4 months, so dues exist); 10% of students with a complaint; 2 audit entries
per student. (Attendance went in D75.)
"""

from __future__ import annotations

import argparse
import math
import time
import uuid
from pathlib import Path
from typing import Any

import psycopg
from alembic import command
from alembic.config import Config
from faker import Faker
from psycopg import sql

from app.core.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2NhbGUtdGVzdA$c2NhbGUtdGVzdC1ub3QtYS1yZWFsLWhhc2g"  # never matches


def reset_database(name: str) -> None:
    if not name.startswith("hms_scale"):
        raise SystemExit("refusing: the scale test only writes to a database named hms_scale*")
    s = get_settings()
    with psycopg.connect(s.conninfo("postgres", "postgres"), autocommit=True) as su:
        su.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        su.execute(sql.SQL("CREATE DATABASE {} OWNER hms_owner").format(sql.Identifier(name)))
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["database"] = name
    command.upgrade(cfg, "head")


def _one(cur: psycopg.Cursor[Any], q: str, p: dict[str, Any]) -> Any:
    row = cur.execute(q, p).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return row[0]


def seed_org(
    conn: psycopg.Connection[Any], n: int, args: argparse.Namespace, first: list[str], last: list[str]
) -> None:
    slug = f"scale-{n:02d}"
    hostels, floors = args.hostels, 10
    per_hostel = math.ceil(args.students / hostels)
    rooms_per_floor = math.ceil(math.ceil(per_hostel * 1.1) / 3 / floors)
    with conn.transaction(), conn.cursor() as cur:
        o = _one(
            cur,
            "INSERT INTO organizations (slug, name, status, email_verified_at, created_at) "
            "VALUES (%(slug)s, %(name)s, 'active', now(), now() - interval '200 days') RETURNING id",
            {"slug": slug, "name": f"Scale Org {n:02d}"},
        )
        p: dict[str, Any] = {"o": o, "slug": slug, "hash": DUMMY_HASH, "first": first, "last": last}
        cur.execute(
            "INSERT INTO organization_profiles (org_id, contact_email) "
            "VALUES (%(o)s, 'office@' || %(slug)s || '.example.com')",
            p,
        )
        p["sess"] = _one(
            cur,
            "INSERT INTO academic_sessions (org_id, name, starts_on, ends_on, is_current) "
            "VALUES (%(o)s, '2026-27', '2026-06-01', '2027-05-31', true) RETURNING id",
            p,
        )

        def staff(name: str, code: str, role: str, hostel: uuid.UUID | None = None) -> uuid.UUID:
            uid = _one(
                cur,
                "INSERT INTO users (org_id, kind, full_name, email, status, password_hash) VALUES "
                "(%(o)s, 'staff', %(name)s, lower(%(code)s) || '@' || %(slug)s || '.example.com', 'active', %(hash)s) "
                "RETURNING id",
                {**p, "name": name, "code": code},
            )
            cur.execute(
                "INSERT INTO staff_members (org_id, user_id, employee_code) VALUES (%(o)s, %(u)s, %(code)s)",
                {"o": o, "u": uid, "code": code},
            )
            cur.execute(
                "INSERT INTO role_assignments (org_id, user_id, role) VALUES (%(o)s, %(u)s, %(r)s)",
                {"o": o, "u": uid, "r": role},
            )
            return uid  # type: ignore[no-any-return]

        p["admin"] = staff("Seed Admin", "E-0001", "super_admin")
        p["acct"] = staff("Seed Admin Two", "E-0002", "super_admin")  # D75: one role
        cur.execute("CREATE TEMP TABLE hs (hostel_id uuid, warden uuid, k int) ON COMMIT DROP")
        for k in range(hostels):
            kind = "boys" if k < hostels / 2 else "girls"
            h = _one(
                cur,
                "INSERT INTO hostels (org_id, code, name, resident_type) "
                "VALUES (%(o)s, %(c)s, %(n)s, %(t)s) RETURNING id",
                {"o": o, "c": f"H{k + 1}", "n": f"Hostel {k + 1}", "t": kind},
            )
            staff(f"Admin {k + 1}", f"E-1{k:03d}", "super_admin")
            warden = staff(f"Office {k + 1}", f"E-2{k:03d}", "super_admin")  # decides this hostel's leave
            cur.execute("INSERT INTO hs VALUES (%(h)s, %(w)s, %(k)s)", {"h": h, "w": warden, "k": k})
            hp = {"o": o, "h": h, "F": floors, "R": rooms_per_floor}
            b = _one(
                cur, "INSERT INTO buildings (org_id, hostel_id, name) VALUES (%(o)s, %(h)s, 'Main') RETURNING id", hp
            )
            rt = _one(
                cur,
                "INSERT INTO room_types (org_id, hostel_id, name, beds_per_room) "
                "VALUES (%(o)s, %(h)s, 'Triple', 3) RETURNING id",
                hp,
            )
            cur.execute(
                "INSERT INTO floors (org_id, hostel_id, building_id, level) "
                "SELECT %(o)s, %(h)s, %(b)s, g FROM generate_series(1, %(F)s) g",
                {**hp, "b": b},
            )
            cur.execute(
                "INSERT INTO rooms (org_id, hostel_id, floor_id, room_type_id, number) "
                "SELECT f.org_id, f.hostel_id, f.id, %(rt)s, (f.level * 100 + r)::text "
                "FROM floors f, generate_series(1, %(R)s) r WHERE f.building_id = %(b)s",
                {**hp, "b": b, "rt": rt},
            )
            cur.execute(
                "INSERT INTO beds (org_id, hostel_id, room_id, label) "
                "SELECT r.org_id, r.hostel_id, r.id, l FROM rooms r, unnest(ARRAY['A','B','C']) l "
                "WHERE r.hostel_id = %(h)s ORDER BY r.number, l",
                hp,
            )

        # Students, in a scratch table first so every later step can join on i.
        cur.execute(
            "CREATE TEMP TABLE s (i int PRIMARY KEY, hostel_id uuid, gender text, allocated bool, activated bool, "
            "user_id uuid DEFAULT uuidv7(), file_id uuid DEFAULT uuidv7(), student_id uuid DEFAULT uuidv7()) "
            "ON COMMIT DROP"
        )
        cur.execute(
            "INSERT INTO s (i, hostel_id, gender, allocated, activated) "
            "SELECT i, hs.hostel_id, CASE WHEN hs.k < %(H)s / 2.0 THEN 'male' ELSE 'female' END, "
            "i %% 25 <> 0, i %% 5 <> 0 "
            "FROM generate_series(1, %(N)s) i JOIN hs ON hs.k = (i - 1) %% %(H)s",
            {"N": args.students, "H": hostels},
        )
        cur.execute(
            "INSERT INTO users (id, org_id, kind, full_name, email, status, password_hash, created_by, created_at) "
            "SELECT user_id, %(o)s, 'student', "
            " (%(first)s::text[])[1 + (i * 7919) %% cardinality(%(first)s::text[])] || ' ' || "
            " (%(last)s::text[])[1 + (i * 104729) %% cardinality(%(last)s::text[])], "
            " 's' || i || '@' || %(slug)s || '.example.com', "
            " CASE WHEN activated THEN 'active' ELSE 'pending_activation' END, "
            " CASE WHEN activated THEN %(hash)s END, %(admin)s, timestamptz '2026-07-01 10:00+05:30' FROM s",
            p,
        )
        cur.execute(
            "INSERT INTO files (id, org_id, category, storage_key, original_name, mime_type, size_bytes, sha256, "
            "uploaded_by) SELECT file_id, %(o)s, 'student_photo', %(o)s || '/student_photo/2026/07/' || file_id, "
            "'photo.jpg', 'image/jpeg', 30000, sha256(convert_to(file_id::text, 'UTF8')), %(admin)s FROM s",
            p,
        )
        cur.execute(
            "INSERT INTO students (id, org_id, user_id, student_code, hostel_id, admission_session_id, gender, "
            "date_of_birth, photo_file_id, admission_date, program, year_of_study, city, created_by) "
            "SELECT student_id, %(o)s, user_id, 'S' || lpad(i::text, 5, '0'), hostel_id, %(sess)s, gender, "
            "date '2004-01-01' + i %% 1500, file_id, date '2026-07-01', "
            "(ARRAY['B.Tech','B.Sc','B.Com','MBA'])[1 + i %% 4], 1 + i %% 4, "
            "(ARRAY['Jaipur','Pune','Lucknow','Indore','Patna','Kochi'])[1 + i %% 6], %(admin)s FROM s",
            p,
        )
        cur.execute(
            "INSERT INTO bed_allocations (org_id, hostel_id, bed_id, student_id, academic_session_id, status, "
            "start_date, checked_in_at, created_by) "
            "SELECT %(o)s, a.hostel_id, b.id, a.student_id, %(sess)s, 'active', date '2026-07-01', "
            "timestamptz '2026-07-01 10:00+05:30', %(admin)s "
            "FROM (SELECT *, row_number() OVER (PARTITION BY hostel_id ORDER BY i) rn FROM s WHERE allocated) a "
            "JOIN (SELECT id, hostel_id, row_number() OVER (PARTITION BY hostel_id ORDER BY id) rn FROM beds "
            "      WHERE org_id = %(o)s) b USING (hostel_id, rn)",
            p,
        )

        # Fees: monthly rent, charged August to November; one payment per paid month.
        p["fs"] = _one(
            cur,
            "INSERT INTO fee_structures (org_id, academic_session_id, name, fee_kind, amount, billing_cycle, "
            "created_by) VALUES (%(o)s, %(sess)s, 'Rent', 'rent', 6000, 'monthly', %(admin)s) RETURNING id",
            p,
        )
        cur.execute(
            "CREATE TEMP TABLE ch ON COMMIT DROP AS SELECT uuidv7() AS id, a.i, a.student_id, a.hostel_id, m.k, "
            "m.ps FROM s a, (SELECT k, (date '2026-08-01' + (k - 1) * interval '1 month')::date AS ps "
            "FROM generate_series(1, 4) k) m WHERE a.allocated ORDER BY a.i, m.k"
        )
        cur.execute(
            "INSERT INTO ledger_entries (id, org_id, hostel_id, student_id, txn_id, account, entry_type, amount, "
            "effective_date, description, fee_structure_id, academic_session_id, period_start, period_end, due_date) "
            "SELECT id, %(o)s, hostel_id, student_id, uuidv7(), 'dues', 'charge', 6000, ps, "
            "'Rent ' || to_char(ps, 'FMMonth YYYY'), %(fs)s, %(sess)s, ps, (ps + interval '1 month - 1 day')::date, "
            "ps + 10 FROM ch ORDER BY i, k",
            p,
        )
        cur.execute(
            "CREATE TEMP TABLE pay ON COMMIT DROP AS SELECT uuidv7() AS id, uuidv7() AS entry_id, ch.id AS charge_id, "
            "ch.i, ch.student_id, ch.hostel_id, least(ch.ps + 3 + ch.i %% 9, current_date) AS received_on, "
            "row_number() OVER (ORDER BY ch.ps, ch.i) AS seq FROM ch WHERE ch.i %% 7 <> 0 AND ch.k <= 1 + ch.i %% 4",
            p,
        )
        cur.execute(
            "INSERT INTO payments (id, org_id, hostel_id, student_id, amount, method, reference, received_on, "
            "financial_year, receipt_seq, idempotency_key, recorded_by) "
            "SELECT id, %(o)s, hostel_id, student_id, 6000, (ARRAY['cash','upi','bank_transfer'])[1 + i %% 3], "
            "CASE WHEN i %% 3 <> 0 THEN 'UTR' || lpad(seq::text, 10, '0') END, received_on, '2026-27', seq, "
            "gen_random_uuid(), %(acct)s FROM pay ORDER BY seq",
            p,
        )
        cur.execute(
            "INSERT INTO ledger_entries (id, org_id, hostel_id, student_id, txn_id, account, entry_type, amount, "
            "effective_date, description, payment_id, created_by) "
            "SELECT entry_id, %(o)s, hostel_id, student_id, uuidv7(), 'dues', 'payment', -6000, received_on, "
            "'Payment received', id, %(acct)s FROM pay ORDER BY seq",
            p,
        )
        cur.execute(
            "INSERT INTO ledger_allocations (org_id, student_id, credit_entry_id, debit_entry_id, amount, method) "
            "SELECT %(o)s, student_id, entry_id, charge_id, 6000, 'auto_oldest_first' FROM pay ORDER BY seq",
            p,
        )

        # Complaints (10% of students) and audit history (2 entries per student).
        cur.execute(
            "CREATE TEMP TABLE cats ON COMMIT DROP AS SELECT row_number() OVER () - 1 AS k, c AS name "
            "FROM unnest(ARRAY['Electrical','Plumbing','Furniture','Cleaning','Internet']) c"
        )
        cur.execute(
            "INSERT INTO complaint_categories (org_id, name) SELECT %(o)s, name FROM cats ORDER BY k",
            p,
        )
        cur.execute(
            "INSERT INTO complaints (org_id, hostel_id, student_id, category_id, title, description, priority, "
            "status, target_date, assigned_to, resolved_at, resolved_by, resolution_note, closed_at, created_at, "
            "updated_at) "
            "SELECT %(o)s, a.hostel_id, a.student_id, cc.id, 'Issue number ' || n, "
            "'Synthetic complaint for the scale test', pr, st, created::date + 7, "
            "CASE WHEN st <> 'open' THEN hs.warden END, "
            "CASE WHEN st IN ('resolved','closed') THEN created + interval '1 hour' END, "
            "CASE WHEN st IN ('resolved','closed') THEN hs.warden END, "
            "CASE WHEN st IN ('resolved','closed') THEN 'Fixed' END, "
            "CASE WHEN st = 'closed' THEN created + interval '2 hours' END, created, created "
            "FROM generate_series(1, %(C)s) n JOIN s a ON a.i = 1 + (n * 37) %% %(N)s JOIN hs USING (hostel_id) "
            "JOIN cats ON cats.k = n %% 5 JOIN complaint_categories cc ON cc.org_id = %(o)s AND cc.name = cats.name "
            "CROSS JOIN LATERAL (SELECT now() - (n %% 90) * interval '1 day' - interval '3 hours' AS created, "
            "(ARRAY['low','medium','high','urgent'])[1 + n %% 4] AS pr, "
            "(ARRAY['open','in_progress','resolved','closed','closed','resolved','open','closed'])[1 + n %% 8] AS st"
            ") z",
            {**p, "C": args.students // 10, "N": args.students},
        )
        cur.execute(
            "INSERT INTO audit_logs (org_id, hostel_id, occurred_at, actor_type, actor_user_id, actor_roles, action, "
            "entity_type, entity_id, changed_fields, after_values) "
            "SELECT %(o)s, a.hostel_id, now() - (n %% 120) * interval '1 day' - (n %% 1440) * interval '1 minute', "
            "'user', %(admin)s, '{super_admin}', 'student.updated', 'student', a.student_id, '{phone}', "
            '\'{"phone": "masked"}\'::jsonb FROM generate_series(1, %(A)s) n JOIN s a ON a.i = 1 + n %% %(N)s',
            {**p, "A": args.students * 2, "N": args.students},
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", default="hms_scale")
    ap.add_argument("--orgs", type=int, default=50)
    ap.add_argument("--students", type=int, default=5000)
    ap.add_argument("--hostels", type=int, default=4)
    args = ap.parse_args()
    t0 = time.monotonic()
    reset_database(args.database)
    fake = Faker("en_IN")
    fake.seed_instance(2026)
    first = sorted({fake.first_name() for _ in range(3000)})
    last = sorted({fake.last_name() for _ in range(3000)})
    print(f"schema ready in {time.monotonic() - t0:.0f}s; {len(first)} first and {len(last)} last names", flush=True)
    with psycopg.connect(get_settings().conninfo("postgres", args.database)) as conn:
        for n in range(1, args.orgs + 1):
            t = time.monotonic()
            seed_org(conn, n, args, first, last)
            print(f"org {n:2d}/{args.orgs} seeded in {time.monotonic() - t:.1f}s", flush=True)
        conn.autocommit = True
        conn.execute("ANALYZE")
        counts = conn.execute(
            "SELECT (SELECT count(*) FROM organizations), (SELECT count(*) FROM students), "
            "(SELECT count(*) FROM bed_allocations), (SELECT count(*) FROM ledger_entries), "
            "(SELECT count(*) FROM payments), "
            "(SELECT count(*) FROM complaints), (SELECT count(*) FROM audit_logs), "
            "pg_size_pretty(pg_database_size(current_database()))"
        ).fetchone()
    assert counts is not None
    labels = ["organizations", "students", "allocations", "ledger entries", "payments"]
    labels += ["complaints", "audit entries", "database size"]
    print("seeded: " + ", ".join(f"{k} {v}" for k, v in zip(labels, counts, strict=True)))
    print(f"total {time.monotonic() - t0:.0f}s")


if __name__ == "__main__":
    main()
