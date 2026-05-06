"""Request timeout enforcement middleware."""

from __future__ import annotations

import asyncio

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from ._config import logger

DEFAULT_TIMEOUT_SECONDS = 60
CHAT_TIMEOUT_SECONDS = 120
ADMIN_TIMEOUT_SECONDS = 300
HEALTH_TIMEOUT_SECONDS = 5


class TimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        if "/health/" in path:
            timeout = HEALTH_TIMEOUT_SECONDS
        elif "/chat/" in path:
            timeout = CHAT_TIMEOUT_SECONDS
        elif "/agents/discover" in path or "/admin/" in path:
            timeout = ADMIN_TIMEOUT_SECONDS
        else:
            timeout = DEFAULT_TIMEOUT_SECONDS

        try:
            return await asyncio.wait_for(call_next(request), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Request timeout: %s %s (%ds)", request.method, path, timeout)
            return JSONResponse(
                status_code=504,
                content={"detail": f"Request timed out after {timeout} seconds"},
            )
