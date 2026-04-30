"""Debug / diagnostics service.

Surfaces discrepancies between what the Databricks App manifest *claims*
the user granted (``effective_user_api_scopes``) and what's actually
embedded in the forwarded OBO token. This is the debugging counterpart to
the OBO auth design doc (``docs/obo-auth-design.md`` §F6).

Admin-only (enforced at the router via ``require_role('admin')``).
Never emits the raw token value anywhere.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from typing import Any

from databricks.sdk import WorkspaceClient

from ..core._config import logger
from ..core._headers import DatabricksAppsHeaders
from ..models import ScopeDebugOut


_SCOPE_SPLIT_RE = re.compile(r"[\s,]+")


def _jwt_payload(token: str) -> dict[str, Any] | None:
    """Base64-decode the JWT payload segment.

    Returns ``None`` when the token is not a JWT (opaque bearer tokens
    exist on some Databricks deployments, and local-dev CLI-profile
    tokens frequently are PATs rather than JWTs).
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    body = parts[1]
    # JWT uses base64url with stripped padding -- restore it.
    padding = "=" * (-len(body) % 4)
    try:
        decoded = base64.urlsafe_b64decode(body + padding)
        return json.loads(decoded)
    except (ValueError, binascii.Error, json.JSONDecodeError):
        return None


def _scopes_from_claim(payload: dict[str, Any]) -> list[str]:
    """Pull a normalized scope list out of common JWT scope claims."""
    for key in ("scope", "scp", "scopes"):
        raw = payload.get(key)
        if raw is None:
            continue
        if isinstance(raw, list):
            return [str(s).strip() for s in raw if str(s).strip()]
        if isinstance(raw, str):
            return [s for s in _SCOPE_SPLIT_RE.split(raw) if s]
    return []


def _declared_scopes(sp_ws: WorkspaceClient) -> tuple[list[str], str, list[str]]:
    """Look up the current app's declared user scopes.

    Returns a tuple of ``(scopes, app_name, notes)``. ``notes`` carries any
    non-fatal warnings (e.g. app-metadata call failed, env var missing).
    """
    notes: list[str] = []
    app_name = (
        os.environ.get("DATABRICKS_APP_NAME")
        or os.environ.get("APP_NAME")
        or ""
    )
    if not app_name:
        notes.append(
            "DATABRICKS_APP_NAME env var not set; cannot look up app metadata. "
            "Falling back to empty declared-scope list."
        )
        return [], "", notes

    try:
        app = sp_ws.apps.get(app_name)
    except Exception as e:
        short = str(e).split("Config:")[0].strip()[:160]
        notes.append(f"ws.apps.get({app_name!r}) failed: {short}")
        return [], app_name, notes

    # The SDK field name has changed over time; try both and fall back to
    # the manifest-equivalent ``user_api_scopes``.
    for attr in ("effective_user_api_scopes", "user_api_scopes"):
        value = getattr(app, attr, None)
        if value:
            return [str(s) for s in value], app_name, notes

    notes.append(
        "App metadata returned no effective_user_api_scopes; this usually "
        "means no user has completed the OAuth consent flow yet."
    )
    return [], app_name, notes


def inspect_scopes(
    headers: DatabricksAppsHeaders,
    sp_ws: WorkspaceClient,
) -> ScopeDebugOut:
    """Produce a diff between declared scopes and scopes embedded in the token.

    This is read-only and never persists the raw token. The service
    principal client is only used to read the *app's own* manifest --
    we're not calling any user-scoped API here.
    """
    notes: list[str] = []

    declared, app_name, decl_notes = _declared_scopes(sp_ws)
    notes.extend(decl_notes)

    if headers.token is None:
        logger.info("debug/me/scopes: no X-Forwarded-Access-Token header")
        return ScopeDebugOut(
            ok=not declared,  # if we declared nothing, there's nothing to miss
            token_kind="missing",
            declared=declared,
            in_token=None,
            missing_from_token=list(declared),
            extra_in_token=[],
            user_email=headers.user_email or "",
            app_name=app_name,
            notes=notes + [
                "No X-Forwarded-Access-Token header present. Most likely you "
                "are running locally (apx dev), where the proxy does not "
                "inject OBO headers -- this report reflects only the "
                "declared scopes."
            ],
        )

    raw = headers.token.get_secret_value()
    payload = _jwt_payload(raw)
    if payload is None:
        notes.append(
            "Token present but not a decodable JWT (opaque or non-standard "
            "format). We cannot list its scopes; rely on an API call to "
            "confirm grant status."
        )
        return ScopeDebugOut(
            ok=False,
            token_kind="opaque",
            declared=declared,
            in_token=None,
            missing_from_token=list(declared),
            extra_in_token=[],
            user_email=headers.user_email or "",
            app_name=app_name,
            notes=notes,
        )

    in_token = sorted(set(_scopes_from_claim(payload)))
    declared_set = set(declared)
    in_token_set = set(in_token)

    missing = sorted(declared_set - in_token_set)
    extra = sorted(in_token_set - declared_set)

    if missing:
        notes.append(
            "Missing scopes typically mean the user consented to an older "
            "version of the app manifest. Remediation: revoke the app "
            "consent in Account Settings -> Access -> Apps, then revisit "
            "the app to trigger a fresh consent prompt (F5)."
        )

    return ScopeDebugOut(
        ok=not missing,
        token_kind="jwt",
        declared=sorted(declared_set),
        in_token=in_token,
        missing_from_token=missing,
        extra_in_token=extra,
        user_email=headers.user_email or payload.get("sub", "") or "",
        app_name=app_name,
        notes=notes,
    )
