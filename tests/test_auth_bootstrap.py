"""``_get_user_role`` -- bootstrap-admin fallback contract.

Why this test exists:
The Admin link in the UI is gated by ``useCurrentUser().isAdmin``, which
reflects the ``role`` returned from ``GET /api/v1/me``. That role is
computed by ``_get_user_role``. Before this change, a DB outage caused
the function to return ``"user"`` even for principals listed in
``BOOTSTRAP_ADMIN_EMAILS`` -- effectively locking the operator out of the
Admin page exactly when they need it most (fresh deploy, Lakebase grant
pending, transient outage).

The fix is to consult ``BOOTSTRAP_ADMIN_EMAILS`` first, regardless of DB
state. These tests pin that contract.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_hub.backend.core.auth import _get_user_role


class _StubResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def one_or_none(self) -> Any:
        return self._value


class _StubSession:
    """Minimal Session double that returns a fixed ``user_roles`` row."""

    def __init__(self, role: str | None) -> None:
        self._role = role
        self.exec_calls: list[str] = []

    def exec(self, statement: Any) -> _StubResult:
        self.exec_calls.append(str(statement))
        if "user_roles WHERE email" in str(statement):
            return _StubResult((self._role,) if self._role else None)
        if "user_roles WHERE role" in str(statement):
            # Pretend at least one admin already exists so the
            # first-user-is-admin auto-promotion path doesn't fire.
            return _StubResult((1,))
        return _StubResult(None)

    def connection(self) -> Any:  # pragma: no cover -- unused on this path
        raise AssertionError("session.connection() should not be called")

    def commit(self) -> None:  # pragma: no cover
        raise AssertionError("session.commit() should not be called")


@pytest.fixture
def bootstrap_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAILS", "boss@example.com, vip@example.com")


@pytest.fixture
def no_bootstrap_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOOTSTRAP_ADMIN_EMAILS", raising=False)


def test_bootstrap_email_no_session_returns_admin(bootstrap_env: None) -> None:
    """DB unavailable + bootstrap email -> admin (the bug we just fixed)."""
    assert _get_user_role(None, "boss@example.com") == "admin"


def test_bootstrap_email_with_session_returns_admin_without_querying_db(
    bootstrap_env: None,
) -> None:
    """Bootstrap email is admin even when the DB *is* up; no query needed."""
    session = _StubSession(role=None)
    assert _get_user_role(session, "VIP@example.com") == "admin"
    assert session.exec_calls == [], "bootstrap path must short-circuit before any DB call"


def test_non_bootstrap_email_no_session_returns_user(no_bootstrap_env: None) -> None:
    """DB unavailable + ordinary user -> user (existing behaviour preserved)."""
    assert _get_user_role(None, "alice@example.com") == "user"


def test_non_bootstrap_email_with_admin_row_returns_admin(
    no_bootstrap_env: None,
) -> None:
    """Ordinary user with an admin row in user_roles -> admin (DB-backed path)."""
    session = _StubSession(role="admin")
    assert _get_user_role(session, "alice@example.com") == "admin"
    assert any(
        "user_roles WHERE email" in c for c in session.exec_calls
    ), "DB lookup must run for non-bootstrap emails"
