"""The financial ledger's guarantees (docs/02 B3, docs/03 §5.8 and §7, docs/05 FIN-*).

Balances after the world seed, for student s1:
  dues    = 30000 - 30000 + 800 - 300 - 200 + 50 - 500 + 500 - 300 = 50   (owes the late fee)
  deposit = 5000 - 200 - 1000                                           = 3800
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from tests.conftest import Connect

from .catalog import APPEND_ONLY_TABLES
from .helpers import (
    CHECK_VIOLATION,
    FOREIGN_KEY_VIOLATION,
    INSUFFICIENT_PRIVILEGE,
    RESTRICT_VIOLATION,
    UNIQUE_VIOLATION,
    expect_db_error,
    ins,
    race,
    set_tenant,
    tenant_tx,
)
from .world import World, add_student, allocate, ledger, next_receipt, payment

pytestmark = pytest.mark.dbproof


def approved_refund(conn: psycopg.Connection[Any], w: World, account: str, amount: str) -> uuid.UUID:
    """A refund request created by acct and approved by acct2 (two different people)."""
    with tenant_tx(conn, w.org) as cur:
        rid = ins(
            cur,
            "refund_requests",
            org_id=w.org,
            hostel_id=w.hostel,
            student_id=w.s1,
            account=account,
            amount=Decimal(amount),
            reason="Test refund",
            requested_by=w.acct,
        )
        cur.execute(
            "UPDATE refund_requests SET status = 'approved', decided_by = %s, decided_at = now() WHERE id = %s",
            (w.acct2, rid),
        )
    return rid  # type: ignore[no-any-return]


# --- Sign and account rules (a CHECK constraint) -------------------------------------------------


@pytest.mark.parametrize(
    "case",
    [
        {"entry_type": "charge", "amount": "-10.00", "due_date": date(2026, 9, 1)},
        {
            "entry_type": "fine",
            "amount": "-10.00",
            "due_date": date(2026, 9, 1),
            "reason_code": "damage",
            "reason_note": "x",
        },
        {"entry_type": "waiver", "amount": "10.00", "reason_code": "waiver", "reason_note": "x"},
        {"entry_type": "payment", "amount": "10.00", "use_payment": True},
        {"entry_type": "deposit_received", "account": "dues", "amount": "10.00", "use_payment": True},
        {"entry_type": "deposit_applied", "amount": "10.00"},
    ],
    ids=lambda c: f"{c['entry_type']}{c['amount']}",
)
def test_ledger_sign_rules(app_conn: psycopg.Connection[Any], fresh: World, case: dict[str, Any]) -> None:
    case = dict(case)
    if case.pop("use_payment", False):
        case["payment_id"] = fresh.pay1
    if case["entry_type"] == "charge":
        case["academic_session_id"] = fresh.session
    case["amount"] = Decimal(case["amount"])
    with expect_db_error(CHECK_VIOLATION, "ledger_sign_rules"), tenant_tx(app_conn, fresh.org) as cur:
        ledger(cur, fresh, **case)


# --- History can't be rewritten ----------------------------------------------------------------


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
@pytest.mark.parametrize(
    "statement", ["UPDATE {t} SET org_id = org_id WHERE org_id = %s", "DELETE FROM {t} WHERE org_id = %s"]
)
def test_financial_and_history_tables_reject_update_and_delete_for_app(
    app_conn: psycopg.Connection[Any], world_a: World, table: str, statement: str
) -> None:
    with expect_db_error(INSUFFICIENT_PRIVILEGE), tenant_tx(app_conn, world_a.org) as cur:
        cur.execute(statement.format(t=table), (world_a.org,))  # no privilege at all


OWNER_STATEMENTS = ["UPDATE {t} SET org_id = org_id WHERE org_id = %s", "DELETE FROM {t} WHERE org_id = %s"]


@pytest.mark.parametrize("table", [t for t in APPEND_ONLY_TABLES if t != "audit_logs"])
@pytest.mark.parametrize("statement", OWNER_STATEMENTS)
def test_append_only_trigger_stops_even_the_owner(connect: Connect, world_a: World, table: str, statement: str) -> None:
    owner = connect("hms_owner")
    with expect_db_error(RESTRICT_VIOLATION), tenant_tx(owner, world_a.org) as cur:
        cur.execute(statement.format(t=table), (world_a.org,))


@pytest.mark.parametrize("statement", OWNER_STATEMENTS)
def test_audit_logs_owner_change_blocked_by_rls(
    connect: Connect, world_a: World, statement: str, su: psycopg.Connection[Any]
) -> None:
    """audit_logs has RLS policies for SELECT and INSERT only. With FORCE RLS and no UPDATE/DELETE
    policy, even the owner's UPDATE/DELETE matches zero rows: the change is refused before the
    append-only trigger is ever reached. (The trigger itself is proven by the superuser test below.)"""
    before = su.execute(
        "SELECT count(*), max(occurred_at) FROM audit_logs WHERE org_id = %s", (world_a.org,)
    ).fetchone()
    owner = connect("hms_owner")
    with tenant_tx(owner, world_a.org) as cur:
        cur.execute(statement.format(t="audit_logs"), (world_a.org,))
        assert cur.rowcount == 0
    after = su.execute("SELECT count(*), max(occurred_at) FROM audit_logs WHERE org_id = %s", (world_a.org,)).fetchone()
    assert before == after and before is not None and before[0] > 0


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
@pytest.mark.parametrize("statement", OWNER_STATEMENTS)
def test_append_only_trigger_stops_even_the_superuser(
    su: psycopg.Connection[Any], world_a: World, table: str, statement: str
) -> None:
    """The superuser bypasses RLS and every privilege check: only the trigger is left standing."""
    with expect_db_error(RESTRICT_VIOLATION), su.transaction(force_rollback=True):
        su.execute(statement.format(t=table), (world_a.org,))


@pytest.mark.parametrize("table", ["audit_logs", "complaint_updates", "payment_reversals"])
def test_append_only_trigger_stops_owner_truncate(connect: Connect, table: str) -> None:
    owner = connect("hms_owner")
    with expect_db_error(RESTRICT_VIOLATION), owner.transaction(force_rollback=True):
        owner.execute(f"TRUNCATE {table}")


# --- Reversals --------------------------------------------------------------------------------


def test_reversal_must_be_exact_opposite(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION, "ledger_guard_reversal"), tenant_tx(app_conn, fresh.org) as cur:
        ledger(
            cur,
            fresh,
            entry_type="reversal",
            amount=Decimal("-40.00"),
            reverses_entry_id=fresh.late_fee,
            reason_note="wrong amount",
        )


def test_entry_reversed_only_once(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        ledger(
            cur,
            fresh,
            entry_type="reversal",
            amount=Decimal("-50.00"),
            reverses_entry_id=fresh.late_fee,
            reason_note="Charged in error",
        )
    with expect_db_error(UNIQUE_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        ledger(
            cur,
            fresh,
            entry_type="reversal",
            amount=Decimal("-50.00"),
            reverses_entry_id=fresh.late_fee,
            reason_note="Again",
        )


def test_cannot_reverse_a_reversal(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION, "ledger_guard_reversal"), tenant_tx(app_conn, fresh.org) as cur:
        ledger(
            cur,
            fresh,
            entry_type="reversal",
            amount=Decimal("-500.00"),
            reverses_entry_id=fresh.rev_pay2,
            reason_note="Undo the undo",
        )


def test_reversal_with_active_allocations_rejected_at_commit(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    """The fine still has three active allocations; reversing it without releasing them fails at COMMIT."""
    with (
        expect_db_error(CHECK_VIOLATION, "ledger_reversal_releases_allocations"),
        tenant_tx(app_conn, fresh.org) as cur,
    ):
        ledger(
            cur,
            fresh,
            entry_type="reversal",
            amount=Decimal("-800.00"),
            reverses_entry_id=fresh.fine,
            reason_note="Fine cancelled",
        )


# --- Payments add up --------------------------------------------------------------------------


def test_payment_entries_must_sum_to_amount(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION, "ledger_payment_balanced"), tenant_tx(app_conn, fresh.org) as cur:
        pid = payment(cur, fresh, "100.00")
        ledger(cur, fresh, entry_type="payment", amount=Decimal("-50.00"), payment_id=pid)


def test_payment_without_entries_rejected(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION, "ledger_payment_balanced"), tenant_tx(app_conn, fresh.org) as cur:
        payment(cur, fresh, "100.00")


def test_payment_reversal_requires_all_entries_reversed(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION, "payment_reversal_complete"), tenant_tx(app_conn, fresh.org) as cur:
        ins(
            cur,
            "payment_reversals",
            returning="payment_id",
            payment_id=fresh.pay1,
            org_id=fresh.org,
            reason="Not really reversed",
            reversed_by=fresh.acct,
        )


def test_payment_entry_cannot_be_reversed_alone(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION, "payment_reversal_complete"), tenant_tx(app_conn, fresh.org) as cur:
        cur.execute(
            "UPDATE ledger_allocations SET released_at = now(), released_by = %s, "
            "release_reason = 'credit_reversed' WHERE credit_entry_id = %s AND released_at IS NULL",
            (fresh.acct, fresh.e_pay3),
        )
        ledger(
            cur,
            fresh,
            entry_type="reversal",
            amount=Decimal("300.00"),
            reverses_entry_id=fresh.e_pay3,
            reason_note="No payment_reversals row",
        )


def test_deposit_application_must_balance(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION, "ledger_deposit_applied_balanced"), tenant_tx(app_conn, fresh.org) as cur:
        ledger(cur, fresh, entry_type="deposit_applied", amount=Decimal("-100.00"))  # dues side only


def test_payment_idempotency_key_unique(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        cur.execute("SELECT idempotency_key FROM payments WHERE id = %s", (fresh.pay1,))
        key = cur.fetchone()[0]  # type: ignore[index]
    with expect_db_error(UNIQUE_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        payment(cur, fresh, "10.00", idempotency_key=key)


def test_same_upi_reference_twice_rejected(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(UNIQUE_VIOLATION, "payments_reference_uq"), tenant_tx(app_conn, fresh.org) as cur:
        payment(cur, fresh, "300.00", method="upi", reference="UTR987654321")


def test_duplicate_pending_utr_rejected(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(UNIQUE_VIOLATION, "payment_submissions_utr_uq"), tenant_tx(app_conn, fresh.org) as cur:
        ins(
            cur,
            "payment_submissions",
            org_id=fresh.org,
            hostel_id=fresh.hostel,
            student_id=fresh.s1,
            amount=Decimal("1000.00"),
            utr="UTR123456789",
            paid_on=date(2026, 9, 3),
            submitted_by=fresh.stu1,
        )


# --- Receipts ------------------------------------------------------------------------------------


def test_receipt_number_matches_sequence(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        cur.execute("SELECT receipt_number, receipt_seq FROM payments WHERE id = %s", (fresh.pay1,))
        number, seq = cur.fetchone()  # type: ignore[misc]
    assert number == f"RCPT/2026-27/{seq:06d}"


def test_receipt_numbers_gap_free_when_payment_fails(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        cur.execute("SELECT last_value FROM org_counters WHERE org_id = %s AND name = 'receipt'", (fresh.org,))
        before = cur.fetchone()[0]  # type: ignore[index]
    with expect_db_error(CHECK_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        payment(cur, fresh, "100.00")  # takes number before+1, then fails at COMMIT (no entries)
    with app_conn.transaction(force_rollback=True):
        set_tenant(app_conn, fresh.org)
        with app_conn.cursor() as cur:
            assert next_receipt(cur, fresh.org) == before + 1  # the number was NOT lost


def test_receipt_seq_unique(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        cur.execute("SELECT receipt_seq FROM payments WHERE id = %s", (fresh.pay1,))
        seq = cur.fetchone()[0]  # type: ignore[index]
    with expect_db_error(UNIQUE_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        ins(
            cur,
            "payments",
            org_id=fresh.org,
            hostel_id=fresh.hostel,
            student_id=fresh.s1,
            amount=Decimal("1.00"),
            method="cash",
            received_on=date(2026, 9, 9),
            financial_year="2026-27",
            receipt_seq=seq,
            idempotency_key=uuid.uuid4(),
            recorded_by=fresh.acct,
        )


# --- Refunds -------------------------------------------------------------------------------------


def test_refund_self_approval_rejected_even_for_super_admin(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        rid = ins(
            cur,
            "refund_requests",
            org_id=fresh.org,
            hostel_id=fresh.hostel,
            student_id=fresh.s1,
            account="deposit",
            amount=Decimal("100.00"),
            reason="Self approval test",
            requested_by=fresh.sa,
        )
    with expect_db_error(CHECK_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        cur.execute(
            "UPDATE refund_requests SET status = 'approved', decided_by = %s, decided_at = now() WHERE id = %s",
            (fresh.sa, rid),
        )


def test_refund_requires_approved_request(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        rid = ins(
            cur,
            "refund_requests",
            org_id=fresh.org,
            hostel_id=fresh.hostel,
            student_id=fresh.s1,
            account="deposit",
            amount=Decimal("100.00"),
            reason="Not yet approved",
            requested_by=fresh.acct,
        )
    with expect_db_error(CHECK_VIOLATION, "ledger_guard_refund_not_approved"), tenant_tx(app_conn, fresh.org) as cur:
        ledger(
            cur,
            fresh,
            account="deposit",
            entry_type="deposit_refunded",
            amount=Decimal("-100.00"),
            refund_request_id=rid,
        )


def test_refund_paid_once(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    rid = approved_refund(app_conn, fresh, "deposit", "100.00")
    with tenant_tx(app_conn, fresh.org) as cur:
        ledger(
            cur,
            fresh,
            account="deposit",
            entry_type="deposit_refunded",
            amount=Decimal("-100.00"),
            refund_request_id=rid,
        )
    with expect_db_error(UNIQUE_VIOLATION, "ledger_one_refund_per_request_uq"), tenant_tx(app_conn, fresh.org) as cur:
        ledger(
            cur,
            fresh,
            account="deposit",
            entry_type="deposit_refunded",
            amount=Decimal("-100.00"),
            refund_request_id=rid,
        )


def test_deposit_cannot_go_negative(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    rid = approved_refund(app_conn, fresh, "deposit", "5000.00")  # only 3800 is held
    with expect_db_error(CHECK_VIOLATION, "ledger_guard_deposit_floor"), tenant_tx(app_conn, fresh.org) as cur:
        ledger(
            cur,
            fresh,
            account="deposit",
            entry_type="deposit_refunded",
            amount=Decimal("-5000.00"),
            refund_request_id=rid,
        )


def test_dues_refund_cannot_exceed_credit(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    rid = approved_refund(app_conn, fresh, "dues", "10.00")  # s1 owes 50: there is no credit
    with expect_db_error(CHECK_VIOLATION, "ledger_guard_refund_exceeds_credit"), tenant_tx(app_conn, fresh.org) as cur:
        ledger(cur, fresh, entry_type="refund", amount=Decimal("10.00"), refund_request_id=rid)


@pytest.mark.race
def test_concurrent_deposit_refunds_only_one_succeeds(connect: Connect, fresh: World) -> None:
    """Two approved refunds, each for the whole 3800 held, posted at the same instant."""
    app = connect("hms_app")
    r1 = approved_refund(app, fresh, "deposit", "3800.00")
    r2 = approved_refund(app, fresh, "deposit", "3800.00")
    result = race(
        lambda: connect("hms_app"),
        fresh.org,
        lambda cur: ledger(
            cur,
            fresh,
            account="deposit",
            entry_type="deposit_refunded",
            amount=Decimal("-3800.00"),
            refund_request_id=r1,
        ),
        lambda cur: ledger(
            cur,
            fresh,
            account="deposit",
            entry_type="deposit_refunded",
            amount=Decimal("-3800.00"),
            refund_request_id=r2,
        ),
    )
    assert len(result.ok) == 1, result
    assert [e.diag.constraint_name for e in result.errors] == ["ledger_guard_deposit_floor"]


# --- Allocations (Q60, Q64) ---------------------------------------------------------------------


def test_allocation_cannot_exceed_credit(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION, "ledger_allocations_exceeds_balance"), tenant_tx(app_conn, fresh.org) as cur:
        allocate(cur, fresh, fresh.e_pay1, fresh.late_fee, "1.00")  # e_pay1 is fully used on the rent


def test_allocation_cannot_exceed_debit(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION, "ledger_allocations_exceeds_balance"), tenant_tx(app_conn, fresh.org) as cur:
        pid = payment(cur, fresh, "1000.00")
        credit = ledger(cur, fresh, entry_type="payment", amount=Decimal("-1000.00"), payment_id=pid)
        allocate(cur, fresh, credit, fresh.late_fee, "60.00")  # late fee is only 50


def test_allocation_same_student_only(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        other = add_student(cur, fresh, "STU-0300")
    with expect_db_error(FOREIGN_KEY_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        pid = payment(cur, fresh, "100.00")
        credit = ledger(cur, fresh, entry_type="payment", amount=Decimal("-100.00"), payment_id=pid)
        allocate(cur, fresh, credit, fresh.late_fee, "10.00", student=other)  # s1's entries, other student


def test_allocation_credit_side_must_be_a_credit(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION, "ledger_allocations_guard"), tenant_tx(app_conn, fresh.org) as cur:
        allocate(cur, fresh, fresh.charge, fresh.late_fee, "1.00")


def test_reversed_entry_cannot_be_allocated(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION, "ledger_allocations_guard"), tenant_tx(app_conn, fresh.org) as cur:
        allocate(cur, fresh, fresh.e_pay2, fresh.late_fee, "10.00")


def test_waiver_must_use_manual_allocation(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION, "ledger_allocations_guard"), tenant_tx(app_conn, fresh.org) as cur:
        w = ledger(cur, fresh, entry_type="waiver", amount=Decimal("-10.00"), reason_code="waiver", reason_note="test")
        allocate(cur, fresh, w, fresh.late_fee, "10.00", manual=False)


def test_waiver_left_unallocated_rejected_at_commit(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION, "ledger_waiver_fully_allocated"), tenant_tx(app_conn, fresh.org) as cur:
        ledger(cur, fresh, entry_type="waiver", amount=Decimal("-10.00"), reason_code="waiver", reason_note="t")


def test_targeted_waiver_accepted(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        w = ledger(
            cur,
            fresh,
            entry_type="waiver",
            amount=Decimal("-50.00"),
            reason_code="waiver",
            reason_note="Waive the late fee",
        )
        allocate(cur, fresh, w, fresh.late_fee, "50.00", manual=True)


def test_released_allocation_cannot_be_reactivated(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(RESTRICT_VIOLATION, "ledger_allocations_release_once"), tenant_tx(app_conn, fresh.org) as cur:
        cur.execute(
            "UPDATE ledger_allocations SET released_at = NULL, released_by = NULL, release_reason = NULL WHERE id = %s",
            (fresh.alloc_rel,),
        )


def test_allocation_amount_cannot_be_edited(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(INSUFFICIENT_PRIVILEGE), tenant_tx(app_conn, fresh.org) as cur:
        cur.execute("UPDATE ledger_allocations SET amount = 1 WHERE id = %s", (fresh.alloc_pay1,))


# --- Idempotent jobs -----------------------------------------------------------------------------


def test_late_fee_unique_per_charge_and_day(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(UNIQUE_VIOLATION, "ledger_one_late_fee_per_day_uq"), tenant_tx(app_conn, fresh.org) as cur:
        ledger(
            cur,
            fresh,
            entry_type="late_fee",
            amount=Decimal("50.00"),
            source_entry_id=fresh.fine,
            late_fee_date=date(2026, 8, 20),
            due_date=date(2026, 8, 20),
            created_by=None,
        )


def test_charge_generated_once_per_period(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(UNIQUE_VIOLATION, "ledger_one_charge_per_period_uq"), tenant_tx(app_conn, fresh.org) as cur:
        ledger(
            cur,
            fresh,
            entry_type="charge",
            amount=Decimal("30000.00"),
            fee_structure_id=fresh.fee_rent,
            academic_session_id=fresh.session,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 11, 30),
            due_date=date(2026, 7, 10),
        )


def test_seeded_balances(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    """Sanity check that the world's money adds up as described in this module's docstring."""
    with tenant_tx(app_conn, fresh.org) as cur:
        cur.execute(
            "SELECT account, sum(amount) FROM ledger_entries WHERE student_id = %s GROUP BY account", (fresh.s1,)
        )
        balances = dict(cur.fetchall())
    assert balances == {"dues": Decimal("50.00"), "deposit": Decimal("3800.00")}
