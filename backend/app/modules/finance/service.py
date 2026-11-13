"""Money operations (docs/04 §12.9, 02 B3; Q24-Q31, Q60, Q64; 05 FIN-1..17).

Every operation that writes money for a student:
  1. locks the student row FIRST (ledger.lock_student; 03 §8 lock order),
  2. writes balanced, append-only rows in ONE transaction,
  3. re-settles allocations oldest-first (ledger.settle),
  4. writes an audit row,
and lets the database's triggers and constraints have the final word.
"""

from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit
from app.core.crud import not_found
from app.core.errors import ProblemError
from app.core.permissions import FINANCE_MANAGE, REFUNDS_APPROVE, Principal
from app.modules.allocations.service import local_today
from app.modules.finance import ledger
from app.modules.finance.models import (
    FeeStructure,
    LedgerAllocation,
    LedgerEntry,
    Payment,
    PaymentReversal,
    PaymentSubmission,
    RefundRequest,
)
from app.modules.notifications import service as notifications
from app.modules.organizations.models import AcademicSession, AcademicTerm
from app.modules.staff.service import active_administrator_count, lock_organization
from app.modules.students import service as students
from app.modules.students.models import Student


def money_student(db: Session, p: Principal, student_id: uuid.UUID, permission: str = FINANCE_MANAGE) -> Student:
    """Scope check (the student's hostel), then the student lock: always the first lock taken."""
    students.get_student(db, p, student_id, permission)
    return ledger.lock_student(db, student_id)


def _audit(
    db: Session,
    p: Principal | None,
    action: str,
    student: Student,
    entity_type: str,
    entity_id: uuid.UUID,
    after: dict[str, object] | None = None,
) -> None:
    actor = p if p is not None else audit.SYSTEM
    audit.record(
        db, actor, action, entity_type, entity_id, org_id=student.org_id, hostel_id=student.hostel_id, after=after
    )


def _notify(db: Session, student: Student, title: str, body: str) -> None:
    notifications.notify(
        db, org_id=student.org_id, user_id=student.user_id, category="fees", title=title, body=body, link="/portal/fees"
    )


# --- charges and fines ------------------------------------------------------------------------------


def add_charge(
    db: Session,
    p: Principal,
    student_id: uuid.UUID,
    *,
    kind: str,
    amount: Decimal,
    description: str,
    due_date: date,
    reason_code: str | None,
    reason_note: str | None,
) -> LedgerEntry:
    student = money_student(db, p, student_id)
    session = students.current_session(db)
    entry = LedgerEntry(
        org_id=student.org_id,
        hostel_id=student.hostel_id,
        student_id=student.id,
        txn_id=uuid.uuid4(),
        account="dues",
        entry_type=kind,
        amount=amount,
        effective_date=local_today(db),
        description=description,
        due_date=due_date,
        created_by=p.user_id,
        academic_session_id=session.id if kind == "charge" else None,
        reason_code=reason_code,
        reason_note=reason_note,
    )
    db.add(entry)
    db.flush()
    ledger.settle(db, student)
    _audit(
        db,
        p,
        f"ledger.{kind}_added",
        student,
        "ledger_entry",
        entry.id,
        after={"amount": amount, "description": description},
    )
    _notify(db, student, "New charge" if kind == "charge" else "A fine was added", f"{description}: Rs {amount}.")
    return entry


