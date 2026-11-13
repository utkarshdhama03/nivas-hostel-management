"""CSRF protection for the cookie endpoints (docs/04 §5.6, docs/05 AUTH-10).

Only /auth/refresh, /auth/logout and their platform twins read a cookie. Everything
else uses the Authorization header, which browsers never attach on their own, so
CSRF can't reach it. For the cookie endpoints there are three locks:
  1. SameSite=Strict on the cookie: the browser doesn't send it on cross-site requests;
  2. X-Requested-With: hms is required: a cross-site HTML form can't add custom headers,
     and cross-site fetch() with a custom header needs a CORS preflight we never allow;
  3. Origin must be our own site.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import ProblemError


def require_csrf(request: Request) -> None:
    allowed = urlsplit(get_settings().hms_public_url)
    expected_origin = f"{allowed.scheme}://{allowed.netloc}"
    if request.headers.get("x-requested-with") != "hms" or request.headers.get("origin") != expected_origin:
        raise ProblemError(403, "csrf_failed", "Request blocked")
