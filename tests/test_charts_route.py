"""Route-level tests for ``GET /messages/{message_id}/charts``.

The endpoint fans out the multi-chart Genie support to the client: one
HTTP call to rehydrate every ``chart_artifacts`` row attached to an
assistant message, in render order, so the UI can reconstruct a stacked
set on reload.

We drive the handler function directly (not via a TestClient) because
the rest of the Phase 4 suite does the same -- it keeps the dependency
tree tight and lets us monkeypatch service-layer helpers without having
to spin up FastAPI / Starlette dependencies.

Cases covered:

* 200 + ordered list when the caller owns the message.
* 200 + empty list when the caller owns the message but it has no
  ``query`` attachments (e.g. a text-only Genie turn).
* 404 when the message doesn't exist at all.
* 404 when another user owns the message (cross-tenant probing guard).
* 404 when the ``charts`` feature is disabled for the caller (aligns
  with the "disabled feature looks the same as missing artifact"
  posture documented on ``_require_feature``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from scgp_agent_hub.backend import router as router_mod
from scgp_agent_hub.backend.router import list_message_charts
from scgp_agent_hub.backend.services.base import NotFoundError


def _fake_request(user_email: str = "atika@example.com") -> Any:
    """Minimal Request with a workspace-client app-state and an OBO header.

    ``_resolve_user_email`` reads the ``X-Forwarded-Email`` header first
    on Databricks Apps; wiring it here bypasses the OBO token path so we
    don't need a real WorkspaceClient. The lookup is case-sensitive
    ``dict.get`` against the exact mixed-case key Databricks emits.
    """
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(engine=None, workspace_client=None)),
        headers={"X-Forwarded-Email": user_email},
    )


class _FakeSession:
    """Enough of the SQLModel session surface to drive the ownership query.

    Returns a monkeypatchable ``owner_email`` so we can script each
    scenario without fabricating a full SQL engine. The handler does
    exactly one ``exec`` call (the ownership check); we intercept that
    and let ``list_artifacts`` flow through its own monkeypatch.
    """

    def __init__(self, owner_email: str | None | _Missing) -> None:
        self._owner_email = owner_email

    def exec(self, _statement: Any) -> Any:  # noqa: A003
        if self._owner_email is _MISSING:
            return _Result(None)
        return _Result((self._owner_email,))


class _Missing:
    """Sentinel for "message row does not exist" vs "exists, no owner"."""


_MISSING = _Missing()


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def one_or_none(self) -> Any:
        return self._value


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #


def _force_feature_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        router_mod.feature_flags_service,
        "is_enabled",
        lambda *a, **k: True,
    )


def test_200_returns_ordered_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_feature_on(monkeypatch)
    # Two artifacts persisted out-of-order on ``idx`` to prove the
    # handler trusts list_artifacts' ordering and does not re-sort.
    monkeypatch.setattr(
        router_mod.chart_service,
        "list_artifacts",
        lambda _s, _m: [
            {
                "id": "c-0",
                "message_id": "m-1",
                "conversation_id": "conv-1",
                "chart_kind": "bar",
                "title": "Primary",
                "columns": [{"name": "k", "type": "STRING"}],
                "rows": [["a", 1]],
                "option": {"series": []},
                "truncated": False,
                "idx": 0,
                "created_at": None,
            },
            {
                "id": "c-1",
                "message_id": "m-1",
                "conversation_id": "conv-1",
                "chart_kind": "line",
                "title": "Drill-down",
                "columns": [{"name": "k", "type": "STRING"}],
                "rows": [["b", 2]],
                "option": {"series": []},
                "truncated": False,
                "idx": 1,
                "created_at": None,
            },
        ],
    )

    out = list_message_charts(
        message_id="m-1",
        request=_fake_request(),
        session=_FakeSession(owner_email="atika@example.com"),  # type: ignore[arg-type]
    )
    assert out.message_id == "m-1"
    assert [c.chart_id for c in out.charts] == ["c-0", "c-1"]
    assert [c.idx for c in out.charts] == [0, 1]
    # Chart kinds pass through unchanged.
    assert [c.chart_kind for c in out.charts] == ["bar", "line"]


def test_200_empty_list_when_message_has_no_charts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Text-only Genie turn: the message exists, the caller owns it, but
    # there are zero chart_artifacts rows. The handler must still 200
    # with an empty list (the UI uses that to hide the card rail).
    _force_feature_on(monkeypatch)
    monkeypatch.setattr(
        router_mod.chart_service, "list_artifacts", lambda _s, _m: []
    )
    out = list_message_charts(
        message_id="m-empty",
        request=_fake_request(),
        session=_FakeSession(owner_email="atika@example.com"),  # type: ignore[arg-type]
    )
    assert out.message_id == "m-empty"
    assert out.charts == []


# --------------------------------------------------------------------------- #
# 404 guards
# --------------------------------------------------------------------------- #


def test_404_when_message_does_not_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_feature_on(monkeypatch)
    # list_artifacts must not be consulted -- we bail before we'd ever
    # read artifacts for a non-existent message.
    monkeypatch.setattr(
        router_mod.chart_service,
        "list_artifacts",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("list_artifacts called for missing message"),
        ),
    )
    with pytest.raises(NotFoundError):
        list_message_charts(
            message_id="m-ghost",
            request=_fake_request(),
            session=_FakeSession(owner_email=_MISSING),  # type: ignore[arg-type]
        )


def test_404_when_caller_does_not_own_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The message exists but belongs to another user. The handler must
    # return the SAME 404 shape so a probing peer cannot distinguish
    # "does not exist" from "exists but not mine".
    _force_feature_on(monkeypatch)
    monkeypatch.setattr(
        router_mod.chart_service,
        "list_artifacts",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("cross-tenant read reached list_artifacts"),
        ),
    )
    with pytest.raises(NotFoundError):
        list_message_charts(
            message_id="m-other",
            request=_fake_request(user_email="me@example.com"),
            session=_FakeSession(owner_email="bob@example.com"),  # type: ignore[arg-type]
        )


def test_404_when_charts_feature_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Master kill-switch (or per-user opt-out) -> the same 404 as a
    # missing row. Matches the posture documented on _require_feature.
    monkeypatch.setattr(
        router_mod.feature_flags_service,
        "is_enabled",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        router_mod.chart_service,
        "list_artifacts",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("list_artifacts called while feature disabled"),
        ),
    )
    with pytest.raises(NotFoundError):
        list_message_charts(
            message_id="m-1",
            request=_fake_request(),
            session=_FakeSession(owner_email="atika@example.com"),  # type: ignore[arg-type]
        )