def add_waiver(
    db: Session,
    p: Principal,
    student_id: uuid.UUID,
    *,
    target_entry_id: uuid.UUID,
    amount: Decimal,
    reason_code: str,
    reason_note: str,
) -> LedgerEntry:
    """Q64: a waiver is FOR one charge: it is allocated to that charge, manually, in this transaction."""
    student = money_student(db, p, student_id)
    target = db.get(LedgerEntry, target_entry_id)
    if (
        target is None
        or target.student_id != student.id
        or target.entry_type not in ("charge", "late_fee", "fine")
        or ledger.is_reversed(db, target.id)
    ):
        raise ProblemError(
            422,
            "validation_error",
            "Invalid request",
            errors=[
                {
                    "field": "target_entry_id",
                    "code": "invalid_target",
                    "message": "Choose an open charge of this student",
                }
            ],
        )
    left = ledger.outstanding(db, target.id)
    if amount > left:
        # The credit allocated to this charge oldest-first is moved to other charges first (reallocation).
        _free_charge(db, student, target, amount - left, p.user_id)
        left = ledger.outstanding(db, target.id)
    if amount > left:
        raise ProblemError(
            409,
            "waiver_exceeds_outstanding",
            "A waiver can't exceed what is still owed on the charge",
            detail=f"Outstanding: Rs {left}.",
        )
    waiver = LedgerEntry(
        org_id=student.org_id,
        hostel_id=student.hostel_id,
        student_id=student.id,
        txn_id=uuid.uuid4(),
        account="dues",
        entry_type="waiver",
        amount=-amount,
        effective_date=local_today(db),
        description=f"Waiver: {target.description}"[:200],
        reason_code=reason_code,
        reason_note=reason_note,
        created_by=p.user_id,
    )
    db.add(waiver)
    db.flush()
    db.add(
        LedgerAllocation(
            org_id=student.org_id,
            student_id=student.id,
            credit_entry_id=waiver.id,
            debit_entry_id=target.id,
            amount=amount,
            method="manual",
            allocated_by=p.user_id,
        )
    )
    db.flush()
    ledger.settle(db, student)  # any credit freed above goes to the next oldest charge
    _audit(
        db,
        p,
        "ledger.waiver_added",
        student,
        "ledger_entry",
        waiver.id,
        after={"amount": amount, "target_entry_id": target.id, "reason_code": reason_code},
    )
    return waiver


def _free_charge(db: Session, student: Student, target: LedgerEntry, needed: Decimal, by: uuid.UUID) -> None:
    """Release automatic allocations on `target` (newest first) worth at least `needed`, so a waiver
    can take their place; settle() then moves that credit to other open charges."""
    rows = db.scalars(
        select(LedgerAllocation)
        .where(
            LedgerAllocation.debit_entry_id == target.id,
            LedgerAllocation.released_at.is_(None),
            LedgerAllocation.method == "auto_oldest_first",
        )
        .order_by(LedgerAllocation.allocated_at.desc())
    ).all()
    freed = Decimal("0")
    for a in rows:
        if freed >= needed:
            break
        a.released_at, a.released_by, a.release_reason = datetime.now(UTC), by, "reallocated"
        freed += a.amount
    db.flush()


def reverse_ledger_entry(db: Session, p: Principal, entry_id: uuid.UUID, reason: str) -> LedgerEntry:
    entry = db.get(LedgerEntry, entry_id)
    if entry is None:
        raise not_found()
    student = money_student(db, p, entry.student_id)
    if entry.payment_id is not None:
        raise ProblemError(409, "reverse_the_payment", "Payment entries are reversed by reversing the payment")
    if entry.entry_type in ("reversal", "refund", "deposit_refunded"):
        raise ProblemError(409, "invalid_reversal", "This entry can't be reversed here")
    today = local_today(db)
    reversals = []
    group = [entry]
    if entry.entry_type == "deposit_applied":  # both legs of a deposit application go together
        group = list(
            db.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.txn_id == entry.txn_id, LedgerEntry.entry_type == "deposit_applied"
                )
            )
        )
    if entry.amount > 0:
        # A waiver is fully allocated to exactly one charge (Q64). If that charge goes, its waiver must
        # go with it; otherwise the waiver stays as credit nobody paid, and could be refunded in cash
        # (review Critical 1).
        waivers = db.scalars(
            select(LedgerEntry)
            .join(LedgerAllocation, LedgerAllocation.credit_entry_id == LedgerEntry.id)
            .where(
                LedgerAllocation.debit_entry_id == entry.id,
                LedgerAllocation.released_at.is_(None),
                LedgerEntry.entry_type == "waiver",
            )
        ).all()
        for wv in waivers:
            reversals.append(ledger.reverse_entry(db, wv, f"Charge reversed: {reason}"[:500], p.user_id, today))
            _audit(db, p, "ledger.entry_reversed", student, "ledger_entry", wv.id, after={"reason": "charge reversed"})
    for e in group:
        reversals.append(ledger.reverse_entry(db, e, reason, p.user_id, today))
    ledger.settle(db, student)
    _audit(db, p, "ledger.entry_reversed", student, "ledger_entry", entry.id, after={"reason": reason})
    return next(r for r in reversals if r.reverses_entry_id == entry.id)  # its own, not a waiver's


