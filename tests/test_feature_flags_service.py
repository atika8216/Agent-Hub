"""Two-tier feature flag resolver -- admin master + per-user opt-out.

The resolver is the single source of truth used by both the router (to
gate UI affordances at ``/app/config`` time) and ``chat_service`` (to
gate streaming-side emission). A regression here would either silently
disable a shipped feature for everyone or leak a half-baked one past
the master kill switch.

We exercise the three-state truth table the plan calls out, plus a
handful of edge cases that the production data has surfaced (malformed
JSON, partial flag dicts, agent-type model resolution).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from scgp_agent_hub.backend.services import feature_flags_service as ff


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #


class FakeResult:
    """Mimic the subset of ``sqlmodel.ExecResult`` the service uses."""

    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def one_or_none(self) -> tuple[Any, ...] | None:
        return self._row


class FakeSession:
    """Map SQL fragments to canned ``FakeResult`` rows.

    The service issues exactly two queries we care about (admin row and
    per-user row). Matching by a substring of the SQL keeps the test
    decoupled from incidental whitespace / formatting.
    """

    def __init__(
        self,
        *,
        admin_row: tuple[Any, ...] | None,
        user_row: tuple[Any, ...] | None = None,
        admin_raises: Exception | None = None,
        user_raises: Exception | None = None,
    ) -> None:
        self._admin_row = admin_row
        self._user_row = user_row
        self._admin_raises = admin_raises
        self._user_raises = user_raises
        self.queries: list[str] = []

    def exec(self, stmt: Any) -> FakeResult:
        sql = str(getattr(stmt, "text", None) or stmt)
        self.queries.append(sql)
        if "admin_settings" in sql:
            if self._admin_raises:
                raise self._admin_raises
            return FakeResult(self._admin_row)
        if "user_prefs" in sql:
            if self._user_raises:
                raise self._user_raises
            return FakeResult(self._user_row)
        return FakeResult(None)


def _admin_row(blob: dict[str, Any] | str | None) -> tuple[Any, ...] | None:
    if blob is None:
        return None
    if isinstance(blob, dict):
        return (json.dumps(blob),)
    return (blob,)


def _user_row(overrides: dict[str, bool] | None) -> tuple[Any, ...]:
    if overrides is None:
        return (None,)
    return (json.dumps(overrides),)


# --------------------------------------------------------------------------- #
# Three-state truth table -- this is the contract the plan locks in.
# --------------------------------------------------------------------------- #


class TestIsEnabledTruthTable:
    @pytest.mark.parametrize("key", ["ai_suggestions", "charts", "pinned"])
    def test_admin_master_off_returns_false(self, key: str) -> None:
        # No matter what the user prefers, master OFF wins.
        flags = {
            key: {"enabled": False, "default_on": True},
        }
        session = FakeSession(
            admin_row=_admin_row(flags),
            user_row=_user_row({key: True}),  # user explicitly opts in
        )
        assert ff.is_enabled(session, "alice@example.com", key) is False  # type: ignore[arg-type]

    @pytest.mark.parametrize("key", ["ai_suggestions", "charts", "pinned"])
    def test_admin_on_user_opted_out_returns_false(self, key: str) -> None:
        flags = {key: {"enabled": True, "default_on": True}}
        session = FakeSession(
            admin_row=_admin_row(flags),
            user_row=_user_row({key: False}),
        )
        assert ff.is_enabled(session, "alice@example.com", key) is False  # type: ignore[arg-type]

    @pytest.mark.parametrize("key", ["ai_suggestions", "charts", "pinned"])
    def test_both_on_returns_true(self, key: str) -> None:
        flags = {key: {"enabled": True, "default_on": True}}
        session = FakeSession(
            admin_row=_admin_row(flags),
            user_row=_user_row(None),
        )
        assert ff.is_enabled(session, "alice@example.com", key) is True  # type: ignore[arg-type]

    @pytest.mark.parametrize("key", ["ai_suggestions", "charts", "pinned"])
    def test_admin_default_off_returns_false_even_without_override(
        self, key: str
    ) -> None:
        # default_on=False means admin opted everyone out by default --
        # the user has to explicitly flip on (which we don't model in the
        # current schema; absence-of-override means "follow default").
        flags = {key: {"enabled": True, "default_on": False}}
        session = FakeSession(
            admin_row=_admin_row(flags),
            user_row=_user_row(None),
        )
        assert ff.is_enabled(session, "alice@example.com", key) is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Resilience -- malformed / missing rows must never raise.
# --------------------------------------------------------------------------- #


class TestResilience:
    def test_missing_row_falls_back_to_defaults_off(self) -> None:
        # Defaults ship with ``enabled=False`` (rollback pattern), so a
        # fresh deploy with no admin row should report the feature off.
        session = FakeSession(admin_row=None, user_row=None)
        assert ff.is_enabled(session, "a@b.com", "ai_suggestions") is False
        assert ff.get_admin_flags(session)["ai_suggestions"]["enabled"] is False

    def test_malformed_json_falls_back_to_defaults(self) -> None:
        # The DB stored a non-JSON string -- the resolver must not 500.
        session = FakeSession(admin_row=("not-json-at-all",), user_row=None)
        assert ff.is_enabled(session, "a@b.com", "charts") is False

    def test_admin_read_exception_falls_back_to_defaults(self) -> None:
        session = FakeSession(
            admin_row=None,
            admin_raises=RuntimeError("transient pg blip"),
        )
        # No throw -- chat path keeps streaming on a DB hiccup.
        assert ff.is_enabled(session, "a@b.com", "pinned") is False

    def test_user_read_exception_treated_as_no_overrides(self) -> None:
        # If we can read the admin row but not the user row, the safe
        # behavior is to fall back to "no opt-out" so admins-on means on.
        flags = {"charts": {"enabled": True, "default_on": True}}
        session = FakeSession(
            admin_row=_admin_row(flags),
            user_raises=RuntimeError("user_prefs read failed"),
        )
        assert ff.is_enabled(session, "alice@example.com", "charts") is True

    def test_partial_admin_row_merges_with_defaults(self) -> None:
        # Admin only set ``charts``; ``ai_suggestions`` and ``pinned``
        # must still be readable (with shipping defaults).
        partial = {"charts": {"enabled": True, "default_on": True}}
        session = FakeSession(admin_row=_admin_row(partial))
        merged = ff.get_admin_flags(session)
        assert merged["charts"]["enabled"] is True
        assert "ai_suggestions" in merged and "default_on" in merged["ai_suggestions"]
        assert "pinned" in merged and "max_per_agent" in merged["pinned"]

    def test_empty_user_email_returns_no_overrides(self) -> None:
        # Anonymous / unauthenticated path (e.g. ``/app/config`` without
        # an email header): we still resolve the master state but never
        # try to read user_prefs.
        flags = {"ai_suggestions": {"enabled": True, "default_on": True}}
        session = FakeSession(admin_row=_admin_row(flags))
        assert ff.is_enabled(session, "", "ai_suggestions") is True
        # Confirms we never issued the user_prefs query for empty email.
        assert all("user_prefs" not in q for q in session.queries)


# --------------------------------------------------------------------------- #
# Suggestion-model resolver
# --------------------------------------------------------------------------- #


class TestSuggestionModelFor:
    def test_default_when_unset(self) -> None:
        session = FakeSession(admin_row=None)
        assert ff.suggestion_model_for(session, "MAS") == ff.DEFAULT_SUGGESTION_MODEL

    def test_per_agent_type_override_wins(self) -> None:
        flags = {
            "ai_suggestions": {
                "enabled": True,
                "default_on": True,
                "models": {
                    "default": "default-model",
                    "MAS": "mas-tuned-model",
                },
            }
        }
        session = FakeSession(admin_row=_admin_row(flags))
        assert ff.suggestion_model_for(session, "MAS") == "mas-tuned-model"
        # Case fallback -- admins shouldn't have to remember casing.
        assert ff.suggestion_model_for(session, "mas") == "mas-tuned-model"

    def test_falls_back_to_default_slot(self) -> None:
        flags = {
            "ai_suggestions": {
                "models": {"default": "fallback-model"},
            }
        }
        session = FakeSession(admin_row=_admin_row(flags))
        assert ff.suggestion_model_for(session, "GENIE_SPACE") == "fallback-model"

    def test_corrupt_models_value_uses_constant_default(self) -> None:
        # Admin saved a string by mistake instead of a dict.
        flags = {"ai_suggestions": {"models": "oops"}}
        session = FakeSession(admin_row=_admin_row(flags))
        assert ff.suggestion_model_for(session, "MAS") == ff.DEFAULT_SUGGESTION_MODEL


# --------------------------------------------------------------------------- #
# Numeric guard rails -- chart cap + pin cap.
# --------------------------------------------------------------------------- #


class TestNumericCaps:
    def test_chart_max_rows_default(self) -> None:
        session = FakeSession(admin_row=None)
        assert ff.chart_max_rows(session) == ff.DEFAULT_CHART_MAX_ROWS

    def test_chart_max_rows_custom(self) -> None:
        flags = {"charts": {"max_rows": 1234}}
        session = FakeSession(admin_row=_admin_row(flags))
        assert ff.chart_max_rows(session) == 1234

    @pytest.mark.parametrize("bad", [0, -1, "abc", None])
    def test_chart_max_rows_invalid_falls_back(self, bad: Any) -> None:
        flags = {"charts": {"max_rows": bad}}
        session = FakeSession(admin_row=_admin_row(flags))
        assert ff.chart_max_rows(session) == ff.DEFAULT_CHART_MAX_ROWS

    def test_pin_max_per_agent_default(self) -> None:
        session = FakeSession(admin_row=None)
        assert ff.pin_max_per_agent(session) == ff.DEFAULT_PIN_MAX_PER_AGENT

    def test_pin_max_per_agent_custom(self) -> None:
        flags = {"pinned": {"max_per_agent": 7}}
        session = FakeSession(admin_row=_admin_row(flags))
        assert ff.pin_max_per_agent(session) == 7


# --------------------------------------------------------------------------- #
# Admin validator -- guards the PUT path so admins can't store junk.
# --------------------------------------------------------------------------- #


class TestValidateBlob:
    def test_round_trip(self) -> None:
        blob = json.dumps(
            {
                "ai_suggestions": {
                    "enabled": True,
                    "default_on": True,
                    "models": {"default": "m"},
                },
                "charts": {"enabled": True, "default_on": True, "max_rows": 100},
                "pinned": {"enabled": True, "default_on": True, "max_per_agent": 5},
            }
        )
        parsed = ff.validate_feature_flags_blob(blob)
        assert parsed["charts"]["max_rows"] == 100

    @pytest.mark.parametrize(
        "blob",
        [
            "not json",
            "[1,2,3]",  # not an object
            json.dumps({"unknown_feature": {}}),
            json.dumps({"ai_suggestions": "string instead of object"}),
            json.dumps({"charts": {"enabled": "yes"}}),  # bad bool
            json.dumps({"charts": {"max_rows": -1}}),
            json.dumps({"charts": {"max_rows": "x"}}),
            json.dumps({"pinned": {"max_per_agent": 0}}),
            json.dumps({"ai_suggestions": {"models": "no"}}),
            json.dumps({"ai_suggestions": {"models": {"default": 42}}}),
        ],
    )
    def test_rejects_invalid_blob(self, blob: str) -> None:
        with pytest.raises(ValueError):
            ff.validate_feature_flags_blob(blob)
