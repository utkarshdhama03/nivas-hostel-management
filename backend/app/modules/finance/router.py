"""Fees, payments and the ledger: admin routes (docs/04 §12.9) and the student's fees (§12.18)."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit, jobs, ratelimit
from app.core.context import Ctx, OrgContext, require, staff_only, student_only
from app.core.crud import check_hostel, check_version, not_found
from app.core.errors import ProblemError
from app.core.models import Job
from app.core.money import MoneyIn, MoneyInOrZero, MoneyOut
from app.core.permissions import FINANCE_MANAGE, FINANCE_VIEW, REFUNDS_APPROVE
from app.core.schemas import Out, Strict, Versioned
from app.modules.finance import ledger, receipts, service
from app.modules.finance.models import (
    FeeStructure,
    LedgerAllocation,
    LedgerEntry,
    Payment,
    PaymentReversal,
    PaymentSubmission,
    RefundRequest,
)
from app.modules.hostels.models import Hostel
from app.modules.identity.models import User
from app.modules.organizations.models import AcademicSession, OrganizationProfile
from app.modules.students import service as students
from app.modules.students.models import Student

router = APIRouter(prefix="/admin", tags=["finance"], dependencies=[Depends(staff_only)])
portal = APIRouter(prefix="/portal/me", tags=["portal"], dependencies=[Depends(student_only)])
View = Annotated[OrgContext, Depends(require(FINANCE_VIEW))]
Manage = Annotated[OrgContext, Depends(require(FINANCE_MANAGE))]
Approve = Annotated[OrgContext, Depends(require(REFUNDS_APPROVE))]
Reason = Annotated[str, Field(min_length=5, max_length=500)]
EXPORTS_PER_HOUR = ratelimit.Limit("exports_per_user", 10, 3600)  # 04 §5.10


# --- fee structures ---------------------------------------------------------------------------------


class FeeStructureOut(Out):
    id: uuid.UUID
    academic_session_id: uuid.UUID
    hostel_id: uuid.UUID | None
    room_type_id: uuid.UUID | None
    name: str
    fee_kind: str
    amount: MoneyOut
    billing_cycle: str
    due_days: int
    grace_days: int
    late_fee_type: str
    late_fee_amount: MoneyOut | None
    late_fee_cap: MoneyOut | None
    is_active: bool
    version: int


class FeeStructureIn(Strict):
    academic_session_id: uuid.UUID | None = None  # default: the current session
    hostel_id: uuid.UUID | None = None
    room_type_id: uuid.UUID | None = None
    name: str = Field(min_length=2, max_length=80)
    fee_kind: Literal["rent", "deposit", "other"]
    amount: MoneyIn
    billing_cycle: Literal["one_time", "monthly", "term", "annual"]
    due_days: int = Field(default=10, ge=0, le=90)
    grace_days: int = Field(default=0, ge=0, le=90)
    late_fee_type: Literal["none", "flat", "per_day"] = "none"
    late_fee_amount: MoneyIn | None = None
    late_fee_cap: MoneyIn | None = None


class FeeStructurePatch(Versioned):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    amount: MoneyIn | None = None
    due_days: int | None = Field(default=None, ge=0, le=90)
    grace_days: int | None = Field(default=None, ge=0, le=90)
    is_active: bool | None = None


def _structure_scope(ctx: OrgContext, hostel_id: uuid.UUID | None) -> None:
    hostels = ctx.principal.hostels_for(FINANCE_MANAGE)
    if hostels is None:
        return
    if hostel_id is None:
        raise ProblemError(
            403,
            "permission_denied",
            "Not allowed",
            detail="Organization-wide fees need an Administrator.",
        )
    if hostel_id not in hostels:
        raise not_found()


def _hostel_exists(ctx: OrgContext, hostel_id: uuid.UUID | None) -> None:
    """A hostel ID that isn't this organization's (RLS hides it) is "not found", not a database
    conflict (review Should fix 5)."""
    if hostel_id is not None and ctx.db.get(Hostel, hostel_id) is None:
        raise not_found()


@router.get("/fee-structures")
def list_fee_structures(ctx: View) -> list[FeeStructureOut]:
    hostels = ctx.principal.hostels_for(FINANCE_VIEW)
    q = select(FeeStructure)
    if hostels is not None:
        q = q.where(FeeStructure.hostel_id.is_(None) | FeeStructure.hostel_id.in_(hostels))
    return [FeeStructureOut.model_validate(f) for f in ctx.db.scalars(q.order_by(FeeStructure.created_at))]


@router.post("/fee-structures", status_code=status.HTTP_201_CREATED)
def create_fee_structure(ctx: Manage, body: FeeStructureIn) -> FeeStructureOut:
    _structure_scope(ctx, body.hostel_id)
    _hostel_exists(ctx, body.hostel_id)
    session_id = body.academic_session_id or students.current_session(ctx.db).id
    if ctx.db.get(AcademicSession, session_id) is None:
        raise not_found()
    data = body.model_dump(exclude={"academic_session_id"})
    fs = FeeStructure(
        org_id=ctx.principal.org_id, academic_session_id=session_id, created_by=ctx.principal.user_id, **data
    )
    ctx.db.add(fs)
    ctx.db.flush()
    audit.record(
        ctx.db,
        ctx.principal,
        "fee_structure.created",
        "fee_structure",
        fs.id,
        hostel_id=fs.hostel_id,
        after={"name": fs.name, "amount": fs.amount, "billing_cycle": fs.billing_cycle},
    )
    ctx.db.refresh(fs)
    return FeeStructureOut.model_validate(fs)


@router.patch("/fee-structures/{fee_structure_id}")
def patch_fee_structure(ctx: Manage, fee_structure_id: uuid.UUID, body: FeeStructurePatch) -> FeeStructureOut:
    fs = ctx.db.scalars(select(FeeStructure).where(FeeStructure.id == fee_structure_id).with_for_update()).first()
    if fs is None:
        raise not_found()
    _structure_scope(ctx, fs.hostel_id)
    check_version(fs, body.version)
    sent = body.model_dump(exclude_unset=True, exclude={"version"})
    if (
        "amount" in sent
        and sent["amount"] != fs.amount
        and ctx.db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.fee_structure_id == fs.id))
    ):
        raise ProblemError(
            409,
            "fee_structure_in_use",
            "The amount can't change once charges exist",
            detail="Create a new fee structure for the new amount (FIN-14).",
        )
    before = {k: getattr(fs, k) for k in sent}
    for key, value in sent.items():
        setattr(fs, key, value)
    fs.updated_at = func.app_now()
    ctx.db.flush()
    audit.record(
        ctx.db,
        ctx.principal,
        "fee_structure.updated",
        "fee_structure",
        fs.id,
        hostel_id=fs.hostel_id,
        before=before,
        after=sent,
    )
    ctx.db.refresh(fs)
    return FeeStructureOut.model_validate(fs)


class ChargeRunIn(Strict):
    fee_structure_id: uuid.UUID
    period_start: date


class ChargeRunOut(Out):
    job_id: uuid.UUID | None
    status: str


@router.post("/charge-runs", status_code=status.HTTP_202_ACCEPTED)
def start_charge_run(ctx: Manage, body: ChargeRunIn) -> ChargeRunOut:
    """Generate the period's charges in the background (04 §12.9). Safe to repeat."""
    fs = ctx.db.get(FeeStructure, body.fee_structure_id)
    if fs is None:
        raise not_found()
    _structure_scope(ctx, fs.hostel_id)
    if fs.fee_kind == "deposit":
        raise ProblemError(422, "validation_error", "Deposits are not charged; they are collected with a payment")
    service.period_end(ctx.db, fs, body.period_start)  # validates term periods now, not later in the job
    job_id = jobs.enqueue(
        ctx.db,
        "fees.charge_run",
        {
            "fee_structure_id": str(fs.id),
            "period_start": body.period_start.isoformat(),
            "requested_by": str(ctx.principal.user_id),
        },
        org_id=ctx.principal.org_id,
        dedupe_key=f"charge-run:{fs.id}:{body.period_start.isoformat()}",
    )
    audit.record(
        ctx.db,
        ctx.principal,
        "charge_run.requested",
        "fee_structure",
        fs.id,
        hostel_id=fs.hostel_id,
        after={"period_start": body.period_start},
    )
    return ChargeRunOut(job_id=job_id, status="queued" if job_id else "already_queued")


