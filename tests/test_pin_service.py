"""Pin service -- per-user, per-agent saved questions.

The router relies on the service to enforce two invariants the DB
schema can't express on its own:

1. **Dedup** -- the table has a ``UNIQUE(user_email, endpoint_name, text)``
   constraint. The service must translate the resulting
   :class:`IntegrityError` into a user-friendly :class:`ConflictError`,
   not a 500.
2. **Quota** -- ``feature_flags.pinned.max_per_agent`` caps how many
   pins a user can stash for a single agent. The service must short-
   circuit *before* hitting the DB so the user gets a clear error.

We also exercise validation (empty text, oversized strings) and the
ownership check on update / delete so a mis-routed request can't
mutate someone else's pins.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from agent_hub.backend.services import pin_service as ps
from agent_hub.backend.services.base import (
    ConflictError,
    NotFoundError,
    ValidationError,
)


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #


class FakeResult:
    def __init__(
        self,
        *,
        rows: list[tuple[Any, ...]] | None = None,
        rowcount: int | None = None,
    ) -> None:
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def all(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def one_or_none(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class FakeSession:
    """Pin-service-shaped session.

    The service issues a fixed sequence of queries per operation:

    - ``create_pin``: SELECT count -> SELECT max position -> INSERT -> SELECT id
    - ``update_pin``: SELECT id -> UPDATE -> SELECT id
    - ``delete_pin``: DELETE
    - ``list_pins``:   SELECT all

    We canned-route by SQL fragment so the test stays decoupled from
    incidental whitespace / column reordering.
    """

    def __init__(
        self,
        *,
        pin_count: int = 0,
        max_position: int | None = -1,
        existing_pin: dict[str, Any] | None = None,
        list_rows: list[tuple[Any, ...]] | None = None,
        delete_rowcount: int = 1,
        insert_raises: Exception | None = None,
    ) -> None:
        self._pin_count = pin_count
        self._max_position = max_position
        self._existing = existing_pin
        self._list_rows = list_rows or []
        self._delete_rowcount = delete_rowcount
        self._insert_raises = insert_raises
        self.queries: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        # Latest insert payload so tests can assert what we wrote.
        self.last_insert_params: dict[str, Any] | None = None

    def exec(self, stmt: Any) -> FakeResult:
        sql = str(getattr(stmt, "text", None) or stmt)
        self.queries.append(sql)
        params = getattr(stmt, "_bindparams", {}) or {}

        upper = sql.upper()
        if "INSERT INTO PINNED_QUESTIONS" in upper:
            if self._insert_raises is not None:
                raise self._insert_raises
            self.last_insert_params = {
                k: getattr(p, "value", p) for k, p in params.items()
            }
            return FakeResult(rowcount=1)

        if "UPDATE PINNED_QUESTIONS" in upper:
            return FakeResult(rowcount=1)

        if "DELETE FROM PINNED_QUESTIONS" in upper:
            return FakeResult(rowcount=self._delete_rowcount)

        if "COUNT(*)" in upper and "PINNED_QUESTIONS" in upper:
            return FakeResult(rows=[(self._pin_count,)])

        if "MAX(POSITION)" in upper:
            return FakeResult(rows=[(self._max_position,)])

        if "SELECT" in upper and "WHERE ID" in upper:
            # _fetch_one(pin_id)
            if not self._existing:
                return FakeResult()
            row = (
                self._existing.get("id"),
                self._existing.get("user_email"),
                self._existing.get("endpoint_name"),
                self._existing.get("text"),
                self._existing.get("label"),
                self._existing.get("position", 0),
                self._existing.get("created_at"),
            )
            return FakeResult(rows=[row])

        if "SELECT" in upper and "PINNED_QUESTIONS" in upper:
            # list_pins
            return FakeResult(rows=self._list_rows)

        return FakeResult()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture(autouse=True)
def _stub_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default the quota to 5 unless a test overrides it.

    Pin service reads ``feature_flags.pinned.max_per_agent`` -- patching
    here keeps tests independent of admin_settings reads.

    Also silences ``pin_event_service.record_event`` so the telemetry
    instrumentation (create/update/delete events) doesn't bleed into
    this module's commit-count and query-count assertions. Telemetry
    has its own test surface in ``test_pin_events.py``.
    """
    monkeypatch.setattr(
        ps.feature_flags_service, "pin_max_per_agent", lambda _s: 5
    )
    monkeypatch.setattr(
        ps.pin_event_service, "record_event", lambda _s, **_kw: None
    )


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


