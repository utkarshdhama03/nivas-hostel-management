"""Students in the admin area (docs/04 §12.7)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import EmailStr, Field
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import Session

from app.core import audit, jobs
from app.core.context import OrgContext, require, staff_only
from app.core.crud import check_hostel, check_version, not_found
from app.core.errors import ProblemError
from app.core.permissions import STUDENT_CONTACTS_VIEW, STUDENT_DOCUMENTS, STUDENTS_MANAGE, STUDENTS_VIEW
from app.core.schemas import Out, Page, PageInfo, PageNumber, PageSize, Strict, Versioned
from app.modules.files import service as files
from app.modules.files.models import File
from app.modules.finance import ledger
from app.modules.identity import sessions
from app.modules.identity.models import User, UserSession
from app.modules.students import service
from app.modules.students.models import Student, StudentDocument, StudentGuardian

router = APIRouter(prefix="/admin", tags=["students"], dependencies=[Depends(staff_only)])
View = Annotated[OrgContext, Depends(require(STUDENTS_VIEW))]
Manage = Annotated[OrgContext, Depends(require(STUDENTS_MANAGE))]
Documents = Annotated[OrgContext, Depends(require(STUDENT_DOCUMENTS))]

Phone = Annotated[str, Field(pattern=r"^\+?[0-9][0-9 -]{6,19}$")]
Gender = Literal["male", "female", "other"]
Relationship = Literal["father", "mother", "guardian", "sibling", "spouse", "other"]


# --- response models: one per view (RBAC-8) ------------------------------------------------------


class StudentListItem(Out):
    id: uuid.UUID
    student_code: str
    full_name: str
    hostel_id: uuid.UUID
    status: str
    photo_file_id: uuid.UUID


class GuardianOut(Out):
    id: uuid.UUID
    full_name: str
    relationship: str
    phone: str
    alt_phone: str | None
    email: str | None
    is_primary: bool
    version: int


class StudentFullOut(StudentListItem):
    """The student record. Since D75 the Administrator is the only staff account and always gets all of it
    (the smaller per-role views were deleted on 12 November); students see their own data through /portal."""

    user_id: uuid.UUID
    email: str | None
    phone: str | None
    gender: str
    admission_session_id: uuid.UUID
    admission_date: date
    institution_roll_no: str | None
    program: str | None
    year_of_study: int | None
    exited_on: date | None
    exit_reason: str | None
    user_status: str
    version: int
    date_of_birth: date
    address_line1: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    guardians: list[GuardianOut]


def _student_out(db: Session, s: Student) -> StudentFullOut:
    user = db.get(User, s.user_id)
    assert user is not None
    base = {
        "id": s.id,
        "student_code": s.student_code,
        "full_name": user.full_name,
        "hostel_id": s.hostel_id,
        "status": s.status,
        "photo_file_id": s.photo_file_id,
    }
    finance = {
        **base,
        "user_id": user.id,
        "email": user.email,
        "phone": user.phone,
        "gender": s.gender,
        "admission_session_id": s.admission_session_id,
        "admission_date": s.admission_date,
        "institution_roll_no": s.institution_roll_no,
        "program": s.program,
        "year_of_study": s.year_of_study,
        "exited_on": s.exited_on,
        "exit_reason": s.exit_reason,
        "user_status": user.status,
        "version": s.version,
    }
    guardians = db.scalars(
        select(StudentGuardian)
        .where(StudentGuardian.student_id == s.id)
        .order_by(StudentGuardian.is_primary.desc(), StudentGuardian.created_at)
    ).all()
    return StudentFullOut.model_validate(
        {
            **finance,
            "date_of_birth": s.date_of_birth,
            "address_line1": s.address_line1,
            "city": s.city,
            "state": s.state,
            "postal_code": s.postal_code,
            "guardians": [GuardianOut.model_validate(g) for g in guardians],
        }
    )


# --- requests ---------------------------------------------------------------------------------------------


class GuardianIn(Strict):
    full_name: str = Field(min_length=1, max_length=120)
    relationship: Relationship
    phone: Phone
    alt_phone: Phone | None = None
    email: EmailStr | None = None
    is_primary: bool = False


class GuardianPatch(Versioned):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    relationship: Relationship | None = None
    phone: Phone | None = None
    alt_phone: Phone | None = None
    email: EmailStr | None = None
    is_primary: bool | None = None


class AdmissionIn(Strict):
    full_name: str = Field(min_length=1, max_length=120)
    email: EmailStr  # D80: required; the student signs in with it, unique across the platform
    phone: Phone | None = None
    hostel_id: uuid.UUID
    admission_session_id: uuid.UUID | None = None  # default: the current session
    gender: Gender
    date_of_birth: date
    photo_file_id: uuid.UUID  # mandatory (Q39)
    admission_date: date
    institution_roll_no: str | None = Field(default=None, max_length=40)
    program: str | None = Field(default=None, max_length=80)
    year_of_study: int | None = Field(default=None, ge=1, le=10)
    address_line1: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    guardians: list[GuardianIn] = Field(default_factory=list, max_length=5)
    send_activation_email: bool = True


class StudentPatch(Versioned):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    email: EmailStr | None = None
    phone: Phone | None = None
    gender: Gender | None = None
    date_of_birth: date | None = None
    photo_file_id: uuid.UUID | None = None
    institution_roll_no: str | None = Field(default=None, max_length=40)
    program: str | None = Field(default=None, max_length=80)
    year_of_study: int | None = Field(default=None, ge=1, le=10)
    address_line1: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")


class DocumentIn(Strict):
    file_id: uuid.UUID
    document_type: Literal["identity", "admission", "other"]
    id_type: Literal["aadhaar", "pan", "passport", "voter_id", "driving_licence", "college_id", "other"] | None = None
    id_number_masked: str | None = Field(default=None, pattern=r"^X{4,16}[0-9A-Z]{4}$")
    masked_aadhaar_attested: bool = False


class DocumentOut(Out):
    id: uuid.UUID
    document_type: str
    id_type: str | None
    id_number_masked: str | None
    file_id: uuid.UUID
    file_mime_type: str
    verified: bool
    verified_at: datetime | None
    created_at: datetime


class ExitIn(Strict):
    exited_on: date
    exit_reason: Literal["graduated", "withdrawn", "expelled", "transferred_out", "other"]
    # Q93: required only when dues or a held deposit are outstanding; recorded in the audit log.
    outstanding_note: str | None = Field(default=None, min_length=5, max_length=500)


USER_FIELDS = {"full_name", "email", "phone"}


def _doc_out(d: StudentDocument) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        document_type=d.document_type,
        id_type=d.id_type,
        id_number_masked=d.id_number_masked,
        file_id=d.file_id,
        file_mime_type=d.file_mime_type,
        verified=d.verified_at is not None,
        verified_at=d.verified_at,
        created_at=d.created_at,
    )


# --- students -------------------------------------------------------------------------------------------


@router.get("/students")
def list_students(
    ctx: View,
    page: PageNumber = 1,
    page_size: PageSize = 25,
    q: str | None = None,
    hostel_id: uuid.UUID | None = None,
    status: Literal["active", "exited"] | None = None,
    without_bed: bool = False,  # the bed map's student picker: only those with no current stay or reservation
) -> Page[StudentListItem]:
    hostels = ctx.principal.hostels_for(STUDENTS_VIEW)
    query = select(Student, User.full_name).join(User, User.id == Student.user_id)
    if hostels is not None:
        query = query.where(Student.hostel_id.in_(hostels))
    if hostel_id is not None:
        if hostels is not None and hostel_id not in hostels:
            raise not_found()
        query = query.where(Student.hostel_id == hostel_id)
    if status is not None:
        query = query.where(Student.status == status)
    if without_bed:
        query = query.where(
            text(
                "NOT EXISTS (SELECT 1 FROM bed_allocations a WHERE a.student_id = students.id "
                "AND a.status IN ('active', 'reserved'))"
            )
        )
    if q:
        if not 2 <= len(q) <= 50:
            raise ProblemError(422, "validation_error", "Search needs 2 to 50 characters")
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(or_(User.full_name.ilike(like), Student.student_code.ilike(like)))
    total = ctx.db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = ctx.db.execute(
        query.order_by(Student.student_code, Student.id).offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [
        StudentListItem(
            id=s.id,
            student_code=s.student_code,
            full_name=name,
            hostel_id=s.hostel_id,
            status=s.status,
            photo_file_id=s.photo_file_id,
        )
        for s, name in rows
    ]
    return Page[StudentListItem](
        items=items,
        page=PageInfo(number=page, size=page_size, total_items=total, total_pages=max(1, -(-total // page_size))),
    )


@router.post("/students", status_code=status.HTTP_201_CREATED)
def admit_student(ctx: Manage, body: AdmissionIn) -> StudentFullOut:
    p = ctx.principal
    check_hostel(p, STUDENTS_MANAGE, body.hostel_id)
    service.check_gender_fits_hostel(ctx.db, body.hostel_id, body.gender)
    if body.admission_session_id is None:
        session = service.current_session(ctx.db)
    else:
        from app.modules.organizations.models import AcademicSession

        found = ctx.db.get(AcademicSession, body.admission_session_id)
        if found is None:
            raise not_found()
        session = found
    service.claim_photo(ctx.db, p, body.photo_file_id)
    service.check_student_email_free(ctx.db, str(body.email))
    if sum(g.is_primary for g in body.guardians) > 1:
        raise ProblemError(422, "validation_error", "Only one primary emergency contact")
    fields = body.model_dump(
        exclude={"full_name", "email", "phone", "guardians", "send_activation_email", "admission_session_id"}
    )
    user, s = service.admit(  # the same code admits an approved registration request (D74)
        ctx.db,
        p,
        session=session,
        full_name=body.full_name,
        email=body.email,
        phone=body.phone,
        student_fields=fields,
        guardians=[g.model_dump() for g in body.guardians],
    )
    if body.send_activation_email:
        jobs.enqueue(
            ctx.db, "email.activation", {"user_id": str(user.id)}, org_id=p.org_id, priority=jobs.PRIORITY_SECURITY
        )
    ctx.db.refresh(s)
    return _student_out(ctx.db, s)


@router.get("/students/{student_id}")
def get_student(ctx: View, student_id: uuid.UUID) -> StudentFullOut:
    return _student_out(ctx.db, service.get_student(ctx.db, ctx.principal, student_id))


@router.patch("/students/{student_id}")
def patch_student(ctx: Manage, student_id: uuid.UUID, body: StudentPatch) -> StudentFullOut:
    s = service.get_student(ctx.db, ctx.principal, student_id, STUDENTS_MANAGE, lock=True)
    check_version(s, body.version)
    user = ctx.db.get(User, s.user_id)
    assert user is not None
    sent = body.model_dump(exclude_unset=True, exclude={"version"})
    if "gender" in sent:
        service.check_gender_fits_hostel(ctx.db, s.hostel_id, sent["gender"])
    if sent.get("photo_file_id") and sent["photo_file_id"] != s.photo_file_id:
        service.claim_photo(ctx.db, ctx.principal, sent["photo_file_id"])
    before = {k: getattr(user if k in USER_FIELDS else s, k) for k in sent}
    for key, value in sent.items():
        setattr(user if key in USER_FIELDS else s, key, value)
    s.updated_at = func.app_now()  # one version for the whole student record (D16)
    ctx.db.flush()
    audit.record(
        ctx.db, ctx.principal, "student.updated", "student", s.id, hostel_id=s.hostel_id, before=before, after=sent
    )
    ctx.db.refresh(s)
    return _student_out(ctx.db, s)


@router.post("/students/{student_id}/exit")
def exit_student(ctx: Manage, student_id: uuid.UUID, body: ExitIn) -> StudentFullOut:
    """Q22: exit after check-out. The portal stays read-only for 90 days (receipts), then a nightly
    job deactivates the account. Settlement is done on the Fees card (apply deposit, refund request).
    Q93: money never blocks an exit, but with dues or a held deposit the first attempt answers 409
    with the amounts, and the exit needs a reason, which is audited. The dues stay on the account."""
    s = service.get_student(ctx.db, ctx.principal, student_id, STUDENTS_MANAGE, lock=True)
    if s.status == "exited":
        raise ProblemError(409, "already_exited", "This student has already exited")
    if service.has_open_allocation(ctx.db, s.id):
        raise ProblemError(409, "allocation_open", "Check the student out of their bed first")
    dues, deposit = ledger.balances(ctx.db, s.id)
    outstanding = dues != 0 or deposit != 0
    if outstanding and not body.outstanding_note:
        raise ProblemError(
            409,
            "outstanding_balance",
            "This student still has money outstanding",
            detail=f"Dues ₹{dues:,.2f}, deposit held ₹{deposit:,.2f}. Settle them on the Fees card, or give a "
            "reason to record the exit anyway; the dues stay on the student's account.",
            extra={"dues": f"{dues:.2f}", "deposit_held": f"{deposit:.2f}"},
        )
    user = ctx.db.get(User, s.user_id)
    assert user is not None
    s.status, s.exited_on, s.exit_reason = "exited", body.exited_on, body.exit_reason
    new_status = "read_only" if user.status == "active" else "deactivated"
    ctx.db.execute(
        update(User)
        .where(User.id == user.id)
        .values(
            status=new_status,
            read_only_until=service.read_only_until(body.exited_on) if new_status == "read_only" else None,
            updated_at=func.now(),
        )
    )
    if new_status == "deactivated":
        sessions.revoke(ctx.db, "deactivated", UserSession.user_id == user.id)
    ctx.db.flush()
    audit.record(
        ctx.db,
        ctx.principal,
        "student.exited",
        "student",
        s.id,
        hostel_id=s.hostel_id,
        after={
            "exited_on": body.exited_on,
            "exit_reason": body.exit_reason,
            "status": "exited",
            **(
                {"outstanding_dues": dues, "deposit_held": deposit, "outstanding_note": body.outstanding_note}
                if outstanding
                else {}
            ),
        },
    )
    ctx.db.refresh(s)
    return _student_out(ctx.db, s)


# --- guardians (emergency contacts) ------------------------------------------------------------------


def _clear_primary(db: Session, student_id: uuid.UUID, keep: uuid.UUID | None = None) -> None:
    q = update(StudentGuardian).where(StudentGuardian.student_id == student_id, StudentGuardian.is_primary)
    if keep is not None:
        q = q.where(StudentGuardian.id != keep)
    db.execute(q.values(is_primary=False, version=StudentGuardian.version + 1))


@router.get("/students/{student_id}/guardians")
def list_guardians(
    ctx: Annotated[OrgContext, Depends(require(STUDENT_CONTACTS_VIEW))], student_id: uuid.UUID
) -> list[GuardianOut]:
    s = service.get_student(ctx.db, ctx.principal, student_id, STUDENT_CONTACTS_VIEW)
    rows = ctx.db.scalars(
        select(StudentGuardian)
        .where(StudentGuardian.student_id == s.id)
        .order_by(StudentGuardian.is_primary.desc(), StudentGuardian.created_at)
    )
    return [GuardianOut.model_validate(g) for g in rows]


@router.post("/students/{student_id}/guardians", status_code=status.HTTP_201_CREATED)
def add_guardian(ctx: Manage, student_id: uuid.UUID, body: GuardianIn) -> GuardianOut:
    s = service.get_student(ctx.db, ctx.principal, student_id, STUDENTS_MANAGE, lock=True)
    if body.is_primary:
        _clear_primary(ctx.db, s.id)
    g = StudentGuardian(org_id=s.org_id, student_id=s.id, **body.model_dump())
    ctx.db.add(g)
    ctx.db.flush()
    audit.record(ctx.db, ctx.principal, "student.guardian_added", "student", s.id, hostel_id=s.hostel_id)
    ctx.db.refresh(g)
    return GuardianOut.model_validate(g)


@router.patch("/students/{student_id}/guardians/{guardian_id}")
def patch_guardian(ctx: Manage, student_id: uuid.UUID, guardian_id: uuid.UUID, body: GuardianPatch) -> GuardianOut:
    s = service.get_student(ctx.db, ctx.principal, student_id, STUDENTS_MANAGE, lock=True)
    g = ctx.db.scalars(
        select(StudentGuardian)
        .where(StudentGuardian.id == guardian_id, StudentGuardian.student_id == s.id)
        .with_for_update()
    ).first()
    if g is None:
        raise not_found()
    check_version(g, body.version)
    if body.is_primary:
        _clear_primary(ctx.db, s.id, keep=g.id)
    for key, value in body.model_dump(exclude_unset=True, exclude={"version"}).items():
        setattr(g, key, value)
    g.updated_at = func.app_now()
    ctx.db.flush()
    audit.record(ctx.db, ctx.principal, "student.guardian_updated", "student", s.id, hostel_id=s.hostel_id)
    ctx.db.refresh(g)
    return GuardianOut.model_validate(g)


# --- documents (Q44, Q62) ---------------------------------------------------------------------------------


@router.get("/students/{student_id}/documents")
def list_documents(ctx: Manage, student_id: uuid.UUID) -> list[DocumentOut]:
    s = service.get_student(ctx.db, ctx.principal, student_id, STUDENTS_MANAGE)
    rows = ctx.db.scalars(
        select(StudentDocument).where(StudentDocument.student_id == s.id).order_by(StudentDocument.created_at)
    )
    return [_doc_out(d) for d in rows]


@router.post("/students/{student_id}/documents", status_code=status.HTTP_201_CREATED)
def add_document(ctx: Manage, student_id: uuid.UUID, body: DocumentIn) -> DocumentOut:
    s = service.get_student(ctx.db, ctx.principal, student_id, STUDENTS_MANAGE, lock=True)
    f = ctx.db.scalars(
        select(File).where(File.id == body.file_id, File.deleted_at.is_(None), files.unattached_clause())
    ).first()
    expected_category = "identity_document" if body.document_type == "identity" else "admission_document"
    errors = []
    if f is None or f.category != expected_category:
        errors.append(
            {
                "field": "file_id",
                "code": "invalid_file",
                "message": f"Upload the file in the {expected_category.replace('_', ' ')} category first",
            }
        )
    if (body.document_type == "identity") != (body.id_type is not None):
        errors.append(
            {
                "field": "id_type",
                "code": "id_type_required",
                "message": "Identity documents need an ID type; other documents must not have one",
            }
        )
    if body.id_type == "aadhaar":
        # Q62: only the masked Aadhaar PDF from UIDAI, with the masked number and the uploader's attestation.
        if f is not None and f.mime_type != "application/pdf":
            errors.append(
                {
                    "field": "file_id",
                    "code": "aadhaar_pdf_required",
                    "message": "Upload the masked Aadhaar PDF downloaded from UIDAI, not a scan or photo",
                }
            )
        if not body.id_number_masked:
            errors.append(
                {
                    "field": "id_number_masked",
                    "code": "masked_number_required",
                    "message": "Enter the masked number, e.g. XXXXXXXX1234",
                }
            )
        if not body.masked_aadhaar_attested:
            errors.append(
                {
                    "field": "masked_aadhaar_attested",
                    "code": "attestation_required",
                    "message": "Confirm this is the masked Aadhaar PDF from UIDAI",
                }
            )
    if errors:
        raise ProblemError(422, "validation_error", "Invalid request", errors=errors)
    assert f is not None
    attested = body.masked_aadhaar_attested and body.id_type == "aadhaar"
    d = StudentDocument(
        org_id=s.org_id,
        student_id=s.id,
        file_id=f.id,
        file_category=f.category,
        file_mime_type=f.mime_type,
        document_type=body.document_type,
        id_type=body.id_type,
        id_number_masked=body.id_number_masked,
        uploaded_by=ctx.principal.user_id,
        masked_attested_by=ctx.principal.user_id if attested else None,
        masked_attested_at=datetime.now(UTC) if attested else None,
    )
    ctx.db.add(d)
    ctx.db.flush()
    audit.record(
        ctx.db,
        ctx.principal,
        "student_document.added",
        "student",
        s.id,
        hostel_id=s.hostel_id,
        after={"document_type": body.document_type},
    )
    ctx.db.refresh(d)
    return _doc_out(d)


@router.get("/students/{student_id}/documents/{document_id}/content")
def document_content(ctx: Documents, student_id: uuid.UUID, document_id: uuid.UUID) -> Response:
    """Restricted delivery (04 §11.2): only with the Authorization header, decrypted in memory,
    never cached, and EVERY download audited (Q62)."""
    s = service.get_student(ctx.db, ctx.principal, student_id, STUDENT_DOCUMENTS)
    d = ctx.db.scalars(
        select(StudentDocument).where(StudentDocument.id == document_id, StudentDocument.student_id == s.id)
    ).first()
    f = ctx.db.get(File, d.file_id) if d else None
    if d is None or f is None or f.deleted_at is not None:
        raise not_found()
    data = files.read_content(f)
    audit.record(
        ctx.db,
        ctx.principal,
        "student_document.downloaded",
        "student",
        s.id,
        hostel_id=s.hostel_id,
        after={"document_type": d.document_type},
    )
    return Response(content=data, media_type=f.mime_type, headers=files.content_headers(f, inline=False))


@router.post("/students/{student_id}/documents/{document_id}/verify")
def verify_document(ctx: Manage, student_id: uuid.UUID, document_id: uuid.UUID) -> DocumentOut:
    s = service.get_student(ctx.db, ctx.principal, student_id, STUDENTS_MANAGE)
    d = ctx.db.scalars(
        select(StudentDocument).where(StudentDocument.id == document_id, StudentDocument.student_id == s.id)
    ).first()
    if d is None:
        raise not_found()
    if d.verified_at is None:
        d.verified_by, d.verified_at = ctx.principal.user_id, datetime.now(UTC)
        ctx.db.flush()
        audit.record(ctx.db, ctx.principal, "student_document.verified", "student", s.id, hostel_id=s.hostel_id)
    return _doc_out(d)