@router.get("/charge-runs/{job_id}")
def charge_run_status(ctx: Manage, job_id: uuid.UUID) -> ChargeRunOut:
    row = ctx.db.execute(select(Job.status).where(Job.id == job_id, Job.kind == "fees.charge_run")).first()
    if row is None:
        raise not_found()
    return ChargeRunOut(job_id=job_id, status=row[0])


# --- balances and ledgers ---------------------------------------------------------------------------------------


class OpenChargeOut(Out):
    entry_id: uuid.UUID
    entry_type: str
    description: str
    due_date: date | None
    amount: MoneyOut
    outstanding: MoneyOut


class BalanceOut(Out):
    student_id: uuid.UUID
    dues: MoneyOut
    deposit_held: MoneyOut
    deposit_required: MoneyOut
    deposit_due: MoneyOut
    unallocated_credit: MoneyOut
    open_charges: list[OpenChargeOut]


class LedgerAllocationOut(Out):
    credit_entry_id: uuid.UUID
    debit_entry_id: uuid.UUID
    amount: MoneyOut
    method: str
    released: bool


class EntryOut(Out):
    id: uuid.UUID
    account: str
    entry_type: str
    amount: MoneyOut
    effective_date: date
    description: str
    due_date: date | None
    payment_id: uuid.UUID | None
    reverses_entry_id: uuid.UUID | None
    reversed: bool
    reason_code: str | None
    created_at: datetime
    allocations: list[LedgerAllocationOut]


