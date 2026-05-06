"""Genie Space persistence + admin visibility filter.

Genie Spaces flow through ``catalog_config`` using a ``genie:<space_id>``
endpoint_name key. That lets the admin UI treat them the same as
endpoint-backed agents (Hide toggle, ownership, description) while still
rendering them via the dedicated Genie grid on the catalog page.

These tests exercise:
  * ``_fetch_genie_spaces_raw`` -- OBO first, SP fallback on failure.
  * ``list_genie_spaces`` -- hides spaces whose persisted row is
    ``visible=false``; persist-on-read for newly-seen spaces.
  * ``list_agents`` SQL -- never returns ``genie:*`` rows (they'd
    double-render on the catalog page).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from agent_hub.backend.services.catalog_service import (
    _fetch_genie_spaces_raw,
    _genie_endpoint_name,
    list_agents,
    list_genie_spaces,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _ws_with_spaces_response(response: Any, *, raises: Exception | None = None) -> MagicMock:
    """Build a ``WorkspaceClient`` mock whose ``api_client.do`` returns/raises."""
    ws = MagicMock()
    if raises is not None:
        ws.api_client.do.side_effect = raises
    else:
        ws.api_client.do.return_value = response
    return ws


class FakeResult:
    """Mimic sqlmodel ExecResult returned by ``session.exec``."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self._rows = list(rows or [])

    def all(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def one_or_none(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class FakeSession:
    """Records ``session.exec`` calls and returns configured rows.

    ``response_queue`` is consumed in order -- each call to ``exec`` pops
    the next canned ``FakeResult``. Non-SELECT statements (inserts) fall
    through with an empty result so tests only need to enqueue the reads.
    """

    def __init__(self, response_queue: list[FakeResult] | None = None) -> None:
        self.executed_statements: list[str] = []
        self.committed = 0
        self.rolled_back = 0
        self._queue = list(response_queue or [])

    def exec(self, stmt: Any) -> FakeResult:  # pragma: no cover - trivial
        sql = getattr(stmt, "text", None) or str(stmt)
        self.executed_statements.append(str(sql))
        if self._queue:
            return self._queue.pop(0)
        return FakeResult()

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1


# --------------------------------------------------------------------------- #
# _fetch_genie_spaces_raw
# --------------------------------------------------------------------------- #

class TestFetchGenieSpacesRaw:
    def test_obo_success(self) -> None:
        ws = _ws_with_spaces_response({"spaces": [{"space_id": "abc", "title": "One"}]})
        got = _fetch_genie_spaces_raw(ws)
        assert len(got) == 1
        assert got[0]["space_id"] == "abc"

    def test_falls_back_to_sp_on_obo_failure(self) -> None:
        # Simulates the 970-byte OBO token Genie failure we saw in prod.
        ws = _ws_with_spaces_response(None, raises=RuntimeError("unable to parse response"))
        sp = _ws_with_spaces_response({"spaces": [{"space_id": "sp-1", "title": "SP Space"}]})
        got = _fetch_genie_spaces_raw(ws, sp)
        assert [s["space_id"] for s in got] == ["sp-1"]

    def test_returns_empty_on_both_failing(self) -> None:
        ws = _ws_with_spaces_response(None, raises=RuntimeError("obo boom"))
        sp = _ws_with_spaces_response(None, raises=RuntimeError("sp boom"))
        assert _fetch_genie_spaces_raw(ws, sp) == []

    def test_non_dict_response_degrades_gracefully(self) -> None:
        ws = _ws_with_spaces_response("not a dict")
        assert _fetch_genie_spaces_raw(ws) == []


# --------------------------------------------------------------------------- #
# list_genie_spaces visibility filter
# --------------------------------------------------------------------------- #

class TestListGenieSpacesVisibility:
    def test_hides_spaces_marked_invisible(self) -> None:
        # Prod scenario: admin hides a Genie Space via /admin/catalog and
        # expects end users to stop seeing it on next render.
        raw = {
            "spaces": [
                {"space_id": "a", "title": "Alpha"},
                {"space_id": "b", "title": "Beta"},
                {"space_id": "c", "title": "Gamma"},
            ]
        }
        ws = _ws_with_spaces_response(raw)
        # Pre-populate: alpha visible, beta hidden, gamma not yet seen.
        session = FakeSession(response_queue=[
            FakeResult([
                (_genie_endpoint_name("a"), True),
                (_genie_endpoint_name("b"), False),
            ])
        ])

        result = list_genie_spaces(ws, session=session)
        titles = {s.title for s in result.spaces}
        # Beta is hidden; alpha and (newly-persisted) gamma are shown.
        assert titles == {"Alpha", "Gamma"}

    def test_no_session_returns_everything(self) -> None:
        # Back-compat path: callers that don't pass a session get the
        # unfiltered OBO view (same as before persistence).
        raw = {"spaces": [{"space_id": "a", "title": "Alpha"}]}
        ws = _ws_with_spaces_response(raw)
        result = list_genie_spaces(ws)
        assert len(result.spaces) == 1

    def test_persistence_failure_degrades_to_full_list(self) -> None:
        # If Lakebase is down we still want the user to see *something*;
        # we log and fall through rather than return an empty list.
        raw = {"spaces": [{"space_id": "a", "title": "Alpha"}]}
        ws = _ws_with_spaces_response(raw)

        class BrokenSession(FakeSession):
            def exec(self, stmt: Any) -> FakeResult:
                raise RuntimeError("db down")

        result = list_genie_spaces(ws, session=BrokenSession())
        assert len(result.spaces) == 1


# --------------------------------------------------------------------------- #
# list_agents skips genie:* rows
# --------------------------------------------------------------------------- #

class TestListAgentsSkipsGenie:
    def test_sql_filters_genie_prefix(self) -> None:
        # We don't spin up a real DB here -- instead, inspect the SQL
        # that gets compiled and make sure it carries the genie filter.
        session = FakeSession(response_queue=[FakeResult([])])
        list_agents(session)  # type: ignore[arg-type]
        assert session.executed_statements, "list_agents should have issued SQL"
        sql = session.executed_statements[0]
        assert "genie:" in sql, "list_agents SQL must exclude genie:* rows"
        assert "NOT LIKE" in sql.upper()