# --- payments ---------------------------------------------------------------------------------------------


def _not_in_future(db: Session, day: date, field: str) -> None:
    """A payment can't be dated after the organization's today: the date picks the receipt's financial
    year (Q29), and a future one would start next year's series early (review Should fix 1)."""
    if day > local_today(db):
        raise ProblemError(
            422,
            "validation_error",
            "Invalid request",
            errors=[{"field": field, "code": "date_in_future", "message": "Payment date can't be in the future."}],
        )


@dataclass(frozen=True)
class PaymentRequest:
    student_id: uuid.UUID
    method: str
    reference: str | None
    received_on: date
    dues_amount: Decimal
    deposit_amount: Decimal
    notes: str | None


def _same_request(p: Payment, r: PaymentRequest, deposit_part: Decimal) -> bool:
    return (
        p.student_id == r.student_id
        and p.method == r.method
        and p.reference == r.reference
        and p.received_on == r.received_on
        and p.amount == r.dues_amount + r.deposit_amount
        and deposit_part == r.deposit_amount
    )


def _deposit_part(db: Session, payment_id: uuid.UUID) -> Decimal:
    return Decimal(
        db.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
                LedgerEntry.payment_id == payment_id, LedgerEntry.entry_type == "deposit_received"
            )
        )
        or 0
    )


def record_payment(
    db: Session, p: Principal, r: PaymentRequest, idempotency_key: uuid.UUID, submission_id: uuid.UUID | None = None
) -> tuple[Payment, bool]:
    """Returns (payment, created). The same Idempotency-Key with the same body returns the original
    payment; with a different body it is refused (04 §10, FIN-2)."""
    existing = db.scalars(select(Payment).where(Payment.idempotency_key == idempotency_key)).first()
    if existing is not None:
        if not _same_request(existing, r, _deposit_part(db, existing.id)):
            raise ProblemError(422, "idempotency_key_reused", "This Idempotency-Key was used for a different payment")
        return existing, False
    if r.dues_amount + r.deposit_amount <= 0:
        raise ProblemError(422, "validation_error", "The payment must be more than zero")
    if r.method != "cash" and not r.reference:
        raise ProblemError(
            422,
            "validation_error",
            "Invalid request",
            errors=[
                {
                    "field": "reference",
                    "code": "reference_required",
                    "message": "UPI and bank payments need a reference",
                }
            ],
        )
    student = money_student(db, p, r.student_id)
    _not_in_future(db, r.received_on, "received_on")  # every path: the admin form and verified UPI submissions
    fy = ledger.financial_year(r.received_on)
    try:
        with db.begin_nested():  # a duplicate key from a concurrent identical request lands here
            payment = Payment(
                org_id=student.org_id,
                hostel_id=student.hostel_id,
                student_id=student.id,
                amount=r.dues_amount + r.deposit_amount,
                method=r.method,
                reference=r.reference,
                received_on=r.received_on,
                financial_year=fy,
                receipt_seq=ledger.next_receipt_seq(db, student.org_id, fy),
                idempotency_key=idempotency_key,
                submission_id=submission_id,
                recorded_by=p.user_id,
                notes=r.notes,
            )
            db.add(payment)
            db.flush()
    except IntegrityError as exc:
        constraint = exc.orig.diag.constraint_name if isinstance(exc.orig, psycopg.Error) else None
        if constraint != "payments_org_id_idempotency_key_key":
            raise
        original = db.scalars(select(Payment).where(Payment.idempotency_key == idempotency_key)).one()
        if not _same_request(original, r, _deposit_part(db, original.id)):
            raise ProblemError(
                422, "idempotency_key_reused", "This Idempotency-Key was used for a different payment"
            ) from exc
        return original, False
    txn = uuid.uuid4()
    common = {
        "org_id": student.org_id,
        "hostel_id": student.hostel_id,
        "student_id": student.id,
        "txn_id": txn,
        "effective_date": r.received_on,
        "payment_id": payment.id,
        "created_by": p.user_id,
    }
    if r.dues_amount > 0:
        db.add(
            LedgerEntry(
                **common,
                account="dues",
                entry_type="payment",
                amount=-r.dues_amount,
                description=f"Payment {payment.method} ({payment.financial_year}/{payment.receipt_seq})",
            )
        )
    if r.deposit_amount > 0:
        db.add(
            LedgerEntry(
                **common,
                account="deposit",
                entry_type="deposit_received",
                amount=r.deposit_amount,
                description="Security deposit received",
            )
        )
    db.flush()
    ledger.settle(db, student)
    db.refresh(payment)
    _audit(
        db,
        p,
        "payment.recorded",
        student,
        "payment",
        payment.id,
        after={"amount": payment.amount, "method": payment.method, "receipt_number": payment.receipt_number},
    )
    _notify(db, student, "Payment received", f"Rs {payment.amount} received. Receipt {payment.receipt_number}.")
    return payment, True


