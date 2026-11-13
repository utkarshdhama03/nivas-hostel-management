"""The ledger core (docs/02 B3, 03 §5.8, §9; Q26, Q29, Q60, Q64).

Two balances per student, both SUMs of append-only ledger rows:
    dues     what the student owes (+) or has in credit (-)
    deposit  the security deposit the organization holds
The database enforces signs, reversals, balance floors and "a payment's entries add up"
(triggers, 03 §7). This module adds what the database can't decide alone:

SETTLEMENT (Q60): which credit pays which charge. After every money change for a student,
`settle()` allocates unallocated credits (payments, deposit applications) to open debits
(charges, late fees, fines, dues refunds), oldest first. Waivers are never auto-allocated:
each targets its chosen charge (Q64). Invariant kept: a student never has an open charge
AND unallocated credit at the same time (05 FIN-6 reconciliation).

Lock order (03 §8): `lock_student()` is called FIRST in every money operation. The
database's own guards lock the same row, so all money work for one student is serialized.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, exists, func, select, text, update
from sqlalchemy.orm import Session, aliased

from app.core.crud import not_found
from app.modules.finance.models import LedgerAllocation, LedgerEntry
from app.modules.students.models import Student

ZERO = Decimal("0.00")
DEBIT_TYPES = ("charge", "late_fee", "fine", "refund")
AUTO_CREDIT_TYPES = ("payment", "deposit_applied")  # waivers are allocated manually (Q64)


def lock_student(db: Session, student_id: uuid.UUID) -> Student:
    student = db.scalars(select(Student).where(Student.id == student_id).with_for_update()).one_or_none()
    if student is None:
        raise not_found()
    return student


def financial_year(day: date) -> str:
    """Indian financial year, April to March (Q29): 2026-05-10 -> '2026-27'."""
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def next_receipt_seq(db: Session, org_id: uuid.UUID, fy: str) -> int:
    """Gap-free (Q29): the counter row changes in the payment's own transaction, so a failed
    payment rolls its number back. Called AFTER lock_student (lock order: student, then this)."""
    db.execute(
        text(
            "INSERT INTO org_counters (org_id, name, scope_key, last_value) VALUES (:o, 'receipt', :fy, 0) "
            "ON CONFLICT DO NOTHING"
        ),
        {"o": org_id, "fy": fy},
    )
    value = db.execute(
        text(
            "UPDATE org_counters SET last_value = last_value + 1 WHERE org_id = :o "
            "AND name = 'receipt' AND scope_key = :fy RETURNING last_value"
        ),
        {"o": org_id, "fy": fy},
    ).scalar_one()
    return int(value)


def balances(db: Session, student_id: uuid.UUID) -> tuple[Decimal, Decimal]:
    row = db.execute(
        select(
            func.coalesce(func.sum(LedgerEntry.amount).filter(LedgerEntry.account == "dues"), 0),
            func.coalesce(func.sum(LedgerEntry.amount).filter(LedgerEntry.account == "deposit"), 0),
        ).where(LedgerEntry.student_id == student_id)
    ).one()
    return Decimal(row[0]).quantize(Decimal("0.01")), Decimal(row[1]).quantize(Decimal("0.01"))


def _not_reversed() -> ColumnElement[bool]:
    r = aliased(LedgerEntry)
    return ~exists().where(r.reverses_entry_id == LedgerEntry.id)


def _allocated(column: Any) -> Any:
    return (
        select(func.coalesce(func.sum(LedgerAllocation.amount), 0))
        .where(column == LedgerEntry.id, LedgerAllocation.released_at.is_(None))
        .scalar_subquery()
    )


@dataclass(frozen=True)
class Open:
    entry: LedgerEntry
    remaining: Decimal


def open_debits(db: Session, student_id: uuid.UUID) -> list[Open]:
    """Charges, late fees, fines and refunds with something still to pay, oldest due first."""
    used = _allocated(LedgerAllocation.debit_entry_id)
    rows = db.execute(
        select(LedgerEntry, LedgerEntry.amount - used)
        .where(
            LedgerEntry.student_id == student_id,
            LedgerEntry.account == "dues",
            LedgerEntry.entry_type.in_(DEBIT_TYPES),
            _not_reversed(),
            LedgerEntry.amount - used > 0,
        )
        .order_by(
            func.coalesce(LedgerEntry.due_date, LedgerEntry.effective_date), LedgerEntry.created_at, LedgerEntry.id
        )
    ).all()
    return [Open(e, Decimal(r)) for e, r in rows]


def unallocated_credits(db: Session, student_id: uuid.UUID, types: tuple[str, ...] = AUTO_CREDIT_TYPES) -> list[Open]:
    used = _allocated(LedgerAllocation.credit_entry_id)
    rows = db.execute(
        select(LedgerEntry, -LedgerEntry.amount - used)
        .where(
            LedgerEntry.student_id == student_id,
            LedgerEntry.account == "dues",
            LedgerEntry.entry_type.in_(types),
            _not_reversed(),
            -LedgerEntry.amount - used > 0,
        )
        .order_by(LedgerEntry.effective_date, LedgerEntry.created_at, LedgerEntry.id)
    ).all()
    return [Open(e, Decimal(r)) for e, r in rows]


def settle(db: Session, student: Student) -> int:
    """Allocate unallocated credits to open debits, oldest first. Returns allocations made."""
    db.flush()
    debits = open_debits(db, student.id)
    credits = unallocated_credits(db, student.id)
    made, di, ci = 0, 0, 0
    d_left = debits[0].remaining if debits else ZERO
    c_left = credits[0].remaining if credits else ZERO
    while di < len(debits) and ci < len(credits):
        amount = min(d_left, c_left)
        credit_id, debit_id = credits[ci].entry.id, debits[di].entry.id
        # One active row per (credit, debit) pair (ledger_allocations_active_pair_uq). A payment that
        # already covers part of this charge (it paid two charges, then one was waived or reversed)
        # gets its rows merged: release the old one, add one for the total (review Critical 2).
        same_pair = db.scalars(
            select(LedgerAllocation).where(
                LedgerAllocation.credit_entry_id == credit_id,
                LedgerAllocation.debit_entry_id == debit_id,
                LedgerAllocation.released_at.is_(None),
                LedgerAllocation.method == "auto_oldest_first",
            )
        ).one_or_none()
        total = amount
        if same_pair is not None:
            same_pair.released_at, same_pair.release_reason = datetime.now(UTC), "reallocated"
            total += same_pair.amount
            db.flush()
        db.add(
            LedgerAllocation(
                org_id=student.org_id,
                student_id=student.id,
                credit_entry_id=credit_id,
                debit_entry_id=debit_id,
                amount=total,
                method="auto_oldest_first",
            )
        )
        made += 1
        d_left -= amount
        c_left -= amount
        if d_left == 0:
            di += 1
            d_left = debits[di].remaining if di < len(debits) else ZERO
        if c_left == 0:
            ci += 1
            c_left = credits[ci].remaining if ci < len(credits) else ZERO
    db.flush()
    return made


def outstanding(db: Session, entry_id: uuid.UUID) -> Decimal:
    entry = db.get(LedgerEntry, entry_id)
    assert entry is not None
    used = (
        db.scalar(
            select(func.coalesce(func.sum(LedgerAllocation.amount), 0)).where(
                LedgerAllocation.debit_entry_id == entry_id, LedgerAllocation.released_at.is_(None)
            )
        )
        or 0
    )
    return Decimal(entry.amount - used)


def release_allocations(db: Session, entry: LedgerEntry, reason: str, by: uuid.UUID | None) -> None:
    db.execute(
        update(LedgerAllocation)
        .where(
            LedgerAllocation.released_at.is_(None),
            (LedgerAllocation.credit_entry_id == entry.id) | (LedgerAllocation.debit_entry_id == entry.id),
        )
        .values(released_at=datetime.now(UTC), released_by=by, release_reason=reason)
    )


def reverse_entry(db: Session, entry: LedgerEntry, reason: str, by: uuid.UUID, today: date) -> LedgerEntry:
    """The exact opposite entry (FIN-5). Its allocations are released first; the database checks
    the reversal is exact, single, and leaves no active allocation (03 §7)."""
    side = "credit_reversed" if entry.amount < 0 else "debit_reversed"
    release_allocations(db, entry, side, by)
    reversal = LedgerEntry(
        org_id=entry.org_id,
        hostel_id=entry.hostel_id,
        student_id=entry.student_id,
        txn_id=uuid.uuid4(),
        account=entry.account,
        entry_type="reversal",
        amount=-entry.amount,
        effective_date=today,
        description=f"Reversal: {entry.description}"[:200],
        reverses_entry_id=entry.id,
        reason_note=reason,
        created_by=by,
    )
    db.add(reversal)
    db.flush()
    return reversal


def is_reversed(db: Session, entry_id: uuid.UUID) -> bool:
    return bool(
        db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.reverses_entry_id == entry_id))
    )
