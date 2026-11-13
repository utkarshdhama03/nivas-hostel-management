"""HTTP middleware: request ID, request metadata, security headers, access log.

Pure ASGI middleware (not BaseHTTPMiddleware): it's simpler about streaming
responses and keeps contextvars intact for the endpoint.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import metrics, request_state
from app.core.errors import PROBLEM_JSON, problem_body

log = logging.getLogger("hms.access")

# docs/05 WEB-2/3/4: no inline scripts from elsewhere, no framing, no MIME sniffing.
SECURITY_HEADERS = {
    b"content-security-policy": b"default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    b"x-content-type-options": b"nosniff",
    b"referrer-policy": b"no-referrer",
    b"x-frame-options": b"DENY",
    b"cache-control": b"no-store",
}
# Swagger UI (development only) loads its own scripts and styles; it gets no CSP.
NO_CSP_PATHS = ("/api/v1/docs", "/api/v1/openapi.json")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Always generated server-side: a client-supplied ID could be used to confuse logs.
        rid = str(uuid.uuid4())
        headers = dict(scope.get("headers") or [])
        tokens = (
            request_state.request_id.set(rid),
            request_state.client_ip.set(scope["client"][0] if scope.get("client") else None),
            request_state.user_agent.set(headers.get(b"user-agent", b"").decode("latin-1")[:300] or None),
        )
        path: str = scope.get("path", "")
        started = time.perf_counter()
        status = 500

        async def send_with_headers(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                raw = list(message.get("headers", []))
                existing = {k.lower() for k, _ in raw}
                for name, value in SECURITY_HEADERS.items():
                    if name == b"content-security-policy" and path.startswith(NO_CSP_PATHS):
                        continue
                    if name not in existing:
                        raw.append((name, value))
                raw.append((b"x-request-id", rid.encode()))
                message["headers"] = raw
            await send(message)

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send_with_headers(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            # Starlette would build its 500 page OUTSIDE this middleware (no security headers,
            # no request ID). We answer here instead, in the standard problem format.
            log.exception("unhandled error")
            if response_started:
                raise
            body = problem_body(
                500, "internal_error", "Something went wrong", path, detail="Quote the request_id when reporting this."
            )
            await JSONResponse(body, status_code=500, media_type=PROBLEM_JSON)(scope, receive, tracking_send)
        finally:
            metrics.record(status)  # the platform health page's error rate (Q50)
            # Method, path, status, duration. Never the query string, headers or body.
            log.info(
                "request",
                extra={
                    "method": scope.get("method"),
                    "path": path,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            request_state.request_id.reset(tokens[0])
            request_state.client_ip.reset(tokens[1])
            request_state.user_agent.reset(tokens[2])
