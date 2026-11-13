"""Builds a complete test organization ("world") through the real hms_app role.

Every insert goes through RLS exactly as the application will do it: open a
transaction, set app.org_id, write. The world deliberately contains:
  * at least one row in every one of the 48 tenant tables, so "you see zero
    rows" and "you see only your organization" tests are meaningful;
  * data for every composite foreign key, including spare, unused parent rows
    (an extra bed, files, a pending leave) so the cross-organization FK test
    always has a candidate that isn't blocked by some other constraint;
  * every ledger path: charge, payment with deposit, allocation, fine, targeted
    waiver, deposit application, late fee, approved+paid refund, a payment that
    is reversed after its allocation is released, and a verified UPI submission.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from .helpers import ins, tenant_tx

# Placeholder ciphertext below is NOT encrypted with any real key, so it must not claim a real key's ID:
# key rotation (Phase 10) would treat it as data under that key. data-key-status reports it as unknown.
KEY_ID = "proof-not-a-real-key"


def now() -> datetime:
    return datetime.now(UTC)


def ts(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


@dataclass
class World:
    """IDs of everything created for one organization."""

    slug: str
    org: uuid.UUID
    ids: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        try:
            return self.ids[name]
        except KeyError:
            raise AttributeError(name) from None


def next_receipt(cur: psycopg.Cursor[Any], org: uuid.UUID, fy: str = "2026-27") -> int:
    """Take the next gap-free receipt number (docs/03 §5.8): the counter row is updated in
    the same transaction as the payment, so a failed payment rolls the increment back."""
    cur.execute(
        "UPDATE org_counters SET last_value = last_value + 1 "
        "WHERE org_id = %s AND name = 'receipt' AND scope_key = %s RETURNING last_value",
        (org, fy),
    )
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


def add_file(cur: psycopg.Cursor[Any], org: uuid.UUID, category: str, mime: str, uploaded_by: uuid.UUID) -> uuid.UUID:
    fid = uuid.uuid4()
    ins(
        cur,
        "files",
        id=fid,
        org_id=org,
        category=category,
        storage_key=f"{org}/{category}/2026/11/{fid}",
        original_name=f"{category}.bin",
        mime_type=mime,
        size_bytes=1234,
        sha256=hashlib.sha256(fid.bytes).digest(),
        uploaded_by=uploaded_by,
        encryption_key_id=KEY_ID if category == "identity_document" else None,
    )
    return fid


def add_user(cur: psycopg.Cursor[Any], org: uuid.UUID, kind: str, name: str, slug: str, **extra: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "org_id": org,
        "kind": kind,
        "full_name": name,
        "email": f"{name.lower().replace(' ', '.')}@{slug}.test",
        "password_hash": "argon2id-placeholder",  # fixed dummy, not a secret  gitleaks:allow
        "status": "active",
    }
    values.update(extra)
    return ins(cur, "users", **values)  # type: ignore[no-any-return]


def add_student(cur: psycopg.Cursor[Any], w: World, code: str) -> uuid.UUID:
    """A new, unallocated student (used by tests that need a free student)."""
    user = add_user(cur, w.org, "student", f"Student {code}", w.slug, created_by=w.hm)
    photo = add_file(cur, w.org, "student_photo", "image/jpeg", w.hm)
    return ins(  # type: ignore[no-any-return]
        cur,
        "students",
        org_id=w.org,
        user_id=user,
        student_code=code,
        hostel_id=w.hostel,
        admission_session_id=w.session,
        gender="male",
        date_of_birth=date(2007, 1, 1),
        photo_file_id=photo,
        admission_date=date(2026, 6, 15),
        created_by=w.hm,
    )


def seed_org(conn: psycopg.Connection[Any], slug: str) -> World:
    """Create a complete organization. `conn` must be an hms_app connection."""
    # --- 1. Register (no tenant context yet: the 'register' policy allows pending orgs) ---
    with tenant_tx(conn, None) as cur:
        org = ins(cur, "organizations", slug=slug, name=f"Organization {slug}")
    w = World(slug=slug, org=org)
    i = w.ids

    with tenant_tx(conn, org) as cur:
        # Email verified -> active (allowed transition for hms_app, docs/03 §5.2).
        cur.execute("UPDATE organizations SET status = 'active', email_verified_at = now() WHERE id = %s", (org,))
        ins(
            cur,
            "organization_profiles",
            returning="org_id",
            org_id=org,
            contact_email=f"office@{slug}.test",
            upi_id="hostel@okbank",
            upi_payee_name="Hostel Office",
        )
        i["session"] = ins(
            cur,
            "academic_sessions",
            org_id=org,
            name="2026-27",
            starts_on=date(2026, 6, 1),
            ends_on=date(2027, 5, 31),
            is_current=True,
        )
        i["term"] = ins(
            cur,
            "academic_terms",
            org_id=org,
            academic_session_id=i["session"],
            name="Term 1",
            starts_on=date(2026, 7, 1),
            ends_on=date(2026, 11, 30),
        )
        # A second, future session with no terms: a spare parent for the composite-FK test
        # (moving a term onto the only session would hit the no-overlapping-terms rule first).
        i["session_next"] = ins(
            cur,
            "academic_sessions",
            org_id=org,
            name="2027-28",
            starts_on=date(2027, 6, 1),
            ends_on=date(2028, 5, 31),
        )
        ins(cur, "org_counters", returning="org_id", org_id=org, name="receipt", scope_key="2026-27")

        # --- Hostel structure ------------------------------------------------------------
        i["hostel"] = ins(cur, "hostels", org_id=org, code="H1", name="Hostel One", resident_type="boys")
        i["hostel2"] = ins(cur, "hostels", org_id=org, code="H2", name="Hostel Two", resident_type="girls")
        i["building"] = ins(cur, "buildings", org_id=org, hostel_id=i["hostel"], name="A")
        i["floor"] = ins(cur, "floors", org_id=org, hostel_id=i["hostel"], building_id=i["building"], level=1)
        i["room_type"] = ins(cur, "room_types", org_id=org, hostel_id=i["hostel"], name="Double", beds_per_room=2)
        i["room"] = ins(
            cur,
            "rooms",
            org_id=org,
            hostel_id=i["hostel"],
            floor_id=i["floor"],
            room_type_id=i["room_type"],
            number="101",
        )
        for label in "ABCD":  # bed D stays free: a spare for tests
            i[f"bed_{label}"] = ins(cur, "beds", org_id=org, hostel_id=i["hostel"], room_id=i["room"], label=label)

        # --- People ------------------------------------------------------------------------
        i["sa"] = add_user(
            cur,
            org,
            "staff",
            "Super Admin",
            slug,
        )
        for key, name in [
            ("hm", "Hostel Manager"),
            ("warden", "Warden One"),
            ("acct", "Accountant One"),
            ("acct2", "Accountant Two"),
            ("guard", "Guard One"),
        ]:
            i[key] = add_user(cur, org, "staff", name, slug, created_by=i["sa"])
        i["stu1"] = add_user(cur, org, "student", "Student One", slug, created_by=i["hm"])
        i["stu2"] = add_user(
            cur,
            org,
            "student",
            "Student Two",
            slug,
            created_by=i["hm"],
            status="pending_activation",
            password_hash=None,
        )

        # D75/D76: one role, one Administrator per organization ("sa"). The other staff accounts keep
        # their old keys because the tests below use them as the people who wrote rows; the database
        # checks here don't depend on roles.
        i["role_sa"] = ins(cur, "role_assignments", org_id=org, user_id=i["sa"], role="super_admin", granted_by=None)
        # A revoked Administrator grant (history): it carries granted_by and revoked_by.
        i["role_revoked"] = ins(
            cur,
            "role_assignments",
            org_id=org,
            user_id=i["warden"],
            role="super_admin",
            granted_by=i["sa"],
            revoked_by=i["sa"],
            revoked_at=now(),
        )
        for key, code in [
            ("sa", "E-SA"),
            ("hm", "E-HM"),
            ("warden", "E-WD"),
            ("acct", "E-AC"),
            ("acct2", "E-AC2"),
            ("guard", "E-SG"),
        ]:
            ins(cur, "staff_members", org_id=org, user_id=i[key], employee_code=code)

        # --- Files (incl. one spare per category for the FK test) --------------------------
        for key, cat, mime in [
            ("photo1", "student_photo", "image/jpeg"),
            ("photo2", "student_photo", "image/jpeg"),
            ("id_file", "identity_document", "application/pdf"),
            ("adm_file", "admission_document", "application/pdf"),
            ("proof1", "payment_proof", "image/png"),
            ("proof2", "payment_proof", "image/png"),
            ("c_file", "complaint_attachment", "image/jpeg"),
            ("spare_id", "identity_document", "application/pdf"),
            ("spare_adm", "admission_document", "application/pdf"),
            ("spare_proof", "payment_proof", "image/png"),
            ("spare_c", "complaint_attachment", "image/jpeg"),
        ]:
            i[key] = add_file(cur, org, cat, mime, i["hm"])

        # --- Students ----------------------------------------------------------------------
        for key, user, code, photo in [("s1", "stu1", "STU-0001", "photo1"), ("s2", "stu2", "STU-0002", "photo2")]:
            i[key] = ins(
                cur,
                "students",
                org_id=org,
                user_id=i[user],
                student_code=code,
                hostel_id=i["hostel"],
                admission_session_id=i["session"],
                gender="male",
                date_of_birth=date(2007, 5, 1),
                photo_file_id=i[photo],
                admission_date=date(2026, 6, 15),
                created_by=i["hm"],
            )
        ins(
            cur,
            "student_guardians",
            org_id=org,
            student_id=i["s1"],
            full_name="Parent One",
            relationship="father",
            phone="+91 98765 43210",
            is_primary=True,
        )
        i["doc_id"] = ins(
            cur,
            "student_documents",
            org_id=org,
            student_id=i["s1"],
            file_id=i["id_file"],
            file_category="identity_document",
            file_mime_type="application/pdf",
            document_type="identity",
            id_type="aadhaar",
            id_number_masked="XXXXXXXX1234",
            masked_attested_by=i["hm"],
            masked_attested_at=now(),
            uploaded_by=i["hm"],
            verified_by=i["sa"],
            verified_at=now(),
        )
        i["doc_adm"] = ins(
            cur,
            "student_documents",
            org_id=org,
            student_id=i["s1"],
            file_id=i["adm_file"],
            file_category="admission_document",
            file_mime_type="application/pdf",
            document_type="admission",
            uploaded_by=i["hm"],
        )

        # --- Allocations: s1 active on bed A; s2 transferred from bed B to bed C --------------
        i["alloc1"] = ins(
            cur,
            "bed_allocations",
            org_id=org,
            hostel_id=i["hostel"],
            bed_id=i["bed_A"],
            student_id=i["s1"],
            academic_session_id=i["session"],
            status="active",
            start_date=date(2026, 7, 1),
            checked_in_at=now(),
            created_by=i["hm"],
        )
        i["alloc2a"] = ins(
            cur,
            "bed_allocations",
            org_id=org,
            hostel_id=i["hostel"],
            bed_id=i["bed_B"],
            student_id=i["s2"],
            academic_session_id=i["session"],
            status="ended",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 9, 1),
            checked_in_at=now(),
            checked_out_at=now(),
            end_reason="transfer",
            created_by=i["hm"],
        )
        i["alloc2b"] = ins(
            cur,
            "bed_allocations",
            org_id=org,
            hostel_id=i["hostel"],
            bed_id=i["bed_C"],
            student_id=i["s2"],
            academic_session_id=i["session"],
            status="active",
            start_date=date(2026, 9, 1),
            checked_in_at=now(),
            previous_allocation_id=i["alloc2a"],
            created_by=i["hm"],
        )

        i["fee_rent"] = ins(
            cur,
            "fee_structures",
            org_id=org,
            academic_session_id=i["session"],
            hostel_id=i["hostel"],
            room_type_id=i["room_type"],
            name="Term rent",
            fee_kind="rent",
            amount=Decimal("30000.00"),
            billing_cycle="term",
            created_by=i["acct"],
        )
        i["fee_deposit"] = ins(
            cur,
            "fee_structures",
            org_id=org,
            academic_session_id=i["session"],
            name="Deposit",
            fee_kind="deposit",
            amount=Decimal("5000.00"),
            billing_cycle="one_time",
            created_by=i["acct"],
        )

        # --- Spare, unused parent rows for the composite-FK test -----------------------------
        # Re-pointing an Org-A row at an Org-B parent must fail on the FOREIGN KEY. If every
        # Org-B parent is already in use, another constraint (unique room number, one active
        # assignment, no overlapping stays, one refund entry per request) fires first and the
        # FK is never reached. These spares are parents that nothing else references.
        i["spare_building"] = ins(cur, "buildings", org_id=org, hostel_id=i["hostel"], name="B")
        i["spare_floor"] = ins(cur, "floors", org_id=org, hostel_id=i["hostel"], building_id=i["building"], level=2)
        i["spare_room"] = ins(
            cur,
            "rooms",
            org_id=org,
            hostel_id=i["hostel"],
            floor_id=i["floor"],
            room_type_id=i["room_type"],
            number="102",
        )
        i["s3"] = add_student(cur, w, "STU-0003")
        i["spare_refund_req"] = ins(
            cur,
            "refund_requests",
            org_id=org,
            hostel_id=i["hostel"],
            student_id=i["s1"],
            account="deposit",
            amount=Decimal("10.00"),
            reason="Spare open request",
            requested_by=i["acct"],
        )

    _seed_ledger(conn, w)
    _seed_operations(conn, w)
    return w


def ledger(cur: psycopg.Cursor[Any], w: World, **cols: Any) -> uuid.UUID:
    """Insert one ledger entry for student s1 unless told otherwise."""
    values: dict[str, Any] = {
        "org_id": w.org,
        "hostel_id": w.hostel,
        "student_id": w.s1,
        "txn_id": uuid.uuid4(),
        "account": "dues",
        "effective_date": date(2026, 7, 1),
        "description": "test entry",
        "created_by": w.acct,
    }
    values.update(cols)
    return ins(cur, "ledger_entries", **values)  # type: ignore[no-any-return]


def payment(cur: psycopg.Cursor[Any], w: World, amount: str, **cols: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "org_id": w.org,
        "hostel_id": w.hostel,
        "student_id": w.s1,
        "amount": Decimal(amount),
        "method": "cash",
        "received_on": date(2026, 7, 5),
        "financial_year": "2026-27",
        "receipt_seq": next_receipt(cur, w.org),
        "idempotency_key": uuid.uuid4(),
        "recorded_by": w.acct,
    }
    values.update(cols)
    return ins(cur, "payments", **values)  # type: ignore[no-any-return]


def allocate(
    cur: psycopg.Cursor[Any],
    w: World,
    credit: uuid.UUID,
    debit: uuid.UUID,
    amount: str,
    manual: bool = False,
    student: uuid.UUID | None = None,
) -> uuid.UUID:
    return ins(
        cur,
        "ledger_allocations",
        org_id=w.org,
        student_id=student or w.s1,  # type: ignore[no-any-return]
        credit_entry_id=credit,
        debit_entry_id=debit,
        amount=Decimal(amount),
        method="manual" if manual else "auto_oldest_first",
        allocated_by=w.acct if manual else None,
    )


def _seed_ledger(conn: psycopg.Connection[Any], w: World) -> None:
    i = w.ids
    with tenant_tx(conn, w.org) as cur:  # term rent + payment covering rent and the deposit
        i["charge"] = ledger(
            cur,
            w,
            entry_type="charge",
            amount=Decimal("30000.00"),
            description="Term 1 rent",
            fee_structure_id=w.fee_rent,
            academic_session_id=w.session,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 11, 30),
            due_date=date(2026, 7, 10),
        )
        i["pay1"] = payment(cur, w, "35000.00")
        i["e_pay1"] = ledger(cur, w, entry_type="payment", amount=Decimal("-30000.00"), payment_id=w.pay1)
        i["e_dep1"] = ledger(
            cur, w, account="deposit", entry_type="deposit_received", amount=Decimal("5000.00"), payment_id=w.pay1
        )
        i["alloc_pay1"] = allocate(cur, w, w.e_pay1, w.charge, "30000.00")

    with tenant_tx(conn, w.org) as cur:  # fine + waiver targeted at it (Q64)
        i["fine"] = ledger(
            cur,
            w,
            entry_type="fine",
            amount=Decimal("800.00"),
            due_date=date(2026, 8, 10),
            reason_code="damage",
            reason_note="Broken chair",
        )
        i["waiver"] = ledger(
            cur, w, entry_type="waiver", amount=Decimal("-300.00"), reason_code="waiver", reason_note="First offence"
        )
        i["alloc_waiver"] = allocate(cur, w, w.waiver, w.fine, "300.00", manual=True)

    with tenant_tx(conn, w.org) as cur:  # deposit applied to the fine (balanced pair)
        txn = uuid.uuid4()
        i["dep_applied_dues"] = ledger(cur, w, txn_id=txn, entry_type="deposit_applied", amount=Decimal("-200.00"))
        ledger(cur, w, txn_id=txn, account="deposit", entry_type="deposit_applied", amount=Decimal("-200.00"))
        allocate(cur, w, w.dep_applied_dues, w.fine, "200.00")

    with tenant_tx(conn, w.org) as cur:  # system-generated late fee (created_by NULL allowed)
        i["late_fee"] = ledger(
            cur,
            w,
            entry_type="late_fee",
            amount=Decimal("50.00"),
            source_entry_id=w.fine,
            late_fee_date=date(2026, 8, 20),
            due_date=date(2026, 8, 20),
            created_by=None,
        )

    with tenant_tx(conn, w.org) as cur:  # refund request by acct ...
        i["refund_req"] = ins(
            cur,
            "refund_requests",
            org_id=w.org,
            hostel_id=w.hostel,
            student_id=w.s1,
            account="deposit",
            amount=Decimal("1000.00"),
            reason="Partial deposit refund",
            requested_by=w.acct,
        )
    with tenant_tx(conn, w.org) as cur:  # ... approved by acct2 (a different person), then paid
        cur.execute(
            "UPDATE refund_requests SET status = 'approved', decided_by = %s, decided_at = now() WHERE id = %s",
            (w.acct2, w.refund_req),
        )
        i["e_refund"] = ledger(
            cur,
            w,
            account="deposit",
            entry_type="deposit_refunded",
            amount=Decimal("-1000.00"),
            refund_request_id=w.refund_req,
        )
        cur.execute(
            "UPDATE refund_requests SET status = 'paid', paid_at = now(), paid_method = 'cash' WHERE id = %s",
            (w.refund_req,),
        )

    with tenant_tx(conn, w.org) as cur:  # payment 2, allocated to the late fee ...
        i["pay2"] = payment(cur, w, "500.00", received_on=date(2026, 8, 25))
        i["e_pay2"] = ledger(cur, w, entry_type="payment", amount=Decimal("-500.00"), payment_id=w.pay2)
        i["alloc_rel"] = allocate(cur, w, w.e_pay2, w.late_fee, "50.00")
    with tenant_tx(conn, w.org) as cur:  # ... then reversed: release allocation, reverse entry, record reversal
        cur.execute(
            "UPDATE ledger_allocations SET released_at = now(), released_by = %s, "
            "release_reason = 'credit_reversed' WHERE id = %s",
            (w.acct, w.alloc_rel),
        )
        i["rev_pay2"] = ledger(
            cur,
            w,
            entry_type="reversal",
            amount=Decimal("500.00"),
            reverses_entry_id=w.e_pay2,
            reason_note="Entered twice",
        )
        ins(
            cur,
            "payment_reversals",
            returning="payment_id",
            payment_id=w.pay2,
            org_id=w.org,
            reason="Entered twice",
            reversed_by=w.acct,
        )

    with tenant_tx(conn, w.org) as cur:  # UPI submissions: one pending, one verified -> payment 3
        i["sub_pending"] = ins(
            cur,
            "payment_submissions",
            org_id=w.org,
            hostel_id=w.hostel,
            student_id=w.s1,
            amount=Decimal("1000.00"),
            utr="UTR123456789",
            paid_on=date(2026, 9, 1),
            proof_file_id=w.proof1,
            submitted_by=w.stu1,
        )
        i["sub_verified"] = ins(
            cur,
            "payment_submissions",
            org_id=w.org,
            hostel_id=w.hostel,
            student_id=w.s1,
            amount=Decimal("300.00"),
            utr="UTR987654321",
            paid_on=date(2026, 9, 2),
            proof_file_id=w.proof2,
            submitted_by=w.stu1,
            status="verified",
            reviewed_by=w.acct,
            reviewed_at=now(),
        )
        i["pay3"] = payment(
            cur,
            w,
            "300.00",
            method="upi",
            reference="UTR987654321",
            submission_id=w.sub_verified,
            received_on=date(2026, 9, 2),
        )
        i["e_pay3"] = ledger(cur, w, entry_type="payment", amount=Decimal("-300.00"), payment_id=w.pay3)
        allocate(cur, w, w.e_pay3, w.fine, "300.00")


def _seed_operations(conn: psycopg.Connection[Any], w: World) -> None:
    i = w.ids
    with tenant_tx(conn, w.org) as cur:
        # Leave (D75: no gate passes).
        i["leave1"] = ins(
            cur,
            "leave_requests",
            org_id=w.org,
            hostel_id=w.hostel,
            student_id=w.s1,
            leave_type="home",
            reason="Festival",
            starts_at=ts(2026, 10, 20, 10),
            ends_at=ts(2026, 10, 25, 18),
            status="approved",
            requested_by=w.stu1,
            decided_by=w.warden,
            decided_at=now(),
        )
        i["leave2"] = ins(
            cur,
            "leave_requests",
            org_id=w.org,
            hostel_id=w.hostel,
            student_id=w.s2,
            leave_type="local_outing",
            reason="Market",
            starts_at=ts(2026, 10, 21, 10),
            ends_at=ts(2026, 10, 21, 16),
            requested_by=w.stu2,
        )
        # Complaints.
        i["category"] = ins(cur, "complaint_categories", org_id=w.org, name="Electrical")
        i["complaint1"] = ins(
            cur,
            "complaints",
            org_id=w.org,
            hostel_id=w.hostel,
            student_id=w.s1,
            room_id=w.room,
            category_id=w.category,
            title="Fan not working",
            description="The fan stopped.",
            priority="high",
            status="in_progress",
            target_date=date(2026, 11, 10),
            assigned_to=w.warden,
        )
        i["complaint2"] = ins(
            cur,
            "complaints",
            org_id=w.org,
            hostel_id=w.hostel,
            student_id=w.s1,
            category_id=w.category,
            title="Light flickers",
            description="Room light flickers.",
            priority="low",
            status="resolved",
            target_date=date(2026, 11, 20),
            assigned_to=w.warden,
            resolved_at=now(),
            resolved_by=w.warden,
            resolution_note="Replaced the tube",
        )
        ins(
            cur,
            "complaint_updates",
            org_id=w.org,
            complaint_id=w.complaint1,
            author_id=w.stu1,
            to_status="open",
            comment="Fan broken since morning",
        )
        ins(cur, "complaint_attachments", org_id=w.org, complaint_id=w.complaint1, file_id=w.c_file)

        # Mess.
        ins(
            cur,
            "mess_menus",
            org_id=w.org,
            hostel_id=w.hostel,
            menu_date=date(2026, 11, 7),
            meal="dinner",
            items="Rice, dal, sabzi",
            updated_by=w.warden,
        )

        # Notifications, preferences, announcement.
        ins(
            cur,
            "notifications",
            org_id=w.org,
            user_id=w.stu1,
            category="fees",  # D84: no 'allocation' category, links only to pages that exist
            title="New charge",
            body="Hostel rent: Rs 7200",
            link="/portal/fees",
        )
        ins(
            cur,
            "notification_preferences",
            returning="user_id",
            org_id=w.org,
            user_id=w.stu1,
            category="reminders",
            email_enabled=False,
        )
        ins(
            cur,
            "announcements",
            org_id=w.org,
            hostel_id=w.hostel,
            title="Water cut",
            body="No water from 10 to 12.",
            audience="students",
            created_by=w.warden,
        )

        # Student registration requests (D74): one waiting for its email (with its link), one approved.
        i["reg_unverified"] = ins(
            cur,
            "student_registration_requests",
            org_id=w.org,
            hostel_id=w.hostel,
            full_name="Asha Rao",
            email=f"asha.{uuid.uuid4().hex[:6]}@example.com",
            gender="female",
            date_of_birth=date(2007, 1, 1),
            guardian_name="Ravi Rao",
            guardian_relationship="father",
            guardian_phone="+91 98765 00000",
            password_hash="x",
            photo=b"x",
            photo_mime="image/jpeg",
        )
        ins(
            cur,
            "auth_tokens",
            org_id=w.org,
            registration_request_id=w.reg_unverified,
            purpose="student_request",
            token_hash=secrets.token_hex(32),
            delivery="email",
            expires_at=now() + timedelta(hours=24),
        )
        i["reg_approved"] = ins(
            cur,
            "student_registration_requests",
            org_id=w.org,
            hostel_id=w.hostel,
            status="approved",
            full_name="Kiran Rao",
            email=f"kiran.{uuid.uuid4().hex[:6]}@example.com",
            gender="male",
            date_of_birth=date(2007, 2, 2),
            guardian_name="Ravi Rao",
            guardian_relationship="father",
            guardian_phone="+91 98765 00001",
            photo_mime="image/jpeg",
            email_verified_at=now(),
            decided_by=w.sa,
            decided_at=now(),
            student_id=w.s2,
        )

        # Authentication data.
        ins(
            cur,
            "sessions",
            org_id=w.org,
            user_id=w.sa,
            family_id=uuid.uuid4(),
            refresh_token_hash=secrets.token_hex(32),
            expires_at=now() + timedelta(days=7),
            absolute_expires_at=now() + timedelta(days=30),
        )
        ins(
            cur,
            "auth_tokens",
            org_id=w.org,
            user_id=w.stu2,
            purpose="activation",
            token_hash=secrets.token_hex(32),
            delivery="copied_link",
            issued_by=w.hm,
            expires_at=now() + timedelta(days=2),
        )
        ins(
            cur,
            "email_otps",
            org_id=w.org,
            user_id=w.warden,
            code_hash=secrets.token_hex(16),
            expires_at=now() + timedelta(minutes=10),
        )
        ins(
            cur,
            "login_attempts",
            org_id=w.org,
            user_id=w.sa,
            identifier_hash=secrets.token_hex(16),
            ip_address="127.0.0.1",
            succeeded=True,
        )

        # Job and audit row.
        ins(cur, "jobs", org_id=w.org, kind="email.send", payload={"user_id": str(w.stu1)})
        ins(
            cur,
            "audit_logs",
            org_id=w.org,
            actor_type="user",
            actor_user_id=w.sa,
            actor_roles=["super_admin"],
            action="hostel.created",
            entity_type="hostel",
            entity_id=w.hostel,
            changed_fields=["name"],
            before_values=None,
            after_values={"name": "Hostel One"},
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