class TestValidation:
    def test_empty_user_email_raises(self) -> None:
        with pytest.raises(ValidationError):
            ps.create_pin(
                FakeSession(),
                user_email="",
                endpoint_name="ep",
                text_value="hi",
            )

    def test_empty_endpoint_raises(self) -> None:
        with pytest.raises(ValidationError):
            ps.create_pin(
                FakeSession(),
                user_email="u@x.com",
                endpoint_name="",
                text_value="hi",
            )

    def test_empty_text_raises(self) -> None:
        with pytest.raises(ValidationError):
            ps.create_pin(
                FakeSession(),
                user_email="u@x.com",
                endpoint_name="ep",
                text_value="   ",
            )

    def test_oversize_text_raises(self) -> None:
        with pytest.raises(ValidationError):
            ps.create_pin(
                FakeSession(),
                user_email="u@x.com",
                endpoint_name="ep",
                text_value="x" * (ps.MAX_TEXT_CHARS + 1),
            )

    def test_oversize_label_raises(self) -> None:
        with pytest.raises(ValidationError):
            ps.create_pin(
                FakeSession(),
                user_email="u@x.com",
                endpoint_name="ep",
                text_value="ok",
                label="L" * (ps.MAX_LABEL_CHARS + 1),
            )


# --------------------------------------------------------------------------- #
# Quota enforcement
# --------------------------------------------------------------------------- #


class TestQuota:
    def test_at_limit_blocks_create(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ps.feature_flags_service, "pin_max_per_agent", lambda _s: 3
        )
        session = FakeSession(pin_count=3)
        with pytest.raises(ValidationError) as exc:
            ps.create_pin(
                session,
                user_email="u@x.com",
                endpoint_name="ep",
                text_value="another",
            )
        assert "limit" in str(exc.value).lower()
        # We must NOT have issued an INSERT after the quota check failed.
        assert all("INSERT" not in q.upper() for q in session.queries)

    def test_under_limit_allows_create(self) -> None:
        session = FakeSession(pin_count=2, max_position=4)
        # Configure _fetch_one to return the freshly inserted pin so the
        # service can return a populated dict.
        session._existing = {  # type: ignore[attr-defined]
            "id": "ignored",
            "user_email": "u@x.com",
            "endpoint_name": "ep",
            "text": "first?",
            "label": None,
            "position": 5,
            "created_at": None,
        }
        out = ps.create_pin(
            session,
            user_email="u@x.com",
            endpoint_name="ep",
            text_value="first?",
        )
        assert out["text"] == "first?"
        assert out["position"] == 5
        assert session.commits == 1
        assert any("INSERT" in q.upper() for q in session.queries)


# --------------------------------------------------------------------------- #
# Dedup -- IntegrityError -> ConflictError
# --------------------------------------------------------------------------- #


def _unique_violation() -> IntegrityError:
    """Build a realistic-looking unique-constraint IntegrityError."""
    return IntegrityError(
        statement="INSERT ...",
        params=None,
        orig=Exception(
            'duplicate key value violates unique constraint '
            '"pinned_questions_user_email_endpoint_name_text_key"'
        ),
    )


class TestDedup:
    def test_unique_violation_becomes_conflict(self) -> None:
        session = FakeSession(
            pin_count=0, max_position=-1, insert_raises=_unique_violation()
        )
        with pytest.raises(ConflictError):
            ps.create_pin(
                session,
                user_email="u@x.com",
                endpoint_name="ep",
                text_value="duplicate?",
            )
        assert session.rollbacks == 1
        # Must NOT commit on a failed insert.
        assert session.commits == 0

    def test_other_integrity_error_propagates(self) -> None:
        # Foreign-key / not-null violations are bugs, not user errors --
        # they must surface as 500s for ops to investigate, not 409s.
        unrelated = IntegrityError(
            statement="INSERT ...",
            params=None,
            orig=Exception("null value in column violates not-null constraint"),
        )
        session = FakeSession(insert_raises=unrelated)
        with pytest.raises(IntegrityError):
            ps.create_pin(
                session,
                user_email="u@x.com",
                endpoint_name="ep",
                text_value="x",
            )

    def test_text_is_normalized_before_insert(self) -> None:
        # Whitespace collapse is part of the dedup contract: "  Hi  "
        # and "Hi" must be treated as the same pin so a paste-with-
        # trailing-newline doesn't create a near-duplicate row.
        session = FakeSession(pin_count=0, max_position=-1)
        session._existing = {  # type: ignore[attr-defined]
            "id": "x",
            "user_email": "u@x.com",
            "endpoint_name": "ep",
            "text": "Hi there",
            "label": None,
            "position": 0,
            "created_at": None,
        }
        ps.create_pin(
            session,
            user_email="u@x.com",
            endpoint_name="ep",
            text_value="  Hi   there\n",
        )
        params = session.last_insert_params or {}
        assert params.get("txt") == "Hi there"