def reverse_payment(db: Session, p: Principal, payment_id: uuid.UUID, reason: str) -> Payment:
    """Q31: payments are never edited or deleted; a reversal cancels ALL of a payment's entries."""
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise not_found()
    student = money_student(db, p, payment.student_id)
    if db.get(PaymentReversal, payment.id) is not None:
        raise ProblemError(409, "already_reversed", "This payment was already reversed")
    today = local_today(db)
    for entry in db.scalars(select(LedgerEntry).where(LedgerEntry.payment_id == payment.id)).all():
        ledger.reverse_entry(db, entry, reason, p.user_id, today)
    db.add(PaymentReversal(payment_id=payment.id, org_id=payment.org_id, reason=reason, reversed_by=p.user_id))
    db.flush()
    ledger.settle(db, student)
    _audit(db, p, "payment.reversed", student, "payment", payment.id, after={"reason": reason})
    return payment


# --- deposits and refunds -------------------------------------------------------------------------------------


def apply_deposit(db: Session, p: Principal, student_id: uuid.UUID, amount: Decimal) -> uuid.UUID:
    """Exit settlement (Q22): move held deposit to dues. Two legs, one transaction; the database
    checks both are equal and that the deposit doesn't go negative."""
    student = money_student(db, p, student_id)
    txn = uuid.uuid4()
    today = local_today(db)
    for account in ("dues", "deposit"):
        db.add(
            LedgerEntry(
                org_id=student.org_id,
                hostel_id=student.hostel_id,
                student_id=student.id,
                txn_id=txn,
                account=account,
                entry_type="deposit_applied",
                amount=-amount,
                effective_date=today,
                description="Deposit applied to dues",
                created_by=p.user_id,
            )
        )
    db.flush()
    ledger.settle(db, student)
    _audit(db, p, "deposit.applied", student, "student", student.id, after={"amount": amount})
    return txn


