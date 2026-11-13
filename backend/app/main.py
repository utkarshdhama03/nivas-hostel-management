"""FastAPI application entry point: wires the request pipeline together.

Order of the layers a request passes through (outermost first):
  RequestContextMiddleware   request ID, client metadata, security headers, access log,
                             last-resort 500 in problem format          (core/middleware.py)
  exception handlers         ProblemError, validation, database errors -> problem details
                                                                        (core/errors.py)
  router dependencies        org_context: token -> tenant transaction -> principal
                                                                        (core/context.py)
  the route function

Start-up (lifespan): refuse to start if the database role could bypass RLS or the keys
are missing or broken (core/startup.py).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.startup import run_startup_checks
from app.modules.allocations import router as allocations
from app.modules.announcements import router as announcements
from app.modules.audit.router import router as audit_router
from app.modules.complaints import router as complaints
from app.modules.files.router import router as files_router
from app.modules.finance import router as finance
from app.modules.health.router import router as health_router
from app.modules.hostels.router import router as hostels_router
from app.modules.identity import router as identity
from app.modules.leave import router as leave
from app.modules.mess import router as mess
from app.modules.notifications.router import router as notifications_router
from app.modules.organizations.router import router as organizations_router
from app.modules.overview.router import router as overview_router
from app.modules.platform import console as platform_console
from app.modules.platform import router as platform
from app.modules.registration import router as registration
from app.modules.staff.router import router as staff_router
from app.modules.students import portal as student_portal
from app.modules.students.router import router as students_router

API_PREFIX = "/api/v1"


def create_app(*, run_checks: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        if run_checks:
            run_startup_checks(("hms_app", "hms_platform"))
        yield

    app = FastAPI(
        title="Hostel Management System API",
        version="0.1.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(health_router)
    api.include_router(identity.public_router)
    api.include_router(identity.org_auth_router)
    api.include_router(identity.auth_router)
    api.include_router(identity.me_router)
    api.include_router(notifications_router)
    api.include_router(organizations_router)
    api.include_router(hostels_router)
    api.include_router(staff_router)
    api.include_router(students_router)
    api.include_router(registration.public_router)
    api.include_router(registration.verify_router)
    api.include_router(registration.router)
    api.include_router(student_portal.router)
    api.include_router(files_router)
    api.include_router(allocations.router)
    api.include_router(allocations.portal)
    api.include_router(finance.router)
    api.include_router(finance.portal)
    api.include_router(leave.router)
    api.include_router(leave.portal)
    api.include_router(audit_router)
    api.include_router(complaints.router)
    api.include_router(complaints.portal)
    api.include_router(mess.router)
    api.include_router(mess.portal)
    api.include_router(announcements.router)
    api.include_router(announcements.portal)
    api.include_router(platform.auth_router)
    api.include_router(platform.router)
    api.include_router(platform_console.router)
    api.include_router(overview_router)
    app.include_router(api)
    return app


app = create_app()