# --------------------------------------------------------------------------- #
# Ownership checks
# --------------------------------------------------------------------------- #


class TestOwnership:
    def test_update_other_user_raises_not_found(self) -> None:
        # Existing pin belongs to bob; alice tries to patch.
        session = FakeSession(
            existing_pin={
                "id": "pin-1",
                "user_email": "bob@x.com",
                "endpoint_name": "ep",
                "text": "secret",
                "label": None,
                "position": 0,
                "created_at": None,
            }
        )
        with pytest.raises(NotFoundError):
            ps.update_pin(
                session,
                user_email="alice@x.com",
                endpoint_name="ep",
                pin_id="pin-1",
                label="new-label",
                label_set=True,
            )

    def test_update_wrong_endpoint_raises_not_found(self) -> None:
        session = FakeSession(
            existing_pin={
                "id": "pin-1",
                "user_email": "alice@x.com",
                "endpoint_name": "different-ep",
                "text": "x",
                "label": None,
                "position": 0,
                "created_at": None,
            }
        )
        with pytest.raises(NotFoundError):
            ps.update_pin(
                session,
                user_email="alice@x.com",
                endpoint_name="ep",
                pin_id="pin-1",
                label="new",
                label_set=True,
            )

    def test_update_no_change_noop(self) -> None:
        # Neither label_set nor position_set -> we just return existing.
        existing = {
            "id": "pin-1",
            "user_email": "alice@x.com",
            "endpoint_name": "ep",
            "text": "x",
            "label": "L",
            "position": 1,
            "created_at": None,
        }
        session = FakeSession(existing_pin=existing)
        out = ps.update_pin(
            session,
            user_email="alice@x.com",
            endpoint_name="ep",
            pin_id="pin-1",
        )
        assert out == existing
        assert all("UPDATE" not in q.upper() for q in session.queries)

    def test_delete_missing_raises_not_found(self) -> None:
        session = FakeSession(delete_rowcount=0)
        with pytest.raises(NotFoundError):
            ps.delete_pin(
                session,
                user_email="u@x.com",
                endpoint_name="ep",
                pin_id="pin-x",
            )

    def test_delete_success_commits(self) -> None:
        session = FakeSession(delete_rowcount=1)
        ps.delete_pin(
            session,
            user_email="u@x.com",
            endpoint_name="ep",
            pin_id="pin-1",
        )
        assert session.commits == 1


# --------------------------------------------------------------------------- #
# list_pins -- thin shape contract
# --------------------------------------------------------------------------- #


class TestListPins:
    def test_returns_normalized_dicts(self) -> None:
        rows = [
            ("p1", "u@x.com", "ep", "Q1?", "lbl", 0, "2026-04-01"),
            ("p2", "u@x.com", "ep", "Q2?", None, 1, "2026-04-02"),
        ]
        session = FakeSession(list_rows=rows)
        out = ps.list_pins(session, user_email="u@x.com", endpoint_name="ep")
        assert [p["id"] for p in out] == ["p1", "p2"]
        assert out[0]["label"] == "lbl"
        assert out[1]["label"] is None
        assert out[0]["position"] == 0

    def test_empty_user_or_endpoint_returns_empty(self) -> None:
        # Defense-in-depth -- a typo in the router shouldn't surface
        # someone else's pins.
        session = FakeSession(list_rows=[("x", "u", "ep", "x", None, 0, None)])
        assert ps.list_pins(session, user_email="", endpoint_name="ep") == []
        assert ps.list_pins(session, user_email="u", endpoint_name="") == []
