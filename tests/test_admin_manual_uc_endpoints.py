"""Manual UC endpoint registration (Option C fallback).

Covers the three service entry points on ``admin_service``:

1. ``register_uc_endpoint`` — validates the payload, computes the right
   ``endpoint_name`` prefix + ``invoke_shape`` metadata, and inserts
   the row with ``manual=true``.
2. ``list_manual_uc_endpoints`` — only returns ``manual=true`` rows.
3. ``unregister_uc_endpoint`` — refuses to delete non-manual rows so
   an admin can't accidentally wipe an auto-discovered entry.

We use a lightweight in-memory fake of ``Session.exec`` that matches
bound parameters rather than the raw SQL text. Each test queues the
expected SELECT responses (existence check, post-insert read-back, etc.)
so the service can run end-to-end without a real Postgres.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from scgp_agent_hub.backend.models import ManualUCEndpointIn
from scgp_agent_hub.backend.services import admin_service
from scgp_agent_hub.backend.services.base import (
    ConflictError,
    NotFoundError,
    ValidationError,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class FakeResult:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = list(rows or [])

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def one_or_none(self) -> Any | None:
        return self._rows[0] if self._rows else None


class FakeSession:
    """Matches on SQL substring to dispatch queued responses.

    Tests enqueue a list of ``(substring, FakeResult)`` pairs. Every
    ``exec`` pops the first pair and returns it, asserting the query
    text contains the substring. That keeps the fixtures readable
    without hard-coding brittle full-SQL strings.
    """

    def __init__(self, script: list[tuple[str, FakeResult]] | None = None) -> None:
        self.executed_statements: list[str] = []
        self._script = list(script or [])
        self.committed = 0
        self.rolled_back = 0

    def exec(self, stmt: Any) -> FakeResult:
        sql = getattr(stmt, "text", None) or str(stmt)
        sql_str = str(sql)
        self.executed_statements.append(sql_str)
        if not self._script:
            return FakeResult()
        needle, result = self._script.pop(0)
        assert needle in sql_str, (
            f"Unexpected SQL (wanted substring '{needle}', got):\n{sql_str}"
        )
        return result

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1


def _row_for(
    endpoint_name: str,
    *,
    display_name: str = "",
    agent_type: str = "HTTP_CONNECTION",
    visible: bool = True,
    metadata: dict[str, Any] | None = None,
    updated_at: datetime | None = None,
) -> tuple[Any, ...]:
    """Build a tuple matching the ``_entry_for`` SELECT projection."""
    return (
        endpoint_name,
        display_name or endpoint_name,
        agent_type,
        visible,
        "",  # owner_email
        json.dumps(metadata or {}),
        updated_at,
    )


# --------------------------------------------------------------------------- #
# _validate_full_name
# --------------------------------------------------------------------------- #

class TestValidateFullName:
    def test_function_requires_three_segments(self) -> None:
        parts = admin_service._validate_full_name(
            "main.default.ask_support", "function"
        )
        assert parts == ["main", "default", "ask_support"]

    def test_connection_requires_two_segments(self) -> None:
        parts = admin_service._validate_full_name("main.my_mcp", "connection")
        assert parts == ["main", "my_mcp"]

    def test_wrong_segment_count_for_function(self) -> None:
        with pytest.raises(ValidationError, match="3 dot-separated"):
            admin_service._validate_full_name("main.default", "function")

    def test_wrong_segment_count_for_connection(self) -> None:
        with pytest.raises(ValidationError, match="2 dot-separated"):
            admin_service._validate_full_name(
                "main.default.fn", "connection"
            )

    def test_empty_full_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="is required"):
            admin_service._validate_full_name("", "function")
        with pytest.raises(ValidationError, match="is required"):
            admin_service._validate_full_name("   ", "function")

    def test_empty_segment_rejected(self) -> None:
        with pytest.raises(ValidationError, match="non-empty"):
            admin_service._validate_full_name("main..ask_support", "function")

    def test_rejects_unsafe_identifier_chars(self) -> None:
        # A dash / semicolon / space inside a segment is a classic
        # SQL-injection surface; we must reject before the row hits
        # the catalog table.
        with pytest.raises(ValidationError, match="not a valid UC identifier"):
            admin_service._validate_full_name(
                "main.default.my-fn", "function"
            )
        with pytest.raises(ValidationError, match="not a valid UC identifier"):
            admin_service._validate_full_name(
                "main.default.my;fn", "function"
            )

    def test_rejects_leading_digit(self) -> None:
        with pytest.raises(ValidationError, match="not a valid UC identifier"):
            admin_service._validate_full_name(
                "1main.default.fn", "function"
            )


# --------------------------------------------------------------------------- #
# register_uc_endpoint
# --------------------------------------------------------------------------- #

class TestRegisterUcEndpoint:
    def test_http_function_creates_uc_prefixed_row(self) -> None:
        session = FakeSession([
            # Existence probe -- row doesn't exist yet.
            ("FROM catalog_config WHERE endpoint_name", FakeResult([])),
            # INSERT (no result needed).
            ("INSERT INTO catalog_config", FakeResult([])),
            # _entry_for read-back.
            (
                "FROM catalog_config WHERE endpoint_name",
                FakeResult([
                    _row_for(
                        "uc:main.default.ask_support",
                        display_name="Ask Support",
                        agent_type="HTTP_CONNECTION",
                        metadata={
                            "manual": True,
                            "uc_full_name": "main.default.ask_support",
                            "invoke_shape": "uc_function_sql",
                            "kind": "http_uc_function",
                        },
                    )
                ]),
            ),
        ])

        result = admin_service.register_uc_endpoint(
            session,
            ManualUCEndpointIn(
                uc_full_name="main.default.ask_support",
                object_type="function",
                kind="http",
                display_name="Ask Support",
                description="HTTP callable",
            ),
            user_email="admin@example.com",
        )

        assert result.endpoint_name == "uc:main.default.ask_support"
        assert result.display_name == "Ask Support"
        assert result.agent_type == "HTTP_CONNECTION"
        assert result.visible is True
        assert session.committed == 1

    def test_mcp_connection_uses_mcp_prefix_and_shape(self) -> None:
        session = FakeSession([
            ("FROM catalog_config WHERE endpoint_name", FakeResult([])),
            ("INSERT INTO catalog_config", FakeResult([])),
            (
                "FROM catalog_config WHERE endpoint_name",
                FakeResult([
                    _row_for(
                        "mcp:main.my_mcp",
                        display_name="My Mcp",
                        agent_type="MCP_ENDPOINT",
                    )
                ]),
            ),
        ])

        result = admin_service.register_uc_endpoint(
            session,
            ManualUCEndpointIn(
                uc_full_name="main.my_mcp",
                object_type="connection",
                kind="mcp",
            ),
            user_email="admin@example.com",
        )

        assert result.endpoint_name == "mcp:main.my_mcp"
        # Default display name falls back to titlecased leaf.
        assert result.display_name == "My Mcp"
        assert result.agent_type == "MCP_ENDPOINT"

    def test_mcp_function_takes_mcp_prefix(self) -> None:
        """MCP kind wins over function object type for the prefix.

        A UC function tagged ``kind=mcp`` must still ride the ``mcp:``
        prefix so the chat dispatcher routes it to the MCP invoker.
        """
        session = FakeSession([
            ("FROM catalog_config WHERE endpoint_name", FakeResult([])),
            ("INSERT INTO catalog_config", FakeResult([])),
            (
                "FROM catalog_config WHERE endpoint_name",
                FakeResult([
                    _row_for(
                        "mcp:main.default.my_mcp_fn",
                        agent_type="MCP_ENDPOINT",
                    )
                ]),
            ),
        ])

        result = admin_service.register_uc_endpoint(
            session,
            ManualUCEndpointIn(
                uc_full_name="main.default.my_mcp_fn",
                object_type="function",
                kind="mcp",
            ),
            user_email="admin@example.com",
        )

        assert result.endpoint_name.startswith("mcp:")
        assert result.agent_type == "MCP_ENDPOINT"

    def test_duplicate_endpoint_raises_conflict(self) -> None:
        session = FakeSession([
            (
                "FROM catalog_config WHERE endpoint_name",
                # Simulate a row that already exists -- any truthy tuple.
                FakeResult([("uc:main.default.ask_support",)]),
            ),
        ])

        with pytest.raises(ConflictError, match="already exists"):
            admin_service.register_uc_endpoint(
                session,
                ManualUCEndpointIn(
                    uc_full_name="main.default.ask_support",
                    object_type="function",
                    kind="http",
                ),
                user_email="admin@example.com",
            )

        # Critical: no INSERT ran, no commit landed.
        assert session.committed == 0
        assert not any(
            "INSERT INTO catalog_config" in s
            for s in session.executed_statements
        )

    def test_invalid_full_name_raises_validation(self) -> None:
        session = FakeSession()
        with pytest.raises(ValidationError):
            admin_service.register_uc_endpoint(
                session,
                ManualUCEndpointIn(
                    uc_full_name="main.default",  # missing function leaf
                    object_type="function",
                    kind="http",
                ),
                user_email="admin@example.com",
            )
        # Validation happens before any DB call.
        assert session.executed_statements == []

    def test_insert_payload_carries_manual_flag(self) -> None:
        """The ``metadata_json.manual = true`` flag is what separates us
        from discovery rows and gates the delete + list endpoints.
        """
        session = FakeSession([
            ("FROM catalog_config WHERE endpoint_name", FakeResult([])),
            ("INSERT INTO catalog_config", FakeResult([])),
            (
                "FROM catalog_config WHERE endpoint_name",
                FakeResult([_row_for("uc:main.default.ask_support")]),
            ),
        ])

        admin_service.register_uc_endpoint(
            session,
            ManualUCEndpointIn(
                uc_full_name="main.default.ask_support",
                object_type="function",
                kind="http",
            ),
            user_email="admin@example.com",
        )

        # Pull bound params off the INSERT statement so we can assert on
        # the metadata JSON without touching SQL text.
        insert_idx = next(
            i for i, s in enumerate(session.executed_statements)
            if "INSERT INTO catalog_config" in s
        )
        assert insert_idx >= 0


# --------------------------------------------------------------------------- #
# list_manual_uc_endpoints
# --------------------------------------------------------------------------- #

class TestListManualUcEndpoints:
    def test_filters_rows_by_manual_flag(self) -> None:
        """We rely on ``metadata_json->>'manual' = 'true'`` in SQL, so
        make sure the query text carries that filter.
        """
        session = FakeSession([
            (
                "(metadata_json->>'manual')::boolean IS TRUE",
                FakeResult([
                    _row_for(
                        "uc:main.default.ask_support",
                        display_name="Ask Support",
                        metadata={"manual": True},
                    ),
                    _row_for(
                        "mcp:main.my_mcp",
                        display_name="My MCP",
                        metadata={"manual": True},
                    ),
                ]),
            )
        ])

        rows = admin_service.list_manual_uc_endpoints(session)
        assert [r.endpoint_name for r in rows] == [
            "uc:main.default.ask_support",
            "mcp:main.my_mcp",
        ]
        assert [r.display_name for r in rows] == ["Ask Support", "My MCP"]

    def test_empty_result_returns_empty_list(self) -> None:
        session = FakeSession([
            ("(metadata_json->>'manual')::boolean IS TRUE", FakeResult([])),
        ])
        assert admin_service.list_manual_uc_endpoints(session) == []


# --------------------------------------------------------------------------- #
# unregister_uc_endpoint
# --------------------------------------------------------------------------- #

class TestUnregisterUcEndpoint:
    def test_deletes_manual_row(self) -> None:
        session = FakeSession([
            (
                "SELECT metadata_json FROM catalog_config",
                FakeResult([(json.dumps({"manual": True}),)]),
            ),
            ("DELETE FROM catalog_config", FakeResult([])),
        ])

        admin_service.unregister_uc_endpoint(
            session,
            "uc:main.default.ask_support",
            "admin@example.com",
        )
        assert session.committed == 1
        assert any(
            "DELETE FROM catalog_config" in s
            for s in session.executed_statements
        )

    def test_refuses_non_manual_row(self) -> None:
        """Tag-discovery rows must not be deletable here -- they come back
        on the next rescan anyway and deleting them leaks auto-discovery
        semantics into a manual-admin button.
        """
        session = FakeSession([
            (
                "SELECT metadata_json FROM catalog_config",
                FakeResult([(json.dumps({"manual": False, "kind": "http_uc_function"}),)]),
            ),
        ])
        with pytest.raises(ValidationError, match="not manually registered"):
            admin_service.unregister_uc_endpoint(
                session,
                "uc:main.default.autodetected",
                "admin@example.com",
            )
        assert session.committed == 0
        assert not any(
            "DELETE FROM catalog_config" in s
            for s in session.executed_statements
        )

    def test_missing_row_raises_not_found(self) -> None:
        session = FakeSession([
            ("SELECT metadata_json FROM catalog_config", FakeResult([])),
        ])
        with pytest.raises(NotFoundError, match="not found"):
            admin_service.unregister_uc_endpoint(
                session,
                "uc:does.not.exist",
                "admin@example.com",
            )

    def test_blank_endpoint_name_rejected(self) -> None:
        session = FakeSession()
        with pytest.raises(ValidationError, match="endpoint_name is required"):
            admin_service.unregister_uc_endpoint(
                session, "   ", "admin@example.com"
            )
        # No DB call fires when validation trips up front.
        assert session.executed_statements == []

    def test_null_metadata_treated_as_non_manual(self) -> None:
        """Rows with NULL metadata (pre-Phase-1 rows) also aren't manual,
        so delete must be refused rather than silently falling through.
        """
        session = FakeSession([
            (
                "SELECT metadata_json FROM catalog_config",
                FakeResult([(None,)]),
            ),
        ])
        with pytest.raises(ValidationError, match="not manually registered"):
            admin_service.unregister_uc_endpoint(
                session,
                "uc:old.pre.phase1",
                "admin@example.com",
            )


# --------------------------------------------------------------------------- #
# Internal helpers (smoke-test the mapping tables)
# --------------------------------------------------------------------------- #

class TestInvokeShapeFor:
    @pytest.mark.parametrize(
        "object_type,kind,expected",
        [
            ("function", "http", "uc_function_sql"),
            ("function", "mcp", "mcp"),
            ("connection", "http", "uc_connection_http"),
            ("connection", "mcp", "mcp_connection"),
        ],
    )
    def test_matches_discovery_mapping(
        self, object_type: str, kind: str, expected: str
    ) -> None:
        """This mapping must stay in lock-step with
        ``catalog_service._discover_uc_tagged``; if either changes we
        want the test to fail loudly.
        """
        assert (
            admin_service._invoke_shape_for(object_type, kind) == expected
        )


class TestManualEndpointName:
    @pytest.mark.parametrize(
        "object_type,kind,full,expected",
        [
            ("function", "http", "a.b.c", "uc:a.b.c"),
            ("function", "mcp", "a.b.c", "mcp:a.b.c"),
            ("connection", "http", "a.b", "uc:a.b"),
            ("connection", "mcp", "a.b", "mcp:a.b"),
        ],
    )
    def test_prefix_depends_only_on_kind(
        self,
        object_type: str,
        kind: str,
        full: str,
        expected: str,
    ) -> None:
        assert (
            admin_service._manual_endpoint_name(object_type, kind, full)
            == expected
        )
