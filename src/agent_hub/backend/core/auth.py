"""RBAC middleware -- role-based access control via Databricks Apps headers."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends, Request
from sqlmodel import Session, text

from ..services.base import ForbiddenError
from ._config import logger
from .lakebase import LakebaseDependency


def _bootstrap_admin_emails() -> set[str]:
    """Read comma-separated emails from BOOTSTRAP_ADMIN_EMAILS env var.

    This list is *only* used by diagnostic endpoints that must keep working
    when Lakebase is down (see ``require_debug_admin`` below). Case-insensitive
    to match Databricks Apps header behaviour, which normalises emails to
    lowercase.
    """
    raw = os.environ.get("BOOTSTRAP_ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _resolve_user_email(request: Request) -> str:
    """Extract the caller's email from Databricks Apps headers or workspace client."""
    email = request.headers.get("X-Forwarded-Email", "")
    if email:
        return email
    try:
        ws = request.app.state.workspace_client
        me = ws.current_user.me()
        return me.user_name or os.environ.get("USER", "anonymous")
    except Exception:
        return os.environ.get("USER", "anonymous")


def _get_user_role(session: Session | None, email: str) -> str:
    """Look up the user's role from the user_roles table.

    If no admin users exist at all, auto-promote this user to admin
    (first-user-is-admin pattern for initial setup).
    Returns 'user' if session is None (DB unavailable).
    """
    if session is None:
        return "user"
    try:
        row = session.exec(
            text("SELECT role FROM user_roles WHERE email = :email").bindparams(email=email)
        ).one_or_none()
    except Exception:
        return "admin"

    if row is not None:
        try:
            return str(row[0])
        except (IndexError, TypeError):
            return str(row)

    try:
        has_any_admin = session.exec(
            text("SELECT 1 FROM user_roles WHERE role = 'admin' LIMIT 1")
        ).one_or_none()
        if has_any_admin is None:
            conn = session.connection()
            conn.execute(
                text("INSERT INTO user_roles (email, role) VALUES (:email, 'admin')"),
                {"email": email},
            )
            session.commit()
            return "admin"
    except Exception:
        return "admin"

    return "user"


ROLE_HIERARCHY = {"user": 0, "admin": 1}


def require_role(*allowed_roles: str):
    """FastAPI dependency factory that enforces role-based access.

    Usage:
        @router.get("/admin/settings", dependencies=[Depends(require_role("admin"))])
    """

    def _checker(
        request: Request,
        session: LakebaseDependency,
    ) -> None:
        email = _resolve_user_email(request)
        user_role = _get_user_role(session, email)

        min_required = min(ROLE_HIERARCHY.get(r, 99) for r in allowed_roles)
        user_level = ROLE_HIERARCHY.get(user_role, 0)

        if user_level < min_required:
            logger.warning(
                "Authorization failed: user %s has role %s; required one of %s",
                email, user_role, list(allowed_roles),
            )
            raise ForbiddenError(
                f"Role '{user_role}' insufficient; requires one of {list(allowed_roles)}"
            )

    return _checker


def require_debug_admin(request: Request) -> None:
    """Admin gate for diagnostic endpoints that must survive a Lakebase outage.

    Order of checks (first hit wins):
      1. Caller email is in ``BOOTSTRAP_ADMIN_EMAILS`` (case-insensitive).
      2. Lakebase is up AND the user_roles table lists the caller as 'admin'.

    If neither holds we 403 with a message that points the operator at
    ``BOOTSTRAP_ADMIN_EMAILS`` -- the whole point of this gate is to keep the
    ``/debug/me/scopes`` runbook usable when things are already broken.
    """
    email = _resolve_user_email(request)
    bootstrap = _bootstrap_admin_emails()
    if email.lower() in bootstrap:
        return

    engine = getattr(request.app.state, "engine", None)
    if engine is not None:
        try:
            with Session(bind=engine) as session:
                role = _get_user_role(session, email)
            if ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY["admin"]:
                return
            logger.warning(
                "require_debug_admin: caller %s has role %s (needs admin)",
                email, role,
            )
        except Exception as exc:
            logger.warning(
                "require_debug_admin: DB role lookup failed for %s: %s",
                email, exc,
            )

    raise ForbiddenError(
        "Debug endpoint requires admin. Add your email to BOOTSTRAP_ADMIN_EMAILS "
        "in the app env (comma-separated) and redeploy; this path is required "
        "when Lakebase is unavailable, otherwise grant yourself admin in user_roles."
    )