def balance_for(db: Session, student: Student) -> BalanceOut:
    dues, deposit = ledger.balances(db, student.id)
    required = db.scalar(
        select(func.coalesce(func.max(FeeStructure.amount), 0)).where(
            FeeStructure.fee_kind == "deposit",
            FeeStructure.is_active,
            FeeStructure.hostel_id.is_(None) | (FeeStructure.hostel_id == student.hostel_id),
        )
    ) or Decimal("0")
    required = Decimal(required)
    opens = ledger.open_debits(db, student.id)
    credit = sum((c.remaining for c in ledger.unallocated_credits(db, student.id)), Decimal("0"))
    return BalanceOut(
        student_id=student.id,
        dues=dues,
        deposit_held=deposit,
        deposit_required=required,
        deposit_due=max(required - deposit, Decimal("0")),
        unallocated_credit=credit,
        open_charges=[
            OpenChargeOut(
                entry_id=o.entry.id,
                entry_type=o.entry.entry_type,
                description=o.entry.description,
                due_date=o.entry.due_date,
                amount=o.entry.amount,
                outstanding=o.remaining,
            )
            for o in opens
        ],
    )


def ledger_for(db: Session, student_id: uuid.UUID, limit: int, before: datetime | None) -> list[EntryOut]:
    q = select(LedgerEntry).where(LedgerEntry.student_id == student_id)
    if before is not None:
        q = q.where(LedgerEntry.created_at < before)
    entries = db.scalars(q.order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc()).limit(limit)).all()
    ids = [e.id for e in entries]
    reversed_ids = set(db.scalars(select(LedgerEntry.reverses_entry_id).where(LedgerEntry.reverses_entry_id.in_(ids))))
    allocs = db.scalars(
        select(LedgerAllocation).where(
            LedgerAllocation.credit_entry_id.in_(ids) | LedgerAllocation.debit_entry_id.in_(ids)
        )
    ).all()
    out = []
    for e in entries:
        mine = [
            LedgerAllocationOut(
                credit_entry_id=a.credit_entry_id,
                debit_entry_id=a.debit_entry_id,
                amount=a.amount,
                method=a.method,
                released=a.released_at is not None,
            )
            for a in allocs
            if e.id in (a.credit_entry_id, a.debit_entry_id)
        ]
        out.append(
            EntryOut(
                id=e.id,
                account=e.account,
                entry_type=e.entry_type,
                amount=e.amount,
                effective_date=e.effective_date,
                description=e.description,
                due_date=e.due_date,
                payment_id=e.payment_id,
                reverses_entry_id=e.reverses_entry_id,
                reversed=e.id in reversed_ids,
                reason_code=e.reason_code,
                created_at=e.created_at,
                allocations=mine,
            )
        )
    return out


@router.get("/students/{student_id}/balance")
def student_balance(ctx: View, student_id: uuid.UUID) -> BalanceOut:
    return balance_for(ctx.db, students.get_student(ctx.db, ctx.principal, student_id, FINANCE_VIEW))


