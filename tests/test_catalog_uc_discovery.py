"""UC-tag driven discovery (Phase 1 of the master roadmap).

Covers the pure-function slices of the UC/MCP discovery flow:

* Endpoint-name prefix helpers (``uc:<full_name>`` / ``mcp:<full_name>``).
* The configurable admin-warehouse lookup.
* The short-circuit arms of ``_discover_uc_tagged`` that must never crash
  when the SP workspace client or the admin warehouse is missing, or when
  the admin has explicitly disabled discovery via the feature flag.
* ``get_agent_detail`` / ``check_access`` gain ``uc:*`` / ``mcp:*`` branches
  that skip ``serving_endpoints.get`` and grant owner / deferred access.

These tests deliberately avoid spinning up a live SDK client. Higher-level
integration coverage lives in the deploy-verify step of Phase 1.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_hub.backend.models import AgentType, UCTagConfig
from agent_hub.backend.services import catalog_service as cs


# --------------------------------------------------------------------------- #
# Prefix helpers
# --------------------------------------------------------------------------- #


class TestPrefixHelpers:
    def test_uc_roundtrip(self) -> None:
        ep = cs._uc_endpoint_name("main.schema.ask_fn")
        assert ep == "uc:main.schema.ask_fn"
        assert cs._is_uc_endpoint(ep) is True
        assert cs._is_mcp_endpoint(ep) is False
        assert cs._strip_uc_prefix(ep) == "main.schema.ask_fn"

    def test_mcp_roundtrip(self) -> None:
        ep = cs._mcp_endpoint_name("main.schema.chat_srv")
        assert ep == "mcp:main.schema.chat_srv"
        assert cs._is_mcp_endpoint(ep) is True
        assert cs._is_uc_endpoint(ep) is False
        assert cs._strip_uc_prefix(ep) == "main.schema.chat_srv"

    def test_non_prefixed_names_are_classified_false(self) -> None:
        assert cs._is_uc_endpoint("ep-mas") is False
        assert cs._is_mcp_endpoint("ep-mas") is False
        assert cs._is_uc_endpoint("") is False
        assert cs._is_mcp_endpoint("") is False
        # Genie prefix must not be misclassified as UC/MCP.
        assert cs._is_uc_endpoint("genie:abc") is False
        assert cs._is_mcp_endpoint("genie:abc") is False

    def test_strip_uc_prefix_is_idempotent_for_unprefixed(self) -> None:
        # Paranoid safety: callers occasionally pass a plain name by mistake.
        assert cs._strip_uc_prefix("ep-mas") == "ep-mas"
        assert cs._strip_uc_prefix("") == ""


# --------------------------------------------------------------------------- #
# Admin warehouse resolution
# --------------------------------------------------------------------------- #


class TestAdminWarehouseId:
    def test_agent_hub_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_HUB_ADMIN_WAREHOUSE_ID", "wh-override")
        monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-sdk")
        assert cs._admin_warehouse_id() == "wh-override"

    def test_falls_back_to_sdk_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AGENT_HUB_ADMIN_WAREHOUSE_ID", raising=False)
        monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-sdk")
        assert cs._admin_warehouse_id() == "wh-sdk"

    def test_empty_when_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AGENT_HUB_ADMIN_WAREHOUSE_ID", raising=False)
        monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)
        assert cs._admin_warehouse_id() == ""


# --------------------------------------------------------------------------- #
# _discover_uc_tagged short-circuits
# --------------------------------------------------------------------------- #


class TestDiscoverUCTaggedShortCircuits:
    def test_missing_sp_ws_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_HUB_ADMIN_WAREHOUSE_ID", "wh-1")
        created, updated, skipped, warnings = cs._discover_uc_tagged(
            sp_ws=None, session=MagicMock(), tag_config=UCTagConfig()
        )
        assert (created, updated, skipped) == (0, 0, 0)
        assert any("SP workspace client unavailable" in w for w in warnings)

    def test_missing_warehouse_is_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AGENT_HUB_ADMIN_WAREHOUSE_ID", raising=False)
        monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)
        created, updated, skipped, warnings = cs._discover_uc_tagged(
            sp_ws=MagicMock(), session=MagicMock(), tag_config=UCTagConfig()
        )
        assert (created, updated, skipped) == (0, 0, 0)
        assert any("AGENT_HUB_ADMIN_WAREHOUSE_ID" in w for w in warnings)

    def test_disable_flag_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_HUB_ADMIN_WAREHOUSE_ID", "wh-1")
        monkeypatch.setenv("AGENT_HUB_DISABLE_UC_MCP_DISCOVERY", "1")
        created, updated, skipped, warnings = cs._discover_uc_tagged(
            sp_ws=MagicMock(), session=MagicMock(), tag_config=UCTagConfig()
        )
        # Returns cleanly with no warnings so ``discover_from_workspace``
        # doesn't flag the deliberate shut-off as an error in the UI.
        assert (created, updated, skipped, warnings) == (0, 0, 0, [])

    def test_empty_tag_config_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_HUB_ADMIN_WAREHOUSE_ID", "wh-1")
        monkeypatch.delenv("AGENT_HUB_DISABLE_UC_MCP_DISCOVERY", raising=False)
        cfg = UCTagConfig(agent_tag_key="", agent_tag_value="", agent_kind_tag_key="")
        created, updated, skipped, warnings = cs._discover_uc_tagged(
            sp_ws=MagicMock(), session=MagicMock(), tag_config=cfg
        )
        assert (created, updated, skipped) == (0, 0, 0)
        assert any("empty agent_tag" in w for w in warnings)


# --------------------------------------------------------------------------- #
# Tag-driven classification dispatch
# --------------------------------------------------------------------------- #


class TestDiscoverUCTaggedClassification:
    """Stub ``_execute_sp_sql`` so we can prove the classifier upserts the
    right ``uc:`` / ``mcp:`` rows without reaching Databricks."""

    def test_function_default_kind_is_http(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_HUB_ADMIN_WAREHOUSE_ID", "wh-1")
        monkeypatch.delenv("AGENT_HUB_DISABLE_UC_MCP_DISCOVERY", raising=False)

        def _fake_execute(sp_ws: Any, statement: str, warehouse_id: str, **_: Any) -> list[dict[str, Any]]:
            sql = statement.lower()
            if "function_tags" in sql and "agent_tag_key" not in sql and "tag_value)" in sql.split("tag_name")[-1]:
                return [
                    {
                        "catalog_name": "main",
                        "schema_name": "sales",
                        "function_name": "ask_agent",
                        "tag_value": "agent",
                    }
                ]
            if "function_tags" in sql:
                # kind-tag query: no row -> default to http
                return []
            return []

        upserts: list[tuple[str, AgentType, dict[str, Any]]] = []

        def _fake_upsert(
            session: Any,
            endpoint_name: str,
            display: str,
            desc: str,
            agent_type: AgentType,
            metadata: dict[str, Any],
        ) -> tuple[int, int, int]:
            upserts.append((endpoint_name, agent_type, metadata))
            return 1, 0, 0

        monkeypatch.setattr(cs, "_execute_sp_sql", _fake_execute)
        monkeypatch.setattr(cs, "_upsert_uc_row", _fake_upsert)

        sess = MagicMock()
        c, u, s, warnings = cs._discover_uc_tagged(
            sp_ws=MagicMock(), session=sess, tag_config=UCTagConfig()
        )

        assert (c, u, s) == (1, 0, 0)
        assert upserts, "expected upsert to be invoked"
        endpoint, agent_type, meta = upserts[0]
        assert endpoint == "uc:main.sales.ask_agent"
        assert agent_type is AgentType.HTTP_CONNECTION
        assert meta["invoke_shape"] == "uc_function_sql"
        assert meta["kind_tag_value"] == "http"
        # The batch commit always runs at the end, even on a clean path.
        sess.commit.assert_called_once()

    def test_function_with_mcp_kind_tag_is_classified_as_mcp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_HUB_ADMIN_WAREHOUSE_ID", "wh-1")
        monkeypatch.delenv("AGENT_HUB_DISABLE_UC_MCP_DISCOVERY", raising=False)

        call_log: list[str] = []

        def _fake_execute(sp_ws: Any, statement: str, warehouse_id: str, **_: Any) -> list[dict[str, Any]]:
            sql = statement.lower()
            call_log.append("fn" if "function_tags" in sql else "conn")
            if "function_tags" in sql:
                # First call: the "match" query (has both tag_name + tag_value).
                # Second call: the "kind" query (only tag_name filter).
                if "lower(tag_value)" in sql:
                    return [
                        {
                            "catalog_name": "main",
                            "schema_name": "tools",
                            "function_name": "chat_mcp_agent",
                            "tag_value": "agent",
                        }
                    ]
                return [
                    {
                        "catalog_name": "main",
                        "schema_name": "tools",
                        "function_name": "chat_mcp_agent",
                        "tag_value": "mcp",
                    }
                ]
            return []

        upserts: list[tuple[str, AgentType, dict[str, Any]]] = []

        def _fake_upsert(
            session: Any,
            endpoint_name: str,
            display: str,
            desc: str,
            agent_type: AgentType,
            metadata: dict[str, Any],
        ) -> tuple[int, int, int]:
            upserts.append((endpoint_name, agent_type, metadata))
            return 1, 0, 0

        monkeypatch.setattr(cs, "_execute_sp_sql", _fake_execute)
        monkeypatch.setattr(cs, "_upsert_uc_row", _fake_upsert)

        cs._discover_uc_tagged(
            sp_ws=MagicMock(), session=MagicMock(), tag_config=UCTagConfig()
        )

        assert upserts, "expected upsert to be invoked"
        endpoint, agent_type, meta = upserts[0]
        assert endpoint == "mcp:main.tools.chat_mcp_agent"
        assert agent_type is AgentType.MCP_ENDPOINT
        assert meta["invoke_shape"] == "mcp"


# --------------------------------------------------------------------------- #
# get_agent_detail / check_access short-circuits for uc/mcp
# --------------------------------------------------------------------------- #


class _Row(tuple):
    """Tuple subclass that lets tests craft a ``session.exec(...).one_or_none()``
    row using positional indexing (mirrors ``sqlmodel.Row``)."""


def _mock_session_returning(row: tuple | None) -> MagicMock:
    sess = MagicMock()
    result = MagicMock()
    result.one_or_none.return_value = row
    result.all.return_value = [row] if row is not None else []
    sess.exec.return_value = result
    return sess


class TestAccessBranchesForUCAndMCP:
    def test_get_agent_detail_uc_grants_access_and_skips_introspection(
        self,
    ) -> None:
        # Row shape matches the SELECT in get_agent_detail:
        # (endpoint_name, display_name, description, agent_type,
        #  owner_email, metadata_json)
        row = _Row(
            (
                "uc:main.sales.ask_agent",
                "Ask Agent",
                "An HTTP agent",
                "HTTP_CONNECTION",
                "owner@example.com",
                {"invoke_shape": "uc_function_sql"},
            )
        )
        sess = _mock_session_returning(row)

        ws = MagicMock()
        # If the UC branch accidentally falls through to the serving
        # endpoint probe, side_effect makes the test fail loudly.
        ws.serving_endpoints.get.side_effect = AssertionError(
            "uc:* must not call serving_endpoints.get"
        )

        detail = cs.get_agent_detail(
            endpoint_name="uc:main.sales.ask_agent",
            ws=ws,
            session=sess,
            sp_ws=None,
            user_email="someone-else@example.com",
        )

        assert detail.has_access is True
        # UC/MCP don't introspect MAS-style children.
        assert detail.sub_agents == []
        ws.serving_endpoints.get.assert_not_called()

    def test_check_access_mcp_non_owner_returns_deferred(self) -> None:
        row = _Row(
            (
                {"invoke_shape": "mcp"},   # metadata_json
                "owner@example.com",         # owner_email
            )
        )
        sess = _mock_session_returning(row)

        ws = MagicMock()
        ws.serving_endpoints.get.side_effect = AssertionError(
            "mcp:* must not call serving_endpoints.get"
        )

        access = cs.check_access(
            endpoint_name="mcp:main.tools.chat_srv",
            user_ws=ws,
            session=sess,
            user_email="anyone@example.com",
        )

        assert access.has_access is True
        # CAN_USE_DEFERRED signals the real tools.list probe happens in
        # Phase 2 at invocation time.
        assert access.permission_level == "CAN_USE_DEFERRED"
        assert access.sub_agent_access == {}

    def test_check_access_uc_owner_returns_owner_level(self) -> None:
        row = _Row(
            (
                {"invoke_shape": "uc_function_sql"},
                "owner@example.com",
            )
        )
        sess = _mock_session_returning(row)

        ws = MagicMock()
        ws.serving_endpoints.get.side_effect = AssertionError(
            "uc:* must not call serving_endpoints.get"
        )

        access = cs.check_access(
            endpoint_name="uc:main.sales.ask_agent",
            user_ws=ws,
            session=sess,
            user_email="owner@example.com",
        )

        assert access.has_access is True
        assert access.permission_level == "OWNER"
        assert access.sub_agent_access == {}


# --------------------------------------------------------------------------- #
# UC discovery runs when warehouse env is set (regression: skip only when unset)
# --------------------------------------------------------------------------- #


class TestDiscoverUCTaggedWarehouseEnvRegression:
    """Ensure discovery executes SQL path when either admin env var is set."""

    def test_runs_with_both_env_vars_mocked_sql(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_HUB_ADMIN_WAREHOUSE_ID", "wh-admin")
        monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-sdk-fallback")
        monkeypatch.delenv("AGENT_HUB_DISABLE_UC_MCP_DISCOVERY", raising=False)

        calls: list[str] = []

        def _fake_execute(
            sp_ws: Any, statement: str, warehouse_id: str, **_: Any
        ) -> list[dict[str, Any]]:
            calls.append(warehouse_id)
            return []

        monkeypatch.setattr(cs, "_execute_sp_sql", _fake_execute)

        c, u, s, warnings = cs._discover_uc_tagged(
            sp_ws=MagicMock(), session=MagicMock(), tag_config=UCTagConfig()
        )

        assert (c, u, s) == (0, 0, 0)
        assert not warnings
        assert calls and all(w == "wh-admin" for w in calls)

    def test_runs_with_only_databricks_warehouse_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AGENT_HUB_ADMIN_WAREHOUSE_ID", raising=False)
        monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-sdk-only")
        monkeypatch.delenv("AGENT_HUB_DISABLE_UC_MCP_DISCOVERY", raising=False)

        warehouses: list[str] = []

        def _fake_execute(
            sp_ws: Any, statement: str, warehouse_id: str, **_: Any
        ) -> list[dict[str, Any]]:
            warehouses.append(warehouse_id)
            return []

        monkeypatch.setattr(cs, "_execute_sp_sql", _fake_execute)

        cs._discover_uc_tagged(
            sp_ws=MagicMock(), session=MagicMock(), tag_config=UCTagConfig()
        )

        assert warehouses and all(w == "wh-sdk-only" for w in warehouses)