def request_refund(
    db: Session, p: Principal, student_id: uuid.UUID, account: str, amount: Decimal, reason: str
) -> RefundRequest:
    student = money_student(db, p, student_id)
    dues, deposit = ledger.balances(db, student.id)
    # Requests still waiting or approved (not yet paid) already claim part of the money (review Minor 12).
    claimed = db.scalar(
        select(func.coalesce(func.sum(RefundRequest.amount), 0)).where(
            RefundRequest.student_id == student.id,
            RefundRequest.account == account,
            RefundRequest.status.in_(("requested", "approved")),
        )
    ) or Decimal("0")
    available = (deposit if account == "deposit" else -dues) - Decimal(claimed)
    if amount > available:
        raise ProblemError(
            409,
            "refund_exceeds_credit" if account == "dues" else "insufficient_deposit",
            "The refund is more than the student has",
            detail=f"Available: Rs {max(available, 0)}.",
        )
    rr = RefundRequest(
        org_id=student.org_id,
        hostel_id=student.hostel_id,
        student_id=student.id,
        account=account,
        amount=amount,
        reason=reason,
        requested_by=p.user_id,
    )
    db.add(rr)
    db.flush()
    _audit(db, p, "refund.requested", student, "refund_request", rr.id, after={"amount": amount, "account": account})
    return rr


def cancel_refund(db: Session, p: Principal, refund_id: uuid.UUID) -> RefundRequest:
    """Withdraw a request that is still waiting (review Minor 12): the money becomes available again."""
    rr, student = _refund(db, p, refund_id, FINANCE_MANAGE)
    if rr.status != "requested":
        raise ProblemError(409, "refund_state", f"Only a waiting refund can be withdrawn; this one is {rr.status}")
    rr.status = "cancelled"
    db.flush()
    _audit(db, p, "refund.cancelled", student, "refund_request", rr.id, after={"status": "cancelled"})
    return rr


