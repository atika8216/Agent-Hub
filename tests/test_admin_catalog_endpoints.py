"""Admin grant + rescan service functions.

Covers the two admin-triggered actions we added so MAS/KA tile metadata
can be refreshed without manual curl runs:

1. ``grant_sp_access_on_tiles`` — add the app service principal to each
   MAS/KA tile's Agent Bricks ACL with ``CAN_MANAGE`` (idempotent).
2. ``rescan_mas_ka_metadata`` — re-read every MAS/KA tile detail under
   the SP and upsert the real display name, description and sub-agents.

The Agent Bricks MAS detail endpoint requires both the ``all-apis`` OAuth
scope (which OBO lacks) and per-tile ``CAN_MANAGE``; these two actions
split that work so the first runs under the admin's OBO (which can grant
ACLs) and the second runs under the SP (which has ``all-apis``). See
``docs/rollback-obo-gaps-2026-04-17.md`` §11.2 for the platform rationale.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from scgp_agent_hub.backend.models import AgentType, SubComponentType
from scgp_agent_hub.backend.services import catalog_service
from scgp_agent_hub.backend.services.catalog_service import (
    _invalidate_tile_detail_cache,
    _tile_acl_contains_sp,
    grant_sp_access_on_tiles,
    rescan_mas_ka_metadata,
)


# --------------------------------------------------------------------------- #
# Shared fakes
# --------------------------------------------------------------------------- #

class FakeResult:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = list(rows or [])

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None


class FakeSession:
    """Captures SELECT rows served + statements issued by the service.

    The services issue one SELECT to enumerate MAS/KA rows, then (in
    rescan) one SELECT per row to read the previous metadata before
    writing. Tests enqueue results in that order.
    """

    def __init__(self, response_queue: list[FakeResult] | None = None) -> None:
        self.executed_statements: list[str] = []
        self.committed = 0
        self.rolled_back = 0
        self._queue = list(response_queue or [])

    def exec(self, stmt: Any) -> FakeResult:
        sql = getattr(stmt, "text", None) or str(stmt)
        self.executed_statements.append(str(sql))
        if self._queue:
            return self._queue.pop(0)
        return FakeResult()

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1


def _sp_ws(app_id: str = "sp-app-id") -> MagicMock:
    """A Service Principal workspace client with a configured ``client_id``."""
    ws = MagicMock()
    ws.config = MagicMock()
    ws.config.client_id = app_id
    return ws


def _mas_row(
    endpoint_name: str,
    *,
    agent_type: str = AgentType.MAS.value,
    tile_id: str | None = None,
) -> tuple[str, str, str]:
    """Build a ``catalog_config`` row tuple matching the SELECT projection."""
    meta = {"tile_id": tile_id} if tile_id else {}
    return (endpoint_name, agent_type, json.dumps(meta))


@pytest.fixture(autouse=True)
def _clear_detail_cache() -> None:
    """Prevent MAS-detail cache state from leaking across tests."""
    _invalidate_tile_detail_cache()
    yield
    _invalidate_tile_detail_cache()


# --------------------------------------------------------------------------- #
# _tile_acl_contains_sp
# --------------------------------------------------------------------------- #

class TestTileAclContainsSp:
    def test_matches_application_id_at_can_manage(self) -> None:
        acl = [
            {
                "application_id": "sp-abc",
                "all_permissions": [
                    {"permission_level": "CAN_MANAGE", "inherited": False}
                ],
            }
        ]
        assert _tile_acl_contains_sp(acl, "sp-abc") is True

    def test_matches_spn_key(self) -> None:
        acl = [
            {
                "service_principal_name": "sp-abc",
                "all_permissions": [{"permission_level": "CAN_MANAGE"}],
            }
        ]
        assert _tile_acl_contains_sp(acl, "sp-abc") is True

    def test_rejects_can_query(self) -> None:
        # CAN_QUERY is insufficient — the detail endpoint requires
        # CAN_MANAGE, so we must not report "already granted".
        acl = [
            {
                "application_id": "sp-abc",
                "all_permissions": [{"permission_level": "CAN_QUERY"}],
            }
        ]
        assert _tile_acl_contains_sp(acl, "sp-abc") is False

    def test_rejects_other_sp(self) -> None:
        acl = [
            {
                "application_id": "someone-else",
                "all_permissions": [{"permission_level": "CAN_MANAGE"}],
            }
        ]
        assert _tile_acl_contains_sp(acl, "sp-abc") is False

    def test_empty_acl(self) -> None:
        assert _tile_acl_contains_sp(None, "sp-abc") is False
        assert _tile_acl_contains_sp([], "sp-abc") is False


# --------------------------------------------------------------------------- #
# grant_sp_access_on_tiles
# --------------------------------------------------------------------------- #

class TestGrantSpAccessOnTiles:
    def _user_ws(self, acl_get_response: Any, *, patch_raises: Exception | None = None) -> MagicMock:
        ws = MagicMock()

        def _do(method: str, path: str, body: Any = None) -> Any:
            if method == "GET":
                if isinstance(acl_get_response, Exception):
                    raise acl_get_response
                return acl_get_response
            if method == "PATCH" and patch_raises is not None:
                raise patch_raises
            return {}

        ws.api_client.do.side_effect = _do
        return ws

    def test_grants_and_patches_when_sp_missing(self) -> None:
        session = FakeSession([
            FakeResult([_mas_row("mas-abc-endpoint", tile_id="tile-abc")])
        ])
        user_ws = self._user_ws({"access_control_list": []})
        sp_ws = _sp_ws()

        result = grant_sp_access_on_tiles(user_ws, sp_ws, session)

        assert result.granted == 1
        assert result.already_granted == 0
        assert result.unauthorized == 0
        assert result.rows[0].status == "granted"

        # Two calls: GET the ACL, PATCH the new principal in.
        methods = [c.args[0] for c in user_ws.api_client.do.call_args_list]
        assert methods == ["GET", "PATCH"]
        patch_body = user_ws.api_client.do.call_args_list[1].kwargs.get("body")
        assert patch_body == {
            "access_control_list": [
                {
                    "service_principal_name": "sp-app-id",
                    "permission_level": "CAN_MANAGE",
                }
            ]
        }

    def test_idempotent_when_sp_already_manager(self) -> None:
        """Second click must not re-PATCH or double-count as granted."""
        session = FakeSession([
            FakeResult([_mas_row("mas-abc-endpoint", tile_id="tile-abc")])
        ])
        acl = {
            "access_control_list": [
                {
                    "service_principal_name": "sp-app-id",
                    "all_permissions": [{"permission_level": "CAN_MANAGE"}],
                }
            ]
        }
        user_ws = self._user_ws(acl)
        result = grant_sp_access_on_tiles(user_ws, _sp_ws(), session)

        assert result.granted == 0
        assert result.already_granted == 1
        assert result.rows[0].status == "already_granted"
        # Only the GET call — no PATCH fires.
        methods = [c.args[0] for c in user_ws.api_client.do.call_args_list]
        assert methods == ["GET"]

    def test_403_classified_as_unauthorized(self) -> None:
        """Admin who doesn't manage the tile lands in the unauthorized bucket."""
        session = FakeSession([
            FakeResult([_mas_row("mas-abc-endpoint", tile_id="tile-abc")])
        ])
        user_ws = self._user_ws(RuntimeError("403 Forbidden: PermissionDenied"))
        result = grant_sp_access_on_tiles(user_ws, _sp_ws(), session)

        assert result.unauthorized == 1
        assert result.granted == 0
        assert result.rows[0].status == "unauthorized"

    def test_access_management_scope_missing_is_unauthorized(self) -> None:
        """Databricks Apps does not yet expose ``access-management`` to OBO.

        The Agent Bricks permissions API rejects the call with a very
        specific error. We classify it as ``unauthorized`` (same bucket
        as an admin-without-CAN_MANAGE) but with a distinct message
        that points at the curl fallback so the admin isn't left
        wondering whether they broke something.
        """
        session = FakeSession([
            FakeResult([_mas_row("mas-abc-endpoint", tile_id="tile-abc")])
        ])
        user_ws = self._user_ws(RuntimeError(
            "Provided OAuth token does not have required scopes: "
            "access-management [ReqId: abc]."
        ))
        result = grant_sp_access_on_tiles(user_ws, _sp_ws(), session)

        assert result.unauthorized == 1
        assert result.rows[0].status == "unauthorized"
        # Message must steer the admin to the fallback runbook so they
        # know this is a platform constraint, not a mis-click.
        assert "access-management" in result.rows[0].message
        assert "curl fallback" in result.rows[0].message

    def test_non_403_classified_as_failed(self) -> None:
        session = FakeSession([
            FakeResult([_mas_row("mas-abc-endpoint", tile_id="tile-abc")])
        ])
        user_ws = self._user_ws(RuntimeError("500 Internal Server Error"))
        result = grant_sp_access_on_tiles(user_ws, _sp_ws(), session)

        assert result.failed == 1
        assert result.unauthorized == 0
        assert result.rows[0].status == "failed"

    def test_skips_non_mas_ka_rows(self) -> None:
        """Genie / UC / MCP rows have no tile ACL and must be ignored."""
        session = FakeSession([
            FakeResult([
                ("genie:space-123", AgentType.GENIE_SPACE.value, "{}"),
                ("uc:main.default.fn", AgentType.HTTP_CONNECTION.value, "{}"),
                ("mcp:main.default.srv", AgentType.MCP_ENDPOINT.value, "{}"),
                _mas_row("mas-abc-endpoint", tile_id="tile-abc"),
            ])
        ])
        user_ws = self._user_ws({"access_control_list": []})
        result = grant_sp_access_on_tiles(user_ws, _sp_ws(), session)

        # Only the MAS row is acted on.
        assert len(result.rows) == 1
        assert result.rows[0].endpoint_name == "mas-abc-endpoint"
        assert result.granted == 1

    def test_skips_row_when_tile_id_cannot_be_resolved(self) -> None:
        """Without a tile_id we can't target the ACL — report as skipped."""
        session = FakeSession([
            FakeResult([_mas_row("mas-nometa-endpoint", tile_id=None)])
        ])
        user_ws = MagicMock()
        # Serving-endpoints lookup returns an empty payload (no tile).
        user_ws.api_client.do.return_value = {}
        sp_ws = _sp_ws()
        # Force the resolver to also return nothing via sp fallback.
        sp_ws.api_client.do.return_value = {}

        result = grant_sp_access_on_tiles(user_ws, sp_ws, session)
        assert result.skipped == 1
        assert result.rows[0].status == "skipped"

    def test_no_sp_application_id_short_circuits(self) -> None:
        """If we can't resolve the SP id, nothing can be granted."""
        session = FakeSession([FakeResult([])])
        sp_ws = MagicMock()
        sp_ws.config = MagicMock()
        sp_ws.config.client_id = None
        sp_ws.current_user.me.side_effect = RuntimeError("no-api")

        result = grant_sp_access_on_tiles(MagicMock(), sp_ws, session)

        assert result.failed == 1
        assert result.granted == 0
        assert result.rows[0].endpoint_name == ""


