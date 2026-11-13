"""PDF receipts (Q29, Q47, Q75): rendered from ledger data on demand with WeasyPrint, never stored.

The HTML template is auto-escaped (Jinja2), so a name or reference can't inject markup, and
WeasyPrint gets no base URL, so the template can't load anything from the network.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import Response
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session
from weasyprint import HTML  # type: ignore[import-untyped]

from app.modules.finance.models import LedgerEntry, Payment, PaymentReversal
from app.modules.identity.models import User
from app.modules.organizations.models import Organization, OrganizationProfile
from app.modules.students.models import Student

TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "pdf"


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(enabled_extensions=("html",)),
        undefined=StrictUndefined,
    )


def render(db: Session, payment: Payment) -> bytes:
    student = db.get(Student, payment.student_id)
    assert student is not None
    user = db.get(User, student.user_id)
    org = db.get(Organization, payment.org_id)
    profile = db.get(OrganizationProfile, payment.org_id)
    entries = db.scalars(select(LedgerEntry).where(LedgerEntry.payment_id == payment.id)).all()
    html = (
        _env()
        .get_template("receipt.html")
        .render(
            org=org,
            profile=profile,
            payment=payment,
            student=student,
            name=user.full_name if user else "",
            dues=sum((-e.amount for e in entries if e.entry_type == "payment"), 0),
            deposit=sum((e.amount for e in entries if e.entry_type == "deposit_received"), 0),
            reversal=db.get(PaymentReversal, payment.id),
        )
    )
    return bytes(HTML(string=html).write_pdf())


def pdf_response(db: Session, payment: Payment) -> Response:
    return Response(
        content=render(db, payment),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(payment)}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _filename(payment: Payment) -> str:
    return (payment.receipt_number or "receipt").replace("/", "-") + ".pdf"
