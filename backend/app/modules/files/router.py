"""Uploads and standard-file delivery (docs/04 §11)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Form, Query, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context import Ctx, PublicDb
from app.core.crud import not_found
from app.core.db import set_tenant
from app.core.errors import ProblemError
from app.core.permissions import STUDENTS_MANAGE, STUDENTS_VIEW, Principal
from app.modules.files import service
from app.modules.files.models import File
from app.modules.identity.models import User, UserSession
from app.modules.students.models import Student, StudentDocument

router = APIRouter(prefix="/files", tags=["files"])
Category = Literal["student_photo", "identity_document", "admission_document", "payment_proof", "complaint_attachment"]
STAFF_CATEGORIES = {"student_photo", "identity_document", "admission_document"}
STUDENT_CATEGORIES = {"payment_proof", "complaint_attachment"}


class UploadOut(BaseModel):
    file_id: uuid.UUID
    category: str
    mime_type: str
    size_bytes: int
    sha256: str


class FileLinkOut(BaseModel):
    url: str
    expires_at: str


@router.post("", status_code=status.HTTP_201_CREATED)
def upload(ctx: Ctx, category: Annotated[Category, Form()], file: UploadFile) -> UploadOut:
    """Step 1 of two (04 §11.1). The file stays unattached (visible only to its uploader) until a
    record references it; unattached files are purged after 24 hours."""
    p = ctx.principal
    if category in STAFF_CATEGORIES and not p.has(STUDENTS_MANAGE):
        raise ProblemError(403, "permission_denied", "Not allowed")
    if category in STUDENT_CATEGORIES and p.kind != "student":
        raise ProblemError(403, "permission_denied", "Not allowed")
    f = service.store_upload(ctx.db, p, category, file.file, file.filename)
    return UploadOut(
        file_id=f.id, category=f.category, mime_type=f.mime_type, size_bytes=f.size_bytes, sha256=f.sha256.hex()
    )


def can_open(db: Session, p: Principal, f: File) -> bool:
    """The per-category access table (03 §5.5), checked through the record that owns the file."""
    if f.deleted_at is not None or f.access_level == "restricted":
        return False
    if db.scalar(select(func.count()).select_from(File).where(File.id == f.id, service.unattached_clause())):
        return f.uploaded_by == p.user_id  # 05 DOC-8
    if f.category == "student_photo":
        s = db.scalars(select(Student).where(Student.photo_file_id == f.id)).first()
        if s is None:
            return False
        if s.user_id == p.user_id:
            return True
        hostels = p.hostels_for(STUDENTS_VIEW)
        return p.kind == "staff" and (hostels is None or s.hostel_id in hostels)
    if f.category == "admission_document":
        d = db.scalars(select(StudentDocument).where(StudentDocument.file_id == f.id)).first()
        s = db.get(Student, d.student_id) if d else None
        hostels = p.hostels_for(STUDENTS_MANAGE)
        return s is not None and (hostels is None or s.hostel_id in hostels)
    if f.category == "payment_proof":  # the submitting student; the Administrators
        from app.core.permissions import FINANCE_VIEW
        from app.modules.finance.models import PaymentSubmission

        sub = db.scalars(select(PaymentSubmission).where(PaymentSubmission.proof_file_id == f.id)).first()
        if sub is None:
            return False
        if sub.submitted_by == p.user_id:
            return True
        hostels = p.hostels_for(FINANCE_VIEW)
        return p.kind == "staff" and (hostels is None or sub.hostel_id in hostels)
    if f.category == "complaint_attachment":  # the submitting student; the hostel's complaint staff (Q40)
        from app.core.permissions import COMPLAINTS_MANAGE
        from app.modules.complaints.models import Complaint, ComplaintAttachment

        c = db.scalars(
            select(Complaint)
            .join(ComplaintAttachment, ComplaintAttachment.complaint_id == Complaint.id)
            .where(ComplaintAttachment.file_id == f.id)
        ).first()
        if c is None:
            return False
        owner = db.scalars(select(Student).where(Student.id == c.student_id)).first()
        if owner is not None and owner.user_id == p.user_id:
            return True
        hostels = p.hostels_for(COMPLAINTS_MANAGE)
        return p.kind == "staff" and (hostels is None or c.hostel_id in hostels)
    return False


@router.get("/{file_id}/link")
def file_link(ctx: Ctx, file_id: uuid.UUID) -> FileLinkOut:
    f = ctx.db.get(File, file_id)
    if f is None or not can_open(ctx.db, ctx.principal, f):
        raise not_found()
    link = service.sign_link(f, ctx.principal)
    return FileLinkOut(url=link.url, expires_at=link.expires_at.isoformat())


@router.get("/{file_id}/content")
def file_content(
    db: PublicDb,
    file_id: uuid.UUID,
    o: Annotated[uuid.UUID, Query()],
    u: Annotated[uuid.UUID, Query()],
    s: Annotated[uuid.UUID, Query()],
    exp: Annotated[int, Query()],
    sig: Annotated[str, Query(max_length=128)],
) -> Response:
    """The signed link (an <img src> can't send the Authorization header). Valid 60 seconds,
    bound to the user and session; the session must still be active (05 DOC-5)."""
    service.verify_link(file_id, o, u, s, exp, sig)
    set_tenant(db, o)
    active = db.scalar(
        select(func.count())
        .select_from(UserSession)
        .join(User, User.id == UserSession.user_id)
        .where(
            UserSession.id == s,
            UserSession.user_id == u,
            UserSession.revoked_at.is_(None),
            UserSession.absolute_expires_at > datetime.now(UTC),  # the clock that wrote it (D67)
            User.status.in_(("active", "read_only")),
        )
    )
    f = db.get(File, file_id)
    if not active or f is None or f.deleted_at is not None or f.access_level == "restricted":
        raise not_found()
    return Response(
        content=service.read_content(f), media_type=f.mime_type, headers=service.content_headers(f, inline=True)
    )