# --------------------------------------------------------------------------- #
# rescan_mas_ka_metadata
# --------------------------------------------------------------------------- #

def _fake_tile_detail_response(
    *,
    name: str = "PTTOR_Ecosystem_Intelligence",
    description: str = "A multi-agent supervisor for the PTTOR ecosystem.",
    tile_id: str = "tile-abc",
    endpoint_name: str = "mas-abc-endpoint",
) -> dict[str, Any]:
    return {
        "multi_agent_supervisor": {
            "tile": {
                "tile_id": tile_id,
                "endpoint_name": endpoint_name,
                "tile_type": "MAS",
                "name": name,
                "description": description,
                "instructions": "",
            },
            "agents": [
                {
                    "name": "policy_operations_agent",
                    "agent_type": "KNOWLEDGE_ASSISTANT",
                    "serving_endpoint": {"name": "ka-ee893c47-endpoint"},
                    "description": "PTTOR policy answers",
                }
            ],
        }
    }


def _fake_ka_detail_response(
    *,
    name: str = "policy_operations_agent",
    description: str = "PTTOR policy answers",
    tile_id: str = "tile-ka",
    endpoint_name: str = "ka-ee893c47-endpoint",
) -> dict[str, Any]:
    """KA tiles live at /api/2.0/knowledge-assistants/{tile_id}.

    Unlike MAS, KA responses don't declare a sub-agent graph -- they
    are leaves in the catalog. We still need the ``tile`` block for
    name + description normalization.
    """
    return {
        "knowledge_assistant": {
            "tile": {
                "tile_id": tile_id,
                "endpoint_name": endpoint_name,
                "tile_type": "KA",
                "name": name,
                "description": description,
                "instructions": "",
            },
        }
    }


