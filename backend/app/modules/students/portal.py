"""The student's own profile in the portal (docs/04 §12.18, Q46).

Every route is "me": the student is the one in the token; no student ID is ever in the URL
(05 RBAC-3). Students may change their phone number (PATCH /me) and their emergency contact;
identity fields are admin-only (Q46). Documents are listed by type, masked number and
verification status only; students can't open identity files (Q62).
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core import audit
from app.core.context import Ctx, student_only
from app.core.crud import not_found
from app.core.schemas import Strict
from app.modules.identity.models import User
from app.modules.students.models import Student, StudentDocument, StudentGuardian
from app.modules.students.router import GuardianOut, Phone, Relationship, _clear_primary

router = APIRouter(prefix="/portal/me", tags=["portal"], dependencies=[Depends(student_only)])


class PortalDocument(BaseModel):
    document_type: str
    id_type: str | None
    id_number_masked: str | None
    verified: bool


class PortalProfile(BaseModel):
    student_code: str
    full_name: str
    email: str | None
    phone: str | None
    hostel_id: uuid.UUID
    status: str
    gender: str
    date_of_birth: date
    admission_date: date
    program: str | None
    year_of_study: int | None
    photo_file_id: uuid.UUID
    read_only: bool
    guardians: list[GuardianOut]
    documents: list[PortalDocument]


class EmergencyContactIn(Strict):
    full_name: str = Field(min_length=1, max_length=120)
    relationship: Relationship
    phone: Phone


def _me(ctx: Ctx) -> Student:
    s = ctx.db.scalars(select(Student).where(Student.user_id == ctx.principal.user_id)).first()
    if s is None:
        raise not_found()
    return s


def _profile(ctx: Ctx, s: Student) -> PortalProfile:
    user = ctx.db.get(User, s.user_id)
    assert user is not None
    guardians = ctx.db.scalars(
        select(StudentGuardian)
        .where(StudentGuardian.student_id == s.id)
        .order_by(StudentGuardian.is_primary.desc(), StudentGuardian.created_at)
    ).all()
    docs = ctx.db.scalars(select(StudentDocument).where(StudentDocument.student_id == s.id)).all()
    return PortalProfile(
        student_code=s.student_code,
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        hostel_id=s.hostel_id,
        status=s.status,
        gender=s.gender,
        date_of_birth=s.date_of_birth,
        admission_date=s.admission_date,
        program=s.program,
        year_of_study=s.year_of_study,
        photo_file_id=s.photo_file_id,
        read_only=ctx.principal.read_only,
        guardians=[GuardianOut.model_validate(g) for g in guardians],
        documents=[
            PortalDocument(
                document_type=d.document_type,
                id_type=d.id_type,
                id_number_masked=d.id_number_masked,
                verified=d.verified_at is not None,
            )
            for d in docs
        ],
    )


@router.get("/profile")
def my_profile(ctx: Ctx) -> PortalProfile:
    return _profile(ctx, _me(ctx))


@router.post("/emergency-contact")
def set_emergency_contact(ctx: Ctx, body: EmergencyContactIn) -> PortalProfile:
    """Q46: the student sets their primary emergency contact (created or updated)."""
    s = _me(ctx)
    primary = ctx.db.scalars(
        select(StudentGuardian).where(StudentGuardian.student_id == s.id, StudentGuardian.is_primary).with_for_update()
    ).first()
    if primary is None:
        _clear_primary(ctx.db, s.id)
        ctx.db.add(StudentGuardian(org_id=s.org_id, student_id=s.id, is_primary=True, **body.model_dump()))
    else:
        for key, value in body.model_dump().items():
            setattr(primary, key, value)
    ctx.db.flush()
    audit.record(ctx.db, ctx.principal, "student.emergency_contact_updated", "student", s.id, hostel_id=s.hostel_id)
    return _profile(ctx, s)
