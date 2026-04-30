"""Pin-events telemetry tests.

Covers:

1. ``record_event`` happy path -- a valid call inserts one row with the
   right bind params and commits.
2. Swallowed DB failure -- when the underlying ``session.exec`` raises,
   the service returns ``None`` and issues a rollback without
   re-raising. This is the contract that keeps a broken telemetry
   pipeline from breaking the user's pin action.
3. Instrumentation -- ``pin_service.create_pin`` / ``update_pin`` /
   ``delete_pin`` each call ``pin_event_service.record_event`` once,
   with the expected ``event_type`` and snapshot fields.
4. ``record_click`` ownership guard -- mismatched ``user_email`` or
   ``endpoint_name`` yields :class:`NotFoundError`, not a fake ``ok``.
5. Unknown pin_id on click route maps to 404 via the same guard.

The backend-wide ``tests/test_pin_service.py`` already exercises the
happy path for create/update/delete; these tests specifically assert
that telemetry is emitted and a DB failure inside telemetry does not
bubble up to the caller.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from scgp_agent_hub.backend.services import (
    pin_event_service as pes,
    pin_service as ps,
)
from scgp_agent_hub.backend.services.base import NotFoundError, ValidationError


# --------------------------------------------------------------------------- #
# Minimal fake session: just enough to capture INSERT params and simulate
# DB failure. Distinct from tests/test_pin_service.py's FakeSession so we
# can monkeypatch pin_service's call into pin_event_service without
# dragging the full multi-query state machine in.
# --------------------------------------------------------------------------- #


class RecordingSession:
    def __init__(self, *, raise_on_exec: Exception | None = None) -> None:
        self._raise_on_exec = raise_on_exec
        self.inserts: list[dict[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0

    def exec(self, stmt: Any) -> Any:
        if self._raise_on_exec is not None:
            raise self._raise_on_exec
        sql = str(getattr(stmt, "text", None) or stmt)
        params = getattr(stmt, "_bindparams", {}) or {}
        resolved = {k: getattr(p, "value", p) for k, p in params.items()}
        if "INSERT INTO PIN_EVENTS" in sql.upper():
            self.inserts.append(resolved)
        return MagicMock()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


# --------------------------------------------------------------------------- #
# record_event
# --------------------------------------------------------------------------- #


class TestRecordEvent:
    def test_happy_path_inserts_and_commits(self) -> None:
        session = RecordingSession()
        event_id = pes.record_event(
            session,
            user_email="u@x.com",
            endpoint_name="ep",
            pin_id="11111111-1111-1111-1111-111111111111",
            event_type="create",
            text_value="what is revenue?",
            label="rev",
            metadata={"position": 3},
        )
        assert event_id is not None
        assert len(session.inserts) == 1
        assert session.commits == 1
        assert session.rollbacks == 0

        row = session.inserts[0]
        assert row["email"] == "u@x.com"
        assert row["ep"] == "ep"
        assert row["pin_id"] == "11111111-1111-1111-1111-111111111111"
        assert row["etype"] == "create"
        assert row["txt"] == "what is revenue?"
        assert row["label"] == "rev"
        assert '"position": 3' in row["meta"]

    def test_null_pin_id_round_trips(self) -> None:
        # delete events must allow pin_id=None because the row is gone.
        session = RecordingSession()
        event_id = pes.record_event(
            session,
            user_email="u@x.com",
            endpoint_name="ep",
            pin_id=None,
            event_type="delete",
        )
        assert event_id is not None
        row = session.inserts[0]
        assert row["pin_id"] is None

    def test_db_failure_is_swallowed(self) -> None:
        # Broken telemetry must not break the caller. The contract is:
        # log, rollback, return None.
        session = RecordingSession(raise_on_exec=RuntimeError("db blip"))
        event_id = pes.record_event(
            session,
            user_email="u@x.com",
            endpoint_name="ep",
            pin_id="abc",
            event_type="click",
        )
        assert event_id is None
        assert session.commits == 0
        assert session.rollbacks == 1

    def test_missing_user_returns_none(self) -> None:
        session = RecordingSession()
        assert (
            pes.record_event(
                session,
                user_email="",
                endpoint_name="ep",
                pin_id=None,
                event_type="create",
            )
            is None
        )
        assert session.inserts == []

    def test_missing_endpoint_returns_none(self) -> None:
        session = RecordingSession()
        assert (
            pes.record_event(
                session,
                user_email="u@x.com",
                endpoint_name="",
                pin_id=None,
                event_type="create",
            )
            is None
        )
        assert session.inserts == []


# --------------------------------------------------------------------------- #
# Instrumentation -- pin_service calls record_event
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _stub_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep quota tests independent of admin_settings."""
    monkeypatch.setattr(
        ps.feature_flags_service, "pin_max_per_agent", lambda _s: 10
    )


