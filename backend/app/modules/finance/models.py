"""Fees, payments and the ledger (docs/03 §5.8). Money is numeric(12,2) / Decimal, never float."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Computed, Date, Integer, Numeric, SmallInteger, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import APP_NOW, created_at, tz, updated_at, uuid_pk, version_column

Money = Numeric(12, 2)


class FeeStructure(Base):
    __tablename__ = "fee_structures"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    academic_session_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    room_type_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(Text)
    fee_kind: Mapped[str] = mapped_column(Text)  # rent | deposit | other
    amount: Mapped[Decimal] = mapped_column(Money)
    billing_cycle: Mapped[str] = mapped_column(Text)  # one_time | monthly | term | annual (Q24)
    due_days: Mapped[int] = mapped_column(SmallInteger, server_default="10")
    grace_days: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    late_fee_type: Mapped[str] = mapped_column(Text, server_default="none")  # none | flat | per_day (Q26)
    late_fee_amount: Mapped[Decimal | None] = mapped_column(Money)
    late_fee_cap: Mapped[Decimal | None] = mapped_column(Money)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __mapper_args__ = {"version_id_col": version}


class PaymentSubmission(Base):
    __tablename__ = "payment_submissions"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    amount: Mapped[Decimal] = mapped_column(Money)
    utr: Mapped[str] = mapped_column(Text)
    paid_on: Mapped[date] = mapped_column(Date)
    proof_file_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    submitted_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    submitted_at: Mapped[datetime] = tz(nullable=False, server_default=APP_NOW)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reviewed_at: Mapped[datetime | None] = tz()
    review_note: Mapped[str | None] = mapped_column(Text)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    amount: Mapped[Decimal] = mapped_column(Money)
    method: Mapped[str] = mapped_column(Text)  # cash | upi | bank_transfer
    reference: Mapped[str | None] = mapped_column(Text)
    received_on: Mapped[date] = mapped_column(Date)
    financial_year: Mapped[str] = mapped_column(Text)
    receipt_seq: Mapped[int] = mapped_column(Integer)
    receipt_number: Mapped[str | None] = mapped_column(
        Text, Computed("('RCPT/' || financial_year || '/' || lpad(receipt_seq::text, 6, '0'))"), nullable=True
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid)
    submission_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recorded_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class PaymentReversal(Base):
    __tablename__ = "payment_reversals"

    payment_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    reason: Mapped[str] = mapped_column(Text)
    reversed_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    reversed_at: Mapped[datetime] = tz(nullable=False, server_default=APP_NOW)


class RefundRequest(Base):
    __tablename__ = "refund_requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    account: Mapped[str] = mapped_column(Text)  # dues | deposit
    amount: Mapped[Decimal] = mapped_column(Money)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="requested")
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    requested_at: Mapped[datetime] = tz(nullable=False, server_default=APP_NOW)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = tz()
    decision_note: Mapped[str | None] = mapped_column(Text)
    self_approved: Mapped[bool] = mapped_column(Boolean, server_default="false")  # sole Administrator (D75)
    paid_method: Mapped[str | None] = mapped_column(Text)
    paid_reference: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None] = tz()


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    txn_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    account: Mapped[str] = mapped_column(Text)  # dues | deposit
    entry_type: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Money)  # signed: + increases the balance
    effective_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text)
    fee_structure_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    academic_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    source_entry_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    late_fee_date: Mapped[date | None] = mapped_column(Date)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    refund_request_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reverses_entry_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reason_code: Mapped[str | None] = mapped_column(Text)
    reason_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = created_at()


class LedgerAllocation(Base):
    __tablename__ = "ledger_allocations"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    credit_entry_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    debit_entry_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    amount: Mapped[Decimal] = mapped_column(Money)
    method: Mapped[str] = mapped_column(Text)  # auto_oldest_first | manual
    allocated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    allocated_at: Mapped[datetime] = tz(nullable=False, server_default=APP_NOW)
    released_at: Mapped[datetime | None] = tz()
    released_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    release_reason: Mapped[str | None] = mapped_column(Text)
