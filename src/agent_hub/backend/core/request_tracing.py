"""Request tracing middleware -- assigns request IDs and tracks latency."""

from __future__ import annotations

import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from ._config import logger
from .logging_config import request_id_var, user_email_var


class RequestTracingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        email = request.headers.get("X-Forwarded-Email", "")

        token_req = request_id_var.set(req_id)
        token_email = user_email_var.set(email)

        start = time.monotonic()
        try:
            response = await call_next(request)
            elapsed_ms = int((time.monotonic() - start) * 1000)

            response.headers["X-Request-ID"] = req_id
            response.headers["X-Response-Time-Ms"] = str(elapsed_ms)

            if elapsed_ms > 5000:
                logger.warning(
                    "Slow request: %s %s took %dms (status %d)",
                    request.method, request.url.path, elapsed_ms, response.status_code,
                )

            return response
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.error(
                "Request failed: %s %s after %dms",
                request.method, request.url.path, elapsed_ms,
                exc_info=True,
            )
            raise
        finally:
            request_id_var.reset(token_req)
            user_email_var.reset(token_email)