@router.get("/students/{student_id}/ledger")
def student_ledger(
    ctx: View, student_id: uuid.UUID, limit: int = 100, before: datetime | None = None
) -> list[EntryOut]:
    s = students.get_student(ctx.db, ctx.principal, student_id, FINANCE_VIEW)
    return ledger_for(ctx.db, s.id, max(1, min(limit, 200)), before)


class ChargeIn(Strict):
    kind: Literal["charge", "fine"]
    amount: MoneyIn
    description: str = Field(min_length=2, max_length=200)
    due_date: date
    reason_code: Literal["damage", "correction", "proration", "other"] | None = None
    reason_note: str | None = Field(default=None, min_length=3, max_length=500)


class WaiverIn(Strict):
    target_entry_id: uuid.UUID
    amount: MoneyIn
    reason_code: Literal["waiver", "discount", "scholarship", "proration", "correction", "other"]
    reason_note: str = Field(min_length=3, max_length=500)


class ReverseIn(Strict):
    reason: Reason


class DepositApplyIn(Strict):
    amount: MoneyIn


@router.post("/students/{student_id}/charges", status_code=status.HTTP_201_CREATED)
def add_charge(ctx: Manage, student_id: uuid.UUID, body: ChargeIn) -> EntryOut:
    if body.kind == "fine" and not (body.reason_code and body.reason_note):
        raise ProblemError(422, "validation_error", "A fine needs a reason code and a note (Q27)")
    e = service.add_charge(
        ctx.db,
        ctx.principal,
        student_id,
        kind=body.kind,
        amount=body.amount,
        description=body.description,
        due_date=body.due_date,
        reason_code=body.reason_code,
        reason_note=body.reason_note,
    )
    return ledger_for(ctx.db, e.student_id, 1, None)[0]


@router.post("/students/{student_id}/waivers", status_code=status.HTTP_201_CREATED)
def add_waiver(ctx: Manage, student_id: uuid.UUID, body: WaiverIn) -> EntryOut:
    e = service.add_waiver(
        ctx.db,
        ctx.principal,
        student_id,
        target_entry_id=body.target_entry_id,
        amount=body.amount,
        reason_code=body.reason_code,
        reason_note=body.reason_note,
    )
    return next(x for x in ledger_for(ctx.db, e.student_id, 20, None) if x.id == e.id)


@router.post("/ledger-entries/{entry_id}/reverse", status_code=status.HTTP_201_CREATED)
def reverse_entry(ctx: Manage, entry_id: uuid.UUID, body: ReverseIn) -> EntryOut:
    r = service.reverse_ledger_entry(ctx.db, ctx.principal, entry_id, body.reason)
    return next(x for x in ledger_for(ctx.db, r.student_id, 20, None) if x.id == r.id)


@router.post("/students/{student_id}/deposit/apply")
def apply_deposit(ctx: Manage, student_id: uuid.UUID, body: DepositApplyIn) -> BalanceOut:
    service.apply_deposit(ctx.db, ctx.principal, student_id, body.amount)
    return balance_for(ctx.db, ctx.db.get(Student, student_id))  # type: ignore[arg-type]


# --- payments ------------------------------------------------------------------------------------------------


class PaymentIn(Strict):
    student_id: uuid.UUID
    method: Literal["cash", "upi", "bank_transfer"]
    reference: str | None = Field(default=None, pattern=r"^[A-Za-z0-9/-]{4,40}$")
    received_on: date
    dues_amount: MoneyInOrZero = Decimal("0")
    deposit_amount: MoneyInOrZero = Decimal("0")
    notes: str | None = Field(default=None, max_length=300)


class PaymentOut(Out):
    id: uuid.UUID
    student_id: uuid.UUID
    hostel_id: uuid.UUID
    amount: MoneyOut
    method: str
    reference: str | None
    received_on: date
    receipt_number: str
    submission_id: uuid.UUID | None
    reversed: bool
    created_at: datetime


def _payment_out(db: Session, p: Payment) -> PaymentOut:
    return PaymentOut(
        id=p.id,
        student_id=p.student_id,
        hostel_id=p.hostel_id,
        amount=p.amount,
        method=p.method,
        reference=p.reference,
        received_on=p.received_on,
        receipt_number=p.receipt_number or "",
        submission_id=p.submission_id,
        reversed=db.get(PaymentReversal, p.id) is not None,
        created_at=p.created_at,
    )


