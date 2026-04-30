"""WorkspaceClient lifespan dependency -- initializes the SDK on startup.

Provides two dependency types:
- App-level client (service principal / CLI profile) for discovery and admin ops
- OBO user client (from X-Forwarded-Access-Token) for per-user access checks
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncGenerator, TypeAlias

from databricks.sdk import WorkspaceClient
from databricks.sdk.config import Config
from fastapi import Depends, FastAPI, Request

from ._base import LifespanDependency
from ._config import logger
from ._headers import HeadersDependency


class _WorkspaceClientDependency(LifespanDependency):
    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncGenerator[None, None]:
        profile = os.environ.get("DATABRICKS_PROFILE")
        try:
            ws = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
            user = ws.current_user.me()
            logger.info("Workspace client initialized for %s at %s", user.user_name, ws.config.host)
        except Exception as e:
            logger.warning("WorkspaceClient init failed (local dev?): %s", e)
            ws = WorkspaceClient(host="https://localhost", token="dev-placeholder") if not profile else WorkspaceClient(profile=profile)

        app.state.workspace_client = ws
        yield

    @staticmethod
    def __call__(*args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Use app.state.workspace_client directly")


def _get_user_ws(
    request: Request,
    headers: HeadersDependency,
) -> WorkspaceClient:
    """Build a per-request WorkspaceClient authenticated as the calling user.

    In deployed Databricks Apps, the proxy sets X-Forwarded-Access-Token.
    In local dev, falls back to the app-level workspace client.
    """
    if not headers.token:
        logger.info(
            "OBO: no token; falling back to app-level WS (email=%s)",
            headers.user_email,
        )
        return request.app.state.workspace_client

    raw_token = headers.token.get_secret_value()
    tok_len = len(raw_token) if raw_token else 0
    if not raw_token or tok_len <= 10 or tok_len >= 10000:
        logger.warning("OBO token invalid length=%d; falling back to app client", tok_len)
        return request.app.state.workspace_client

    logger.info(
        "OBO: using user token len=%d for email=%s",
        tok_len,
        headers.user_email,
    )

    # Reuse the app-level host so we don't accidentally resolve to a
    # different env-driven host. Build a Config explicitly so the SDK can't
    # silently fall back to the service principal's oauth-m2m creds picked
    # up from the DATABRICKS_CLIENT_ID / SECRET env vars in the app runtime.
    app_ws = request.app.state.workspace_client
    host = getattr(getattr(app_ws, "config", None), "host", None) or os.environ.get("DATABRICKS_HOST")
    cfg = Config(
        host=host,
        token=raw_token,
        auth_type="pat",
        client_id=None,
        client_secret=None,
    )
    return WorkspaceClient(config=cfg)


UserWorkspaceClientDependency: TypeAlias = Annotated[
    WorkspaceClient, Depends(_get_user_ws)
]