def _sp_ws_for_rescan(
    detail_response: Any,
    *,
    detail_raises: Exception | None = None,
    ep_description: str = "legacy endpoint description",
) -> MagicMock:
    """SP client wired for both the detail API and serving_endpoints.get."""
    sp_ws = _sp_ws()

    def _do(method: str, path: str, body: Any = None) -> Any:
        if method == "GET" and (
            path.startswith("/api/2.0/multi-agent-supervisors/")
            or path.startswith("/api/2.0/knowledge-assistants/")
        ):
            if detail_raises is not None:
                raise detail_raises
            return detail_response
        # Fall-through for resolve_tile_id_from_endpoint reads.
        return {}

    sp_ws.api_client.do.side_effect = _do

    # Serving endpoint fake: just needs description + config attributes.
    ep = MagicMock()
    ep.description = ep_description
    ep.task = "agent/v1/chat"
    ep.config = None
    sp_ws.serving_endpoints.get.return_value = ep
    return sp_ws


class TestRescanMasKaMetadata:
    def test_refreshes_row_and_writes_sub_agents(self) -> None:
        # Two SELECTs: MAS/KA enumeration, then the pre-update peek of
        # the row we're about to refresh.
        session = FakeSession([
            FakeResult([_mas_row("mas-abc-endpoint", tile_id="tile-abc")]),
            FakeResult([
                ("Mas Abc", "legacy desc", AgentType.MAS.value, "{}"),
            ]),
        ])

        sp_ws = _sp_ws_for_rescan(_fake_tile_detail_response())
        # ``user_ws=None`` exercises the SP-only path so the existing
        # assertions still hold; the OBO-first routing is covered by
        # test_obo_used_for_serving_endpoint_lookup below.
        result = rescan_mas_ka_metadata(None, sp_ws, session)

        assert result.refreshed == 1
        assert result.unchanged == 0
        assert result.failed == 0
        assert session.committed == 1

        row = result.rows[0]
        assert row.status == "refreshed"
        assert "PTTOR_Ecosystem_Intelligence" in row.message

        # Verify an UPDATE actually happened with the new payload — the
        # fake session records the SQL text so we can grep it.
        assert any("UPDATE catalog_config" in s for s in session.executed_statements)

    def test_force_bypasses_cache_on_repeated_calls(self) -> None:
        """Two rescan clicks must each hit the Agent Bricks detail API."""
        session_a = FakeSession([
            FakeResult([_mas_row("mas-abc-endpoint", tile_id="tile-abc")]),
            FakeResult([("", "", AgentType.MAS.value, "{}")]),
        ])
        session_b = FakeSession([
            FakeResult([_mas_row("mas-abc-endpoint", tile_id="tile-abc")]),
            FakeResult([("", "", AgentType.MAS.value, "{}")]),
        ])

        sp_ws = _sp_ws_for_rescan(_fake_tile_detail_response())

        rescan_mas_ka_metadata(None, sp_ws, session_a)
        rescan_mas_ka_metadata(None, sp_ws, session_b)

        detail_calls = [
            c for c in sp_ws.api_client.do.call_args_list
            if c.args[1].startswith("/api/2.0/multi-agent-supervisors/")
        ]
        assert len(detail_calls) == 2, (
            "Rescan must bypass the 60s TTL cache so every click re-reads"
        )

    def test_detail_unavailable_reports_failed_with_hint(self) -> None:
        """When the SP isn't in the ACL yet, ``None`` surfaces as failed."""
        session = FakeSession([
            FakeResult([_mas_row("mas-abc-endpoint", tile_id="tile-abc")]),
        ])
        sp_ws = _sp_ws_for_rescan(
            {},  # empty dict -> _load_tile_detail returns None
            detail_raises=RuntimeError("403 Forbidden"),
        )
        result = rescan_mas_ka_metadata(None, sp_ws, session)

        assert result.failed == 1
        assert result.refreshed == 0
        assert "Grant access" in (result.rows[0].message or "")

    def test_skips_non_mas_ka_rows(self) -> None:
        session = FakeSession([
            FakeResult([
                ("genie:space-1", AgentType.GENIE_SPACE.value, "{}"),
                ("uc:main.default.fn", AgentType.HTTP_CONNECTION.value, "{}"),
                ("mcp:main.default.srv", AgentType.MCP_ENDPOINT.value, "{}"),
            ]),
        ])
        sp_ws = _sp_ws_for_rescan(_fake_tile_detail_response())
        result = rescan_mas_ka_metadata(None, sp_ws, session)

        # Nothing in the results because every row was filtered out.
        assert result.rows == []
        assert result.refreshed == 0
        assert sp_ws.api_client.do.call_count == 0

    def test_sub_agents_payload_is_well_formed(self) -> None:
        """Persisted sub-agent shape matches the ``SubAgentInfo`` contract."""
        session = FakeSession([
            FakeResult([_mas_row("mas-abc-endpoint", tile_id="tile-abc")]),
            FakeResult([("", "", AgentType.MAS.value, "{}")]),
        ])
        sp_ws = _sp_ws_for_rescan(_fake_tile_detail_response())

        # Spy on the service helper that builds the sub-agent list so we
        # can inspect what ends up in the UPDATE payload.
        seen: dict[str, Any] = {}
        real_resolver = catalog_service._resolve_sub_components

        def _spy(*args: Any, **kwargs: Any) -> Any:
            out = real_resolver(*args, **kwargs)
            seen["out"] = out
            return out

        catalog_service._resolve_sub_components = _spy  # type: ignore[assignment]
        try:
            rescan_mas_ka_metadata(None, sp_ws, session)
        finally:
            catalog_service._resolve_sub_components = real_resolver  # type: ignore[assignment]

        subs = seen.get("out") or []
        assert subs, "rescan must emit at least one sub-agent for this fixture"
        first = subs[0]
        assert first["type"] == SubComponentType.KNOWLEDGE_ASSISTANT.value
        assert first["endpoint_ref"] == "ka-ee893c47-endpoint"

    def test_ka_row_hits_knowledge_assistants_url_and_refreshes(self) -> None:
        """KA tiles live at a different detail URL than MAS tiles.

        Regression test for the 2026-04-27 bug where ``AgentType.KA``
        rows blew up with ``AttributeError: KNOWLEDGE_ASSISTANT`` in
        the rescan service *and* where the MAS URL was hit for KA
        tiles, triggering ``Tile type config is not of type MasConfig``
        from Agent Bricks. The fix:

        1. Use ``AgentType.KA.value`` (the enum member name is ``KA``,
           not ``KNOWLEDGE_ASSISTANT``).
        2. Route ``ka-…`` endpoints to
           ``/api/2.0/knowledge-assistants/{tile_id}``.
        3. Normalize the KA response shape
           (``knowledge_assistant.tile``) alongside the MAS shape.
        """
        session = FakeSession([
            FakeResult([
                (
                    "ka-ee893c47-endpoint",
                    AgentType.KA.value,
                    json.dumps({"tile_id": "tile-ka"}),
                )
            ]),
            FakeResult([("", "", AgentType.KA.value, "{}")]),
        ])

        # Wire the SP client so the KA URL responds with the KA-shape
        # payload and the MAS URL would explode (which it mustn't be
        # called at all for a ``ka-`` row).
        sp_ws = _sp_ws()
        ka_detail = _fake_ka_detail_response(
            tile_id="tile-ka",
            endpoint_name="ka-ee893c47-endpoint",
        )

        def _do(method: str, path: str, body: Any = None) -> Any:
            if method == "GET" and path.startswith(
                "/api/2.0/knowledge-assistants/"
            ):
                return ka_detail
            if method == "GET" and path.startswith(
                "/api/2.0/multi-agent-supervisors/"
            ):
                raise RuntimeError(
                    "Tile type config is not of type MasConfig"
                )
            return {}

        sp_ws.api_client.do.side_effect = _do

        ep = MagicMock()
        ep.description = "legacy ka description"
        ep.task = "agent/v1/chat"
        ep.config = None
        sp_ws.serving_endpoints.get.return_value = ep

        result = rescan_mas_ka_metadata(None, sp_ws, session)

        assert result.refreshed == 1, (
            f"KA row should refresh cleanly with the KA detail API: {result.rows}"
        )
        assert result.failed == 0
        assert "policy_operations_agent" in (result.rows[0].message or "")

        # The KA URL must be the first (preferred) detail hit; MAS URL
        # should never fire for a ``ka-`` endpoint.
        paths_called = [
            c.args[1] for c in sp_ws.api_client.do.call_args_list
            if c.args[0] == "GET"
            and c.args[1].startswith(
                ("/api/2.0/knowledge-assistants/",
                 "/api/2.0/multi-agent-supervisors/")
            )
        ]
        assert any(
            p.startswith("/api/2.0/knowledge-assistants/") for p in paths_called
        ), f"KA detail URL must be called: {paths_called}"
        assert not any(
            p.startswith("/api/2.0/multi-agent-supervisors/") for p in paths_called
        ), f"MAS detail URL must not be called for a ka- row: {paths_called}"

    def test_obo_used_for_serving_endpoint_lookup(self) -> None:
        """Grant-catalog-access only writes the tile ACL; the SP may still
        lack View on the serving endpoint. Rescan must therefore try the
        admin's OBO first for ``serving_endpoints.get``. Regression test
        for the 2026-04-27 bug where all three newly-granted tiles logged
        ``User does not have permission 'View' on Endpoint …`` because
        rescan was calling SP-only.
        """
        session = FakeSession([
            FakeResult([_mas_row("mas-abc-endpoint", tile_id="tile-abc")]),
            FakeResult([("", "", AgentType.MAS.value, "{}")]),
        ])
        sp_ws = _sp_ws_for_rescan(_fake_tile_detail_response())

        # User OBO client succeeds on serving_endpoints.get (admin has
        # View on any tile they manage), but OBO intentionally fails on
        # ``multi-agent-supervisors`` because that endpoint requires the
        # ``all-apis`` scope which Databricks Apps cannot carry. The SP
        # call against that detail endpoint must still succeed via the
        # tile ACL grant.
        user_ws = MagicMock()
        ep = MagicMock()
        ep.description = "obo-served endpoint"
        ep.task = "agent/v1/chat"
        ep.config = None
        user_ws.serving_endpoints.get.return_value = ep
        user_ws.api_client.do.side_effect = RuntimeError(
            "Provided OAuth token does not have required scopes: all-apis"
        )
        # The SP would raise View on the serving endpoint if we hit it;
        # rescan must use the OBO client first to avoid that path.
        sp_ws.serving_endpoints.get.side_effect = RuntimeError(
            "User does not have permission 'View' on Endpoint "
            "mas-abc-endpoint."
        )

        result = rescan_mas_ka_metadata(user_ws, sp_ws, session)

        assert result.failed == 0, (
            f"OBO-first path must succeed even when SP lacks View: {result.rows}"
        )
        assert user_ws.serving_endpoints.get.called, (
            "Rescan must call serving_endpoints.get on the OBO client first"
        )