@router.post("/payments", status_code=status.HTTP_201_CREATED)
def record_payment(
    ctx: Manage,
    body: PaymentIn,
    response: Response,
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
) -> PaymentOut:
    """`Idempotency-Key` is required (04 §10): a retry with the same key and body returns the original."""
    # The future-date check is in service.record_payment, so verified UPI submissions get it too.
    payment, created = service.record_payment(
        ctx.db,
        ctx.principal,
        service.PaymentRequest(
            body.student_id,
            body.method,
            body.reference,
            body.received_on,
            body.dues_amount,
            body.deposit_amount,
            body.notes,
        ),
        idempotency_key,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return _payment_out(ctx.db, payment)


def _payments_query(ctx: OrgContext, student_id: uuid.UUID | None, start: date | None, end: date | None):  # type: ignore[no-untyped-def]
    hostels = ctx.principal.hostels_for(FINANCE_VIEW)
    q = select(Payment)
    if hostels is not None:
        q = q.where(Payment.hostel_id.in_(hostels))
    if student_id is not None:
        q = q.where(Payment.student_id == student_id)
    if start is not None:
        q = q.where(Payment.received_on >= start)
    if end is not None:
        q = q.where(Payment.received_on <= end)
    return q.order_by(Payment.received_on.desc(), Payment.receipt_seq.desc())


@router.get("/payments")
def list_payments(
    ctx: View, student_id: uuid.UUID | None = None, start: date | None = None, end: date | None = None, limit: int = 100
) -> list[PaymentOut]:
    q = _payments_query(ctx, student_id, start, end).limit(max(1, min(limit, 500)))
    return [_payment_out(ctx.db, p) for p in ctx.db.scalars(q)]


@router.get("/payments/export.csv")
def export_payments(ctx: View, start: date | None = None, end: date | None = None) -> StreamingResponse:
    """The cash book (Q47): rate-limited and audited (04 §9)."""
    wait = EXPORTS_PER_HOUR.take(str(ctx.principal.user_id))
    if wait:
        raise ProblemError(
            429, "rate_limited", "Too many exports. Try again later.", headers={"Retry-After": str(wait)}
        )
    rows = ctx.db.execute(
        _payments_query(ctx, None, start, end)
        .with_only_columns(
            Payment.id,
            Payment.receipt_number,
            Payment.received_on,
            Payment.method,
            Payment.reference,
            Payment.amount,
            Student.student_code,
            User.full_name,
        )
        .join(Student, Student.id == Payment.student_id)
        .join(User, User.id == Student.user_id)
    ).all()
    reversed_ids = set(ctx.db.scalars(select(PaymentReversal.payment_id)))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["receipt_number", "received_on", "method", "reference", "amount", "reversed", "student_id", "student_name"]
    )
    for r in rows:
        writer.writerow(
            [
                r[1],
                r[2].isoformat(),
                r[3],
                r[4] or "",
                f"{r[5]:.2f}",
                "yes" if r[0] in reversed_ids else "no",
                r[6],
                _csv_safe(r[7]),
            ]
        )
    audit.record(
        ctx.db,
        ctx.principal,
        "payments.exported",
        "payment",
        None,
        after={"start": start, "end": end, "rows": len(rows)},
    )
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="payments.csv"'},
    )


def _csv_safe(value: str) -> str:
    """CSV/formula injection: a cell starting with = + - @ is shown as text in spreadsheets."""
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


@router.get("/payments/{payment_id}")
def get_payment(ctx: View, payment_id: uuid.UUID) -> PaymentOut:
    p = ctx.db.get(Payment, payment_id)
    if p is None:
        raise not_found()
    check_hostel(ctx.principal, FINANCE_VIEW, p.hostel_id)
    return _payment_out(ctx.db, p)


@router.get("/payments/{payment_id}/receipt.pdf")
def payment_receipt(ctx: View, payment_id: uuid.UUID) -> Response:
    p = ctx.db.get(Payment, payment_id)
    if p is None:
        raise not_found()
    check_hostel(ctx.principal, FINANCE_VIEW, p.hostel_id)
    return receipts.pdf_response(ctx.db, p)


@router.post("/payments/{payment_id}/reverse")
def reverse_payment(ctx: Manage, payment_id: uuid.UUID, body: ReverseIn) -> PaymentOut:
    return _payment_out(ctx.db, service.reverse_payment(ctx.db, ctx.principal, payment_id, body.reason))


