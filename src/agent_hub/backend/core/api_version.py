"""API versioning middleware with deprecation support."""

from __future__ import annotations

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

CURRENT_VERSION = "v1"
SUPPORTED_VERSIONS = {"v1"}


class APIVersionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-API-Version"] = CURRENT_VERSION
        response.headers["X-Supported-Versions"] = ", ".join(sorted(SUPPORTED_VERSIONS))
        return response