class TestInstrumentation:
    def test_create_pin_emits_create_event(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Reuse the full FakeSession from test_pin_service via in-module
        # construction: import lazily so test_pin_service.py ownership stays
        # clean.
        from tests.test_pin_service import FakeSession

        recorded: list[dict[str, Any]] = []

        def fake_record(_s: Any, **kwargs: Any) -> str | None:
            recorded.append(kwargs)
            return "event-id"

        monkeypatch.setattr(
            ps.pin_event_service, "record_event", fake_record
        )

        session = FakeSession(pin_count=0, max_position=-1)
        session._existing = {  # type: ignore[attr-defined]
            "id": "pin-1",
            "user_email": "u@x.com",
            "endpoint_name": "ep",
            "text": "what is revenue?",
            "label": "rev",
            "position": 0,
            "created_at": None,
        }
        ps.create_pin(
            session,
            user_email="u@x.com",
            endpoint_name="ep",
            text_value="what is revenue?",
            label="rev",
        )
        assert len(recorded) == 1
        assert recorded[0]["event_type"] == "create"
        assert recorded[0]["text_value"] == "what is revenue?"
        assert recorded[0]["label"] == "rev"

    def test_update_pin_emits_event_only_when_changed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.test_pin_service import FakeSession

        recorded: list[dict[str, Any]] = []
        monkeypatch.setattr(
            ps.pin_event_service,
            "record_event",
            lambda _s, **kw: recorded.append(kw) or "event-id",
        )

        session = FakeSession(
            existing_pin={
                "id": "pin-1",
                "user_email": "u@x.com",
                "endpoint_name": "ep",
                "text": "q?",
                "label": "old",
                "position": 2,
                "created_at": None,
            }
        )
        # label changes old->new: expect one update event.
        ps.update_pin(
            session,
            user_email="u@x.com",
            endpoint_name="ep",
            pin_id="pin-1",
            label="new",
            label_set=True,
        )
        assert len(recorded) == 1
        assert recorded[0]["event_type"] == "update"
        meta = recorded[0]["metadata"]
        assert meta["label_changed"] is True

    def test_update_pin_noop_emits_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.test_pin_service import FakeSession

        recorded: list[dict[str, Any]] = []
        monkeypatch.setattr(
            ps.pin_event_service,
            "record_event",
            lambda _s, **kw: recorded.append(kw) or "event-id",
        )

        session = FakeSession(
            existing_pin={
                "id": "pin-1",
                "user_email": "u@x.com",
                "endpoint_name": "ep",
                "text": "q?",
                "label": "same",
                "position": 2,
                "created_at": None,
            }
        )
        # Same label resubmitted: must NOT emit an event.
        ps.update_pin(
            session,
            user_email="u@x.com",
            endpoint_name="ep",
            pin_id="pin-1",
            label="same",
            label_set=True,
        )
        assert recorded == []

    def test_delete_pin_emits_delete_event(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.test_pin_service import FakeSession

        recorded: list[dict[str, Any]] = []
        monkeypatch.setattr(
            ps.pin_event_service,
            "record_event",
            lambda _s, **kw: recorded.append(kw) or "event-id",
        )

        session = FakeSession(
            existing_pin={
                "id": "pin-1",
                "user_email": "u@x.com",
                "endpoint_name": "ep",
                "text": "q?",
                "label": "L",
                "position": 2,
                "created_at": None,
            }
        )
        ps.delete_pin(
            session,
            user_email="u@x.com",
            endpoint_name="ep",
            pin_id="pin-1",
        )
        assert len(recorded) == 1
        assert recorded[0]["event_type"] == "delete"
        # Snapshot must survive the delete so dev-team queries still work.
        assert recorded[0]["text_value"] == "q?"
        assert recorded[0]["label"] == "L"


# --------------------------------------------------------------------------- #
# record_click -- ownership guard
# --------------------------------------------------------------------------- #


class TestRecordClick:
    def test_owner_records_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests.test_pin_service import FakeSession

        recorded: list[dict[str, Any]] = []
        monkeypatch.setattr(
            ps.pin_event_service,
            "record_event",
            lambda _s, **kw: recorded.append(kw) or "event-id",
        )

        session = FakeSession(
            existing_pin={
                "id": "pin-1",
                "user_email": "u@x.com",
                "endpoint_name": "ep",
                "text": "q?",
                "label": None,
                "position": 0,
                "created_at": None,
            }
        )
        ok = ps.record_click(
            session,
            user_email="u@x.com",
            endpoint_name="ep",
            pin_id="pin-1",
        )
        assert ok is True
        assert len(recorded) == 1
        assert recorded[0]["event_type"] == "click"
        assert recorded[0]["text_value"] == "q?"

    def test_peer_cannot_click_foreign_pin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ownership mismatch must 404 before any telemetry fires.

        The 404 shape is shared with "no such pin" so a probing caller
        can't tell whether a peer's pin exists.
        """
        from tests.test_pin_service import FakeSession

        called = []
        monkeypatch.setattr(
            ps.pin_event_service,
            "record_event",
            lambda _s, **kw: called.append(kw) or "event-id",
        )

        session = FakeSession(
            existing_pin={
                "id": "pin-1",
                "user_email": "owner@x.com",
                "endpoint_name": "ep",
                "text": "q?",
                "label": None,
                "position": 0,
                "created_at": None,
            }
        )
        with pytest.raises(NotFoundError):
            ps.record_click(
                session,
                user_email="peer@x.com",
                endpoint_name="ep",
                pin_id="pin-1",
            )
        assert called == []

    def test_wrong_endpoint_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.test_pin_service import FakeSession

        called = []
        monkeypatch.setattr(
            ps.pin_event_service,
            "record_event",
            lambda _s, **kw: called.append(kw) or "event-id",
        )

        session = FakeSession(
            existing_pin={
                "id": "pin-1",
                "user_email": "u@x.com",
                "endpoint_name": "other-ep",
                "text": "q?",
                "label": None,
                "position": 0,
                "created_at": None,
            }
        )
        with pytest.raises(NotFoundError):
            ps.record_click(
                session,
                user_email="u@x.com",
                endpoint_name="ep",
                pin_id="pin-1",
            )
        assert called == []

    def test_unknown_pin_404(self) -> None:
        from tests.test_pin_service import FakeSession

        session = FakeSession()  # no existing pin
        with pytest.raises(NotFoundError):
            ps.record_click(
                session,
                user_email="u@x.com",
                endpoint_name="ep",
                pin_id="missing",
            )

    def test_empty_pin_id_422(self) -> None:
        from tests.test_pin_service import FakeSession

        session = FakeSession()
        with pytest.raises(ValidationError):
            ps.record_click(
                session,
                user_email="u@x.com",
                endpoint_name="ep",
                pin_id="",
            )

    def test_telemetry_failure_returns_false_without_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A swallowed DB error inside record_event must surface as
        ``recorded=false`` rather than a 500. The chat UI doesn't branch
        on this today, but the route contract must preserve the signal
        so ops can correlate in logs.
        """
        from tests.test_pin_service import FakeSession

        monkeypatch.setattr(
            ps.pin_event_service,
            "record_event",
            lambda _s, **kw: None,
        )
        session = FakeSession(
            existing_pin={
                "id": "pin-1",
                "user_email": "u@x.com",
                "endpoint_name": "ep",
                "text": "q?",
                "label": None,
                "position": 0,
                "created_at": None,
            }
        )
        ok = ps.record_click(
            session,
            user_email="u@x.com",
            endpoint_name="ep",
            pin_id="pin-1",
        )
        assert ok is False