# --- UPI submissions ------------------------------------------------------------------------------------------------


class SubmissionOut(Out):
    id: uuid.UUID
    student_id: uuid.UUID
    amount: MoneyOut
    utr: str
    paid_on: date
    proof_file_id: uuid.UUID | None
    status: str
    submitted_at: datetime
    review_note: str | None
    student_code: str | None = None  # filled in for the queue: whose payment it is (review Should fix 7)
    student_name: str | None = None


class ReviewIn(Strict):
    note: str | None = Field(default=None, min_length=3, max_length=500)


def _named[T: (SubmissionOut, RefundOut)](db: Session, items: list[T]) -> list[T]:
    """Add the student's code and name to queue items, in one query (RLS keeps it to this organization)."""
    ids = {i.student_id for i in items}
    who = {
        sid: (code, name)
        for sid, code, name in db.execute(
            select(Student.id, Student.student_code, User.full_name)
            .join(User, User.id == Student.user_id)
            .where(Student.id.in_(ids))
        )
    }
    for i in items:
        i.student_code, i.student_name = who.get(i.student_id, (None, None))
    return items


@router.get("/payment-submissions")
def list_submissions(
    ctx: Manage,
    status: Literal["pending", "verified", "rejected"] = "pending",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,  # 05 WEB-6 (review Minor 13)
) -> list[SubmissionOut]:
    hostels = ctx.principal.hostels_for(FINANCE_MANAGE)
    q = select(PaymentSubmission).where(PaymentSubmission.status == status)
    if hostels is not None:
        q = q.where(PaymentSubmission.hostel_id.in_(hostels))
    rows = ctx.db.scalars(q.order_by(PaymentSubmission.submitted_at).limit(limit))
    return _named(ctx.db, [SubmissionOut.model_validate(x) for x in rows])


@router.post("/payment-submissions/{submission_id}/verify")
def verify_submission(ctx: Manage, submission_id: uuid.UUID) -> SubmissionOut:
    return SubmissionOut.model_validate(service.review_submission(ctx.db, ctx.principal, submission_id, True, None))


@router.post("/payment-submissions/{submission_id}/reject")
def reject_submission(ctx: Manage, submission_id: uuid.UUID, body: ReviewIn) -> SubmissionOut:
    return SubmissionOut.model_validate(
        service.review_submission(ctx.db, ctx.principal, submission_id, False, body.note)
    )


# --- refunds (Q30) --------------------------------------------------------------------------------------------------


class RefundIn(Strict):
    student_id: uuid.UUID
    account: Literal["dues", "deposit"]
    amount: MoneyIn
    reason: Reason


class DecisionIn(Strict):
    note: str | None = Field(default=None, min_length=3, max_length=500)


class PayRefundIn(Strict):
    method: Literal["cash", "upi", "bank_transfer"]
    reference: str | None = Field(default=None, max_length=40)


class RefundOut(Out):
    id: uuid.UUID
    student_id: uuid.UUID
    account: str
    amount: MoneyOut
    reason: str
    status: str
    requested_by: uuid.UUID
    decided_by: uuid.UUID | None
    decision_note: str | None
    self_approved: bool = False  # approved by the requester as the sole Administrator (D75)
    paid_method: str | None
    paid_at: datetime | None
    student_code: str | None = None
    student_name: str | None = None


