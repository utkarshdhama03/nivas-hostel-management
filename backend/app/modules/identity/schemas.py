"""Request and response bodies for authentication (docs/04 §5).

Every request model forbids unknown fields (04 §1): sending "role": "super_admin" or an
"org_id" is a 422, never silently ignored. Passwords are capped at 1,024 characters so
nobody can make the server hash megabytes of input.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Password = Field(min_length=1, max_length=1024)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- requests -------------------------------------------------------------------------------------


class NewOrganization(Strict):
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(min_length=3, max_length=40)
    contact_email: EmailStr


class NewSuperAdmin(Strict):
    full_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Password


class RegistrationIn(Strict):
    organization: NewOrganization
    super_admin: NewSuperAdmin


class TokenIn(Strict):
    token: str = Field(min_length=10, max_length=200)


class LoginIn(Strict):
    portal: Literal["staff", "student"]
    identifier: str = Field(min_length=1, max_length=254)
    password: str = Password


class PasswordChangeIn(Strict):
    current_password: str = Password
    new_password: str = Password


class ResetRequestIn(Strict):
    identifier: str = Field(min_length=1, max_length=254)


class AdministratorLoginIn(Strict):
    """D78/D80: Organization sign-in (portal staff) and Student sign-in (portal student) with only the
    email and the password; the server finds the organization from the email."""

    email: str = Field(min_length=3, max_length=254)
    password: str = Password
    portal: Literal["staff", "student"] = "staff"


class AdministratorResetRequestIn(Strict):
    email: str = Field(min_length=3, max_length=254)
    portal: Literal["staff", "student"] = "staff"


class AdministratorResetConfirmIn(Strict):
    email: str = Field(min_length=3, max_length=254)
    portal: Literal["staff", "student"] = "staff"
    code: str = Field(min_length=6, max_length=12)
    new_password: str = Password


class ResetConfirmIn(Strict):
    """D77: the same identifier as the request, the 6-digit code from the email, and the new password."""

    identifier: str = Field(min_length=1, max_length=254)
    code: str = Field(min_length=6, max_length=12)
    new_password: str = Password


# --- responses ------------------------------------------------------------------------------------


class UserBrief(BaseModel):
    id: uuid.UUID
    full_name: str
    kind: str


class AuthOut(BaseModel):
    """What a successful sign-in returns (D87: there is no second step)."""

    status: Literal["authenticated"]
    access_token: str | None = None
    token_type: Literal["bearer"] | None = None
    expires_in: int | None = None
    user: UserBrief | None = None


class AcceptedOut(BaseModel):
    status: Literal["accepted"] = "accepted"


class ActivateIn(Strict):
    token: str = Field(min_length=10, max_length=200)
    password: str = Password


class ActivationPreviewOut(BaseModel):
    full_name: str
    expires_at: datetime


class PublicOrgOut(BaseModel):
    slug: str
    name: str
    status: Literal["active", "suspended"]


class SlugAvailabilityOut(BaseModel):
    available: bool
    reason: str | None = None


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_used_at: datetime | None
    ip_address: str | None
    user_agent: str | None
    current: bool


class SessionsOut(BaseModel):
    sessions: list[SessionOut]


class RevokedOut(BaseModel):
    revoked: int


class RoleOut(BaseModel):
    role: str
    hostel_id: uuid.UUID | None


class OrgBrief(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    timezone: str


class MePatch(Strict):
    version: int = Field(ge=1)
    phone: str | None = Field(default=None, pattern=r"^\+?[0-9][0-9 -]{6,19}$")


class MeOut(BaseModel):
    id: uuid.UUID
    version: int
    kind: str
    full_name: str
    email: str | None
    phone: str | None
    status: str
    code: str | None  # Employee ID or Student ID
    organization: OrgBrief
    roles: list[RoleOut]
    permissions: list[str]
