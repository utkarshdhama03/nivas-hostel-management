"""Student registration requests (D74; docs/04 §12.7a).

Public (no sign-in): the form's facts, the form itself (multipart, with the photo) and the emailed
verification link. The Administrator (students.manage): the list, one request, its photo, approve,
reject. There is no status route: sign-in tells a student how their request stands (identity.service).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from pydantic import BaseModel, EmailStr, Field, TypeAdapter, ValidationError
from sqlalchemy import func, select

from app.core.context import OrgContext, require, staff_only
from app.core.errors import ProblemError
from app.core.permissions import STUDENTS_MANAGE
from app.core.schemas import Out, Page, PageInfo, PageNumber, PageSize, Strict
from app.modules.registration import service
from app.modules.registration.models import StudentRegistrationRequest as Request

public_router = APIRouter(prefix="/public/organizations/{org_slug}", tags=["student registration"])
verify_router = APIRouter(prefix="/o/{org_slug}/auth", tags=["student registration"])
router = APIRouter(prefix="/admin", tags=["student registration"], dependencies=[Depends(staff_only)])
Manage = Annotated[OrgContext, Depends(require(STUDENTS_MANAGE))]

PHONE = r"^\+?[0-9][0-9 -]{6,19}$"
Gender = Literal["male", "female", "other"]
Relationship = Literal["father", "mother", "guardian", "sibling", "spouse", "other"]


# --- public ------------------------------------------------------------------------------------------


class HostelChoiceOut(BaseModel):
    id: uuid.UUID
    name: str
    resident_type: str | None


class RegistrationInfoOut(BaseModel):
    """accepts_requests false: the organization doesn't exist or doesn't take requests (same answer)."""

    accepts_requests: bool
    organization_name: str | None
    hostels: list[HostelChoiceOut]


class AcceptedOut(BaseModel):
    status: Literal["accepted"] = "accepted"


class TokenIn(Strict):
    token: str = Field(min_length=10, max_length=200)


_email = TypeAdapter(EmailStr)


@public_router.get("/student-registration")
def registration_info(org_slug: str) -> RegistrationInfoOut:
    info = service.registration_info(org_slug)
    return RegistrationInfoOut(
        accepts_requests=info.accepts_requests,
        organization_name=info.organization_name,
        hostels=[HostelChoiceOut(id=h.id, name=h.name, resident_type=h.resident_type) for h in info.hostels],
    )


@public_router.post("/student-registration", status_code=status.HTTP_202_ACCEPTED)
def submit_request(
    org_slug: str,
    hostel_id: Annotated[uuid.UUID, Form()],
    full_name: Annotated[str, Form(min_length=1, max_length=120)],
    email: Annotated[str, Form(max_length=254)],
    gender: Annotated[Gender, Form()],
    date_of_birth: Annotated[date, Form()],
    guardian_name: Annotated[str, Form(min_length=1, max_length=120)],
    guardian_relationship: Annotated[Relationship, Form()],
    guardian_phone: Annotated[str, Form(pattern=PHONE)],
    password: Annotated[str, Form(min_length=1, max_length=1024)],
    photo: Annotated[UploadFile, File()],
    phone: Annotated[str | None, Form(pattern=PHONE)] = None,
    program: Annotated[str | None, Form(max_length=80)] = None,
    year_of_study: Annotated[int | None, Form(ge=1, le=10)] = None,
    website: Annotated[str | None, Form()] = None,  # the honeypot: people never see it, bots fill it in
) -> AcceptedOut:
    """Always 202 "check your email" for a valid form, whether or not the email is already known."""
    try:
        clean_email = str(_email.validate_python(email.strip()))
    except ValidationError as exc:
        raise ProblemError(
            422,
            "validation_error",
            "Invalid request",
            errors=[{"field": "email", "code": "invalid_email", "message": "Enter a valid email address"}],
        ) from exc
    service.submit(
        org_slug,
        service.RequestForm(
            hostel_id=hostel_id,
            full_name=full_name,
            email=clean_email,
            phone=phone or None,
            gender=gender,
            date_of_birth=date_of_birth,
            program=program or None,
            year_of_study=year_of_study,
            guardian_name=guardian_name,
            guardian_relationship=guardian_relationship,
            guardian_phone=guardian_phone,
            password=password,
        ),
        photo.file,
        website,
    )
    return AcceptedOut()


@verify_router.post("/student-registration/verify", status_code=status.HTTP_204_NO_CONTENT)
def verify_request(org_slug: str, body: TokenIn) -> None:
    service.verify(org_slug, body.token)


# --- the Administrator -----------------------------------------------------------------------------------


class RegistrationRequestListItem(Out):
    id: uuid.UUID
    hostel_id: uuid.UUID
    status: str
    full_name: str | None
    email: str | None
    created_at: datetime
    decided_at: datetime | None


class RegistrationRequestOut(RegistrationRequestListItem):
    phone: str | None
    gender: str | None
    date_of_birth: date | None
    program: str | None
    year_of_study: int | None
    guardian_name: str | None
    guardian_relationship: str | None
    guardian_phone: str | None
    email_verified_at: datetime | None
    decided_by: uuid.UUID | None
    reject_reason: str | None
    student_id: uuid.UUID | None
    has_photo: bool
    personal_data_deleted_at: datetime | None


class RegistrationRejectIn(Strict):
    reason: str = Field(min_length=5, max_length=500)


class RegistrationApprovedOut(BaseModel):
    student_id: uuid.UUID
    student_code: str


def _out(row: Request) -> RegistrationRequestOut:
    return RegistrationRequestOut.model_validate(
        {
            **{c: getattr(row, c) for c in RegistrationRequestOut.model_fields if c != "has_photo"},
            "has_photo": row.photo is not None,
        }
    )


@router.get("/registration-requests")
def list_requests(
    ctx: Manage,
    page: PageNumber = 1,
    page_size: PageSize = 25,
    status: Literal["pending", "approved", "rejected"] = "pending",
) -> Page[RegistrationRequestListItem]:
    hostels = ctx.principal.hostels_for(STUDENTS_MANAGE)
    query = select(Request).where(Request.status == status)
    if hostels is not None:
        query = query.where(Request.hostel_id.in_(hostels))
    total = ctx.db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    order = Request.created_at.asc() if status == "pending" else Request.decided_at.desc()
    rows = ctx.db.scalars(query.order_by(order, Request.id).offset((page - 1) * page_size).limit(page_size)).all()
    return Page[RegistrationRequestListItem](
        items=[RegistrationRequestListItem.model_validate(r) for r in rows],
        page=PageInfo(number=page, size=page_size, total_items=total, total_pages=max(1, -(-total // page_size))),
    )


@router.get("/registration-requests/{request_id}")
def get_request(ctx: Manage, request_id: uuid.UUID) -> RegistrationRequestOut:
    return _out(service.get_request(ctx, request_id))


@router.get(
    "/registration-requests/{request_id}/photo",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
def request_photo(ctx: Manage, request_id: uuid.UUID) -> Response:
    """The photo the student sent, for the Administrator to look at before deciding. After approval
    it is the student's photo file; after rejection it is deleted with the personal data."""
    row = service.get_request(ctx, request_id)
    if row.photo is None:
        raise ProblemError(404, "not_found", "Not found")
    return Response(
        content=row.photo,
        media_type=row.photo_mime or "image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/registration-requests/{request_id}/approve")
def approve_request(ctx: Manage, request_id: uuid.UUID) -> RegistrationApprovedOut:
    student = service.approve(ctx, request_id)
    return RegistrationApprovedOut(student_id=student.id, student_code=student.student_code)


@router.post("/registration-requests/{request_id}/reject")
def reject_request(ctx: Manage, request_id: uuid.UUID, body: RegistrationRejectIn) -> RegistrationRequestOut:
    return _out(service.reject(ctx, request_id, body.reason))