@router.get("/refund-requests")
def list_refunds(
    ctx: View,
    status: Literal["requested", "approved", "rejected", "paid", "cancelled"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,  # 05 WEB-6 (review Minor 13)
) -> list[RefundOut]:
    hostels = ctx.principal.hostels_for(FINANCE_VIEW)
    q = select(RefundRequest)
    if hostels is not None:
        q = q.where(RefundRequest.hostel_id.in_(hostels))
    if status:
        q = q.where(RefundRequest.status == status)
    rows = ctx.db.scalars(q.order_by(RefundRequest.requested_at.desc()).limit(limit))
    return _named(ctx.db, [RefundOut.model_validate(r) for r in rows])


@router.post("/refund-requests", status_code=status.HTTP_201_CREATED)
def create_refund(ctx: Manage, body: RefundIn) -> RefundOut:
    return RefundOut.model_validate(
        service.request_refund(ctx.db, ctx.principal, body.student_id, body.account, body.amount, body.reason)
    )


@router.post("/refund-requests/{refund_id}/cancel")
def cancel_refund(ctx: Manage, refund_id: uuid.UUID) -> RefundOut:
    return _named(ctx.db, [RefundOut.model_validate(service.cancel_refund(ctx.db, ctx.principal, refund_id))])[0]


@router.post("/refund-requests/{refund_id}/approve")
def approve_refund(ctx: Approve, refund_id: uuid.UUID, body: DecisionIn) -> RefundOut:
    return RefundOut.model_validate(service.decide_refund(ctx.db, ctx.principal, refund_id, True, body.note))


@router.post("/refund-requests/{refund_id}/reject")
def reject_refund(ctx: Approve, refund_id: uuid.UUID, body: DecisionIn) -> RefundOut:
    if not body.note:
        raise ProblemError(422, "validation_error", "Say why the refund is rejected")
    return RefundOut.model_validate(service.decide_refund(ctx.db, ctx.principal, refund_id, False, body.note))


@router.post("/refund-requests/{refund_id}/mark-paid")
def pay_refund(ctx: Manage, refund_id: uuid.UUID, body: PayRefundIn) -> RefundOut:
    return RefundOut.model_validate(service.pay_refund(ctx.db, ctx.principal, refund_id, body.method, body.reference))


# --- the student's fees (portal) ------------------------------------------------------------------------------------


class UpiOut(Out):
    upi_id: str
    payee_name: str
    amount: MoneyOut
    link: str  # upi://pay?... (Q28); the portal draws it as a QR code


class PortalFeesOut(Out):
    balance: BalanceOut
    entries: list[EntryOut]
    payments: list[PaymentOut]
    submissions: list[SubmissionOut]
    upi: UpiOut | None


class SubmissionIn(Strict):
    amount: MoneyIn
    utr: str = Field(pattern=r"^[A-Za-z0-9]{6,35}$")
    paid_on: date
    proof_file_id: uuid.UUID | None = None


def _me_student(ctx: Ctx) -> Student:
    s = ctx.db.scalars(select(Student).where(Student.user_id == ctx.principal.user_id)).first()
    if s is None:
        raise not_found()
    return s


@portal.get("/fees")
def my_fees(ctx: Ctx) -> PortalFeesOut:
    s = _me_student(ctx)
    balance = balance_for(ctx.db, s)
    profile = ctx.db.get(OrganizationProfile, s.org_id)
    upi = None
    if profile and profile.upi_id and profile.upi_payee_name and balance.dues > 0:
        from urllib.parse import quote

        link = (
            f"upi://pay?pa={quote(profile.upi_id)}&pn={quote(profile.upi_payee_name)}&am={balance.dues:.2f}"
            f"&cu=INR&tn={quote(s.student_code)}"
        )
        upi = UpiOut(upi_id=profile.upi_id, payee_name=profile.upi_payee_name, amount=balance.dues, link=link)
    pays = ctx.db.scalars(select(Payment).where(Payment.student_id == s.id).order_by(Payment.received_on.desc()))
    subs = ctx.db.scalars(
        select(PaymentSubmission)
        .where(PaymentSubmission.student_id == s.id)
        .order_by(PaymentSubmission.submitted_at.desc())
    )
    return PortalFeesOut(
        balance=balance,
        entries=ledger_for(ctx.db, s.id, 100, None),
        payments=[_payment_out(ctx.db, p) for p in pays],
        submissions=[SubmissionOut.model_validate(x) for x in subs],
        upi=upi,
    )


@portal.post("/payment-submissions", status_code=status.HTTP_201_CREATED)
def submit_payment(ctx: Ctx, body: SubmissionIn) -> SubmissionOut:
    s = _me_student(ctx)
    ledger.lock_student(ctx.db, s.id)
    return SubmissionOut.model_validate(
        service.submit_payment(ctx.db, ctx.principal, s, body.amount, body.utr, body.paid_on, body.proof_file_id)
    )


@portal.get("/payments/{payment_id}/receipt.pdf")
def my_receipt(ctx: Ctx, payment_id: uuid.UUID) -> Response:
    s = _me_student(ctx)
    p = ctx.db.get(Payment, payment_id)
    if p is None or p.student_id != s.id:
        raise not_found()
    return receipts.pdf_response(ctx.db, p)
