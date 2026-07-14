"""HTTP security headers, CSP, and HTTPS enforcement.

WHY THIS EXISTS
---------------
The frontend renders model output as HTML (citation chips, bold, paragraphs). A
Content-Security-Policy is the backstop that keeps a hostile string in a legal
document from becoming script execution in a lawyer's browser, even if an
escaping bug slips through.

CSP NOTE (honest trade-off)
---------------------------
The UI loads Tailwind from a CDN, which compiles styles in-browser and therefore
requires 'unsafe-eval' and 'unsafe-inline' for styles. That measurably weakens
the CSP. The right long-term fix is a build step that ships precompiled CSS and
lets us drop both. Until then, set STRICT_CSP=true to enforce the hardened
policy (which WILL break the Tailwind CDN — use it with a built stylesheet).
"""

from __future__ import annotations

import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, Response

logger = logging.getLogger(__name__)

# Permissive policy that works with the current CDN-based frontend.
_CSP_COMPAT = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.tailwindcss.com 'unsafe-eval' 'unsafe-inline'; "
    "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

# Hardened policy for a build-step frontend (no CDN, no eval).
_CSP_STRICT = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "font-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds defense-in-depth headers to every response; optionally forces HTTPS."""

    def __init__(self, app, force_https: bool = False, strict_csp: bool = False, hsts_seconds: int = 63072000):
        super().__init__(app)
        self.force_https = force_https
        self.csp = _CSP_STRICT if strict_csp else _CSP_COMPAT
        self.hsts_seconds = hsts_seconds

    async def dispatch(self, request, call_next):
        # HTTPS enforcement (respects a reverse proxy's X-Forwarded-Proto).
        if self.force_https:
            proto = request.headers.get("x-forwarded-proto", request.url.scheme)
            if proto != "https":
                return RedirectResponse(request.url.replace(scheme="https"), status_code=308)

        response: Response = await call_next(request)

        response.headers.setdefault("Content-Security-Policy", self.csp)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")          # clickjacking
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
        )
        # Never let a browser or proxy cache a legal answer containing case data.
        if request.url.path in ("/chat", "/case-intelligence", "/simulate-case", "/translate"):
            response.headers.setdefault("Cache-Control", "no-store, private")

        if self.force_https:
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={self.hsts_seconds}; includeSubDomains",
            )
        return response


def install_security_headers(app) -> None:
    """Wire the middleware using env configuration."""
    force_https = os.getenv("FORCE_HTTPS", "false").strip().lower() in {"1", "true", "yes", "on"}
    strict_csp = os.getenv("STRICT_CSP", "false").strip().lower() in {"1", "true", "yes", "on"}
    app.add_middleware(SecurityHeadersMiddleware, force_https=force_https, strict_csp=strict_csp)
    logger.info("Security headers installed (force_https=%s, strict_csp=%s)", force_https, strict_csp)