def _refund(db: Session, p: Principal, refund_id: uuid.UUID, permission: str) -> tuple[RefundRequest, Student]:
    rr = db.get(RefundRequest, refund_id)
    if rr is None:
        raise not_found()
    student = money_student(db, p, rr.student_id, permission)  # scope: the student's hostel; lock: student first
    rr = db.scalars(
        select(RefundRequest)
        .where(RefundRequest.id == refund_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    return rr, student


def decide_refund(db: Session, p: Principal, refund_id: uuid.UUID, approve: bool, note: str | None) -> RefundRequest:
    """Maker-checker (Q30), as far as one person allows. Since D76 an organization has exactly one
    Administrator, so the requester (the Administrator) APPROVES their own request: the approval is
    marked `self_approved` and audited as refund.self_approved_sole_administrator (D75). A request made
    by someone else (an account closed by D75/D76) is decided normally. To drop your own request,
    withdraw it. The database (constraint refund_requests_second_person and the trigger
    refund_self_approval_guard, under the organization-row lock) is the backstop."""
    rr, student = _refund(db, p, refund_id, REFUNDS_APPROVE)
    if rr.status != "requested":
        raise ProblemError(409, "refund_state", f"This refund is already {rr.status}")
    self_approved = False
    if rr.requested_by == p.user_id:
        if not approve:
            raise ProblemError(
                403,
                "second_person_required",
                "You requested this refund",
                detail="To drop it, withdraw it instead.",
            )
        lock_organization(db, rr.org_id)  # the same lock the database trigger takes
        if active_administrator_count(db) != 1:  # always 1 since D76; kept as a second check
            raise ProblemError(
                403,
                "second_person_required",
                "Another administrator must approve this refund",
                detail="You requested it, and the organization has other administrators.",
            )
        self_approved = True
    rr.status = "approved" if approve else "rejected"
    rr.decided_by, rr.decided_at, rr.decision_note = p.user_id, datetime.now(UTC), note
    rr.self_approved = self_approved
    db.flush()
    action = "refund.self_approved_sole_administrator" if self_approved else f"refund.{rr.status}"
    _audit(db, p, action, student, "refund_request", rr.id, after={"status": rr.status, "self_approved": self_approved})
    return rr


def pay_refund(db: Session, p: Principal, refund_id: uuid.UUID, method: str, reference: str | None) -> RefundRequest:
    rr, student = _refund(db, p, refund_id, FINANCE_MANAGE)
    if rr.status != "approved":
        raise ProblemError(409, "refund_not_approved", "Only an approved refund can be paid")
    if method != "cash" and not reference:
        raise ProblemError(422, "validation_error", "Non-cash refunds need a reference")
    # The entry goes in while the request is still 'approved': ledger_guard only accepts refund money
    # for an approved request (03 §7). Then the request becomes 'paid', in the same transaction.
    db.add(
        LedgerEntry(
            org_id=student.org_id,
            hostel_id=student.hostel_id,
            student_id=student.id,
            txn_id=uuid.uuid4(),
            account=rr.account,
            entry_type="refund" if rr.account == "dues" else "deposit_refunded",
            amount=rr.amount if rr.account == "dues" else -rr.amount,
            effective_date=local_today(db),
            description="Refund paid" if rr.account == "dues" else "Deposit refunded",
            refund_request_id=rr.id,
            created_by=p.user_id,
        )
    )
    db.flush()  # ledger_guard: approved, exact amount, balance floors (FIN-7)
    rr.status, rr.paid_method, rr.paid_reference, rr.paid_at = "paid", method, reference, datetime.now(UTC)
    db.flush()
    ledger.settle(db, student)
    _audit(db, p, "refund.paid", student, "refund_request", rr.id, after={"method": method})
    _notify(db, student, "Refund paid", f"Rs {rr.amount} refunded by {method}.")
    return rr


# --- UPI submissions (Q28) ---------------------------------------------------------------------------------------


def submit_payment(
    db: Session,
    p: Principal,
    student: Student,
    amount: Decimal,
    utr: str,
    paid_on: date,
    proof_file_id: uuid.UUID | None,
) -> PaymentSubmission:
    _not_in_future(db, paid_on, "paid_on")
    if proof_file_id is not None:
        from app.modules.files.models import File
        from app.modules.files.service import unattached_clause

        f = db.scalars(
            select(File).where(
                File.id == proof_file_id, File.deleted_at.is_(None), unattached_clause(), File.uploaded_by == p.user_id
            )
        ).first()
        if f is None or f.category != "payment_proof":
            raise ProblemError(422, "validation_error", "Upload the payment screenshot first")
    sub = PaymentSubmission(
        org_id=student.org_id,
        hostel_id=student.hostel_id,
        student_id=student.id,
        amount=amount,
        utr=utr.strip().upper(),
        paid_on=paid_on,
        proof_file_id=proof_file_id,
        submitted_by=p.user_id,
    )
    db.add(sub)
    db.flush()  # payment_submissions_utr_uq -> 409 utr_already_submitted
    _audit(db, p, "payment_submission.created", student, "payment_submission", sub.id, after={"amount": amount})
    return sub


def review_submission(
    db: Session, p: Principal, submission_id: uuid.UUID, verify: bool, note: str | None
) -> PaymentSubmission:
    first = db.get(PaymentSubmission, submission_id)
    if first is None:
        raise not_found()
    student = money_student(db, p, first.student_id)
    sub = db.scalars(
        select(PaymentSubmission)
        .where(PaymentSubmission.id == submission_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if sub.status != "pending":
        raise ProblemError(409, "submission_state", f"This submission is already {sub.status}")
    if not verify and not note:
        raise ProblemError(422, "validation_error", "Say why the submission is rejected")
    sub.status = "verified" if verify else "rejected"
    sub.reviewed_by, sub.reviewed_at, sub.review_note = p.user_id, datetime.now(UTC), note
    db.flush()
    if verify:
        record_payment(
            db,
            p,
            PaymentRequest(
                student.id, "upi", sub.utr, sub.paid_on, sub.amount, Decimal("0"), "UPI submission verified"
            ),
            idempotency_key=sub.id,
            submission_id=sub.id,
        )
    else:
        _notify(db, student, "UPI payment not accepted", f"Reason: {note}")
    _audit(db, p, f"payment_submission.{sub.status}", student, "payment_submission", sub.id)
    return sub


# --- charge runs (Q24) and late fees (Q26) ----------------------------------------------------------------------


def period_end(db: Session, fs: FeeStructure, start: date) -> date | None:
    if fs.billing_cycle == "monthly":
        # A monthly period is a calendar month (D68). Starting on the 15th would bill a full month for
        # half of one, and, because a charge is unique per (student, structure, period start), a second
        # run from the 1st would charge the same month twice.
        if start.day != 1:
            raise ProblemError(422, "validation_error", "A monthly period starts on the 1st of a month")
        last = calendar.monthrange(start.year, start.month)[1]
        return start.replace(day=last)
    if fs.billing_cycle == "term":
        term = db.scalars(
            select(AcademicTerm).where(
                AcademicTerm.academic_session_id == fs.academic_session_id,
                AcademicTerm.starts_on <= start,
                AcademicTerm.ends_on >= start,
            )
        ).first()
        if term is None:
            raise ProblemError(422, "validation_error", "No term of the session contains that date")
        return term.ends_on
    session = db.get(AcademicSession, fs.academic_session_id)
    return session.ends_on if session and fs.billing_cycle == "annual" else None


def period_window(db: Session, fs: FeeStructure, start: date) -> tuple[date | None, date | None]:
    """The span in which a structure charges a student at most once (review Critical 3): the calendar
    month, the term containing `start`, or (annual and one-time) the whole structure, which belongs to
    one academic session. The unique index only knows the exact start date, so runs from two dates in
    the same period used to charge twice."""
    if fs.billing_cycle == "monthly":
        return start, period_end(db, fs, start)
    if fs.billing_cycle == "term":
        term = db.scalars(
            select(AcademicTerm).where(
                AcademicTerm.academic_session_id == fs.academic_session_id,
                AcademicTerm.starts_on <= start,
                AcademicTerm.ends_on >= start,
            )
        ).first()
        if term is None:
            raise ProblemError(422, "validation_error", "No term of the session contains that date")
        return term.starts_on, term.ends_on
    return None, None


def eligible_students(db: Session, fs: FeeStructure, on: date) -> list[Student]:
    """Students living in the structure's hostel/room type on the period start (active stay)."""
    q = text(
        """
        SELECT DISTINCT a.student_id FROM bed_allocations a
        JOIN beds b ON b.id = a.bed_id JOIN rooms r ON r.id = b.room_id
        JOIN students s ON s.id = a.student_id
        WHERE a.status IN ('active', 'ended') AND a.stay @> CAST(:on AS date) AND s.status = 'active'
          AND (CAST(:hostel AS uuid) IS NULL OR a.hostel_id = CAST(:hostel AS uuid))
          AND (CAST(:room_type AS uuid) IS NULL OR r.room_type_id = CAST(:room_type AS uuid))
        """
    )
    ids = [r[0] for r in db.execute(q, {"on": on, "hostel": fs.hostel_id, "room_type": fs.room_type_id})]
    return list(db.scalars(select(Student).where(Student.id.in_(ids)).order_by(Student.id)))


def run_charges(db: Session, fee_structure_id: uuid.UUID, start: date, by: uuid.UUID | None) -> int:
    """Generate one charge per eligible student for the period. Safe to rerun: the unique index
    (student, fee structure, period start) makes a second charge impossible (FIN-11)."""
    fs = db.get(FeeStructure, fee_structure_id)
    if fs is None or fs.fee_kind == "deposit" or not fs.is_active:
        return 0
    end = period_end(db, fs, start)
    lo, hi = period_window(db, fs, start)
    created = 0
    for s in eligible_students(db, fs, start):
        ledger.lock_student(db, s.id)  # student first, one student at a time: the check below can't race
        already = (
            select(func.count())
            .select_from(LedgerEntry)
            .where(
                LedgerEntry.student_id == s.id,
                LedgerEntry.fee_structure_id == fs.id,
                LedgerEntry.entry_type == "charge",
            )
        )
        if lo is not None:
            already = already.where(LedgerEntry.period_start >= lo)
        if hi is not None:
            already = already.where(LedgerEntry.period_start <= hi)
        if db.scalar(already):
            continue
        db.add(
            LedgerEntry(
                org_id=s.org_id,
                hostel_id=s.hostel_id,
                student_id=s.id,
                txn_id=uuid.uuid4(),
                account="dues",
                entry_type="charge",
                amount=fs.amount,
                effective_date=start,
                description=f"{fs.name} ({start:%d-%m-%Y})"[:200],  # dates as people read them here (Q48)
                fee_structure_id=fs.id,
                academic_session_id=fs.academic_session_id,
                period_start=start,
                period_end=end,
                due_date=start + timedelta(days=fs.due_days),
                created_by=by,
            )
        )
        db.flush()
        ledger.settle(db, s)
        _notify(db, s, "New charge", f"{fs.name}: Rs {fs.amount}, due {start + timedelta(days=fs.due_days):%d-%m-%Y}.")
        created += 1
    return created


def apply_late_fees(db: Session, today: date) -> int:
    """Daily (Q26): per overdue charge, a flat fee once, or a per-day fee up to the cap. One row per
    charge per day with a unique key, so a rerun can't double-charge (FIN-11)."""
    rows = db.execute(
        text(
            """
        SELECT e.id, e.student_id, e.due_date, f.grace_days, f.late_fee_type, f.late_fee_amount, f.late_fee_cap
        FROM ledger_entries e JOIN fee_structures f ON f.id = e.fee_structure_id
        WHERE e.entry_type = 'charge' AND f.late_fee_type <> 'none'
          AND NOT EXISTS (SELECT 1 FROM ledger_entries r WHERE r.reverses_entry_id = e.id)
          AND e.due_date + f.grace_days < CAST(:today AS date)
        ORDER BY e.student_id, e.id
        """
        ),
        {"today": today},
    ).all()
    created = 0
    for charge_id, student_id, due, grace, kind, amount, cap in rows:
        student = ledger.lock_student(db, student_id)
        if ledger.outstanding(db, charge_id) <= 0:
            continue
        first_day = due + timedelta(days=grace + 1)
        days = (
            [first_day]
            if kind == "flat"
            else [first_day + timedelta(days=i) for i in range((today - first_day).days + 1)]
        )
        charge = db.get(LedgerEntry, charge_id)
        assert charge is not None
        for day in days:
            if db.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.source_entry_id == charge_id, LedgerEntry.late_fee_date == day)
            ):
                continue
            fee = Decimal(amount)
            if kind == "per_day":
                charged = Decimal(
                    db.scalar(
                        text(
                            "SELECT coalesce(sum(e.amount), 0) FROM ledger_entries e WHERE e.source_entry_id = :c "
                            "AND e.entry_type = 'late_fee' AND NOT EXISTS "
                            "(SELECT 1 FROM ledger_entries r WHERE r.reverses_entry_id = e.id)"
                        ),
                        {"c": charge_id},
                    )
                    or 0
                )
                fee = min(fee, Decimal(cap) - charged)
                if fee <= 0:
                    break
            db.add(
                LedgerEntry(
                    org_id=student.org_id,
                    hostel_id=student.hostel_id,
                    student_id=student.id,
                    txn_id=uuid.uuid4(),
                    account="dues",
                    entry_type="late_fee",
                    amount=fee,
                    effective_date=day,
                    description=f"Late fee: {charge.description}"[:200],
                    due_date=day,
                    source_entry_id=charge_id,
                    late_fee_date=day,
                )
            )
            db.flush()
            created += 1
        ledger.settle(db, student)
    return created
