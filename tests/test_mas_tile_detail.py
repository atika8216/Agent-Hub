"""MAS tile-detail enrichment: display name, description, and sub-agent graph.

Exercises the ``/api/2.0/multi-agent-supervisors/{tile_id}`` path we added
to close three gaps the Phase 3 redesign surfaced:

1. MAS rows like ``mas-93f96edd-endpoint`` rendered as ``Mas 93f96edd``
   because the list-tile projection didn't carry the human name.
2. ``metadata_json.sub_agents`` was always empty because the MAS
   instruction-regex parser never matched real Agent Bricks layouts.
3. ``get_agent_detail`` served stale Lakebase rows without ever refreshing.

The fixture in ``tests/fixtures/rest_multi_agent_supervisor.json`` was
captured live from the PTTOR Ecosystem Intelligence MAS in
``fevm-aan-demo``. Any regression in the helper shape will break these
tests before it ships.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent_hub.backend.models import SubComponentType
from agent_hub.backend.services import catalog_service
from agent_hub.backend.services.catalog_service import (
    _derive_description,
    _derive_display_name,
    _invalidate_tile_detail_cache,
    _load_tile_detail,
    _looks_like_fallback_display_name,
    _sub_agents_from_detail,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "rest_multi_agent_supervisor.json"


@pytest.fixture
def mas_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture(autouse=True)
def _clear_detail_cache() -> None:
    """Stop cache state from one test from leaking into the next."""
    _invalidate_tile_detail_cache()
    yield
    _invalidate_tile_detail_cache()


# --------------------------------------------------------------------------- #
# _derive_display_name key precedence
# --------------------------------------------------------------------------- #


class TestDisplayNameKeyPrecedence:
    def test_detail_name_key_wins(self) -> None:
        got = _derive_display_name(
            "mas-93f96edd-endpoint",
            {"name": "PTTOR_Ecosystem_Intelligence"},
            None,
        )
        assert got == "PTTOR_Ecosystem_Intelligence"

    def test_display_name_key_works(self) -> None:
        got = _derive_display_name(
            "mas-abc-endpoint",
            {"display_name": "Friendly Name"},
            None,
        )
        assert got == "Friendly Name"

    def test_title_key_works(self) -> None:
        got = _derive_display_name(
            "mas-abc-endpoint",
            {"title": "Another Title"},
            None,
        )
        assert got == "Another Title"

    def test_nested_metadata_name_works(self) -> None:
        got = _derive_display_name(
            "mas-abc-endpoint",
            {"metadata": {"name": "Nested Name"}},
            None,
        )
        assert got == "Nested Name"

    def test_name_wins_over_display_name(self) -> None:
        got = _derive_display_name(
            "x",
            {"name": "Primary", "display_name": "Secondary", "title": "Tertiary"},
            None,
        )
        assert got == "Primary"

    def test_blank_name_falls_through_to_uc_model(self) -> None:
        got = _derive_display_name(
            "mas-abc-endpoint",
            {"name": "   ", "display_name": ""},
            "main.default.ma_my_agent",
        )
        assert got == "Ma My Agent"


# --------------------------------------------------------------------------- #
# _derive_description
# --------------------------------------------------------------------------- #


class TestDeriveDescription:
    def test_tile_description_wins(self) -> None:
        tile = {"description": "Rich description from Agent Bricks"}
        ep = MagicMock(description="ep short")
        assert _derive_description(tile, ep) == "Rich description from Agent Bricks"

    def test_ep_description_fallback(self) -> None:
        ep = MagicMock(description="ep short")
        assert _derive_description({}, ep) == "ep short"

    def test_summary_key_supported(self) -> None:
        assert _derive_description({"summary": "via summary"}, None) == "via summary"

    def test_empty_everywhere(self) -> None:
        ep = MagicMock(description=None)
        assert _derive_description(None, ep) == ""


# --------------------------------------------------------------------------- #
# _looks_like_fallback_display_name
# --------------------------------------------------------------------------- #


class TestFallbackDetector:
    def test_detects_prettified_hex_endpoint(self) -> None:
        assert _looks_like_fallback_display_name(
            "Mas 93f96edd", "mas-93f96edd-endpoint",
        )

    def test_rejects_real_display_name(self) -> None:
        assert not _looks_like_fallback_display_name(
            "PTTOR_Ecosystem_Intelligence", "mas-93f96edd-endpoint",
        )

    def test_empty_counts_as_fallback(self) -> None:
        assert _looks_like_fallback_display_name("", "mas-abc-endpoint")


# --------------------------------------------------------------------------- #
# _sub_agents_from_detail
# --------------------------------------------------------------------------- #


class TestSubAgentsFromDetail:
    def test_maps_three_child_types(self, mas_fixture: dict[str, Any]) -> None:
        """All three sub-agent shapes (KA, Genie, UC function) are mapped."""
        tile_id = mas_fixture["multi_agent_supervisor"]["tile"]["tile_id"]
        detail = {
            "tile_id": tile_id,
            "name": mas_fixture["multi_agent_supervisor"]["tile"]["name"],
            "description": "",
            "instructions": "",
            "sub_agents": mas_fixture["multi_agent_supervisor"]["agents"],
            "endpoint_name": "mas-93f96edd-endpoint",
        }

        subs = _sub_agents_from_detail(detail)

        assert len(subs) == 5
        by_name = {s["name"]: s for s in subs}

        # Knowledge assistant child: endpoint_ref is the KA serving endpoint.
        ka = by_name["policy_operations_agent"]
        assert ka["type"] == SubComponentType.KNOWLEDGE_ASSISTANT.value
        assert ka["endpoint_ref"] == "ka-ee893c47-endpoint"
        assert "PTTOR policy" in ka["description"]

        # Genie space: endpoint_ref is the space id (used by genie: routing).
        genie = by_name["analytics_agent"]
        assert genie["type"] == SubComponentType.GENIE_SPACE.value
        assert genie["endpoint_ref"] == "01f104d3c643122f88ec6fc770caae7a"

        # UC function: endpoint_ref is catalog.schema.name.
        uc = by_name["thailand_news_agent"]
        assert uc["type"] == SubComponentType.UC_FUNCTION.value
        assert uc["endpoint_ref"] == "aan_demo_workspace_catalog.gold.search_thailand_news"

    def test_empty_detail_returns_empty(self) -> None:
        assert _sub_agents_from_detail(None) == []
        assert _sub_agents_from_detail({}) == []
        assert _sub_agents_from_detail({"sub_agents": []}) == []

    def test_unknown_agent_type_falls_back_to_served_model(self) -> None:
        subs = _sub_agents_from_detail({
            "sub_agents": [{"name": "mystery", "agent_type": "something_new"}],
        })
        assert subs == [
            {
                "name": "mystery",
                "type": SubComponentType.SERVED_MODEL.value,
                "description": "",
                "endpoint_ref": "",
            }
        ]


# --------------------------------------------------------------------------- #
# _load_tile_detail (happy path, failure path, TTL cache)
# --------------------------------------------------------------------------- #


def _ws_with_detail(
    response: Any,
    *,
    raises: Exception | None = None,
) -> MagicMock:
    ws = MagicMock()
    if raises is not None:
        ws.api_client.do.side_effect = raises
    else:
        ws.api_client.do.return_value = response
    return ws


class TestLoadTileDetail:
    def test_happy_path_returns_normalized_dict(
        self, mas_fixture: dict[str, Any]
    ) -> None:
        ws = _ws_with_detail(mas_fixture)

        detail = _load_tile_detail(
            ws,
            None,
            tile_id="93f96edd-a638-4d8e-9be9-a3eec93d1209",
            endpoint_name="mas-93f96edd-endpoint",
        )

        assert detail is not None
        assert detail["name"] == "PTTOR_Ecosystem_Intelligence"
        assert detail["description"].startswith(
            "Unified intelligence hub for the PTTOR/OR ecosystem"
        )
        assert len(detail["sub_agents"]) == 5
        ws.api_client.do.assert_called_once_with(
            "GET",
            "/api/2.0/multi-agent-supervisors/93f96edd-a638-4d8e-9be9-a3eec93d1209",
        )

    def test_missing_tile_id_returns_none_without_calling(self) -> None:
        ws = _ws_with_detail({})
        assert _load_tile_detail(
            ws, None, tile_id=None, endpoint_name="mas-xyz-endpoint"
        ) is None
        ws.api_client.do.assert_not_called()

    def test_obo_failure_falls_back_to_sp(
        self, mas_fixture: dict[str, Any]
    ) -> None:
        ws = _ws_with_detail({}, raises=RuntimeError("403 Forbidden"))
        sp = _ws_with_detail(mas_fixture)

        detail = _load_tile_detail(
            ws, sp,
            tile_id="93f96edd-a638-4d8e-9be9-a3eec93d1209",
            endpoint_name="mas-93f96edd-endpoint",
        )

        assert detail is not None
        assert detail["name"] == "PTTOR_Ecosystem_Intelligence"
        ws.api_client.do.assert_called_once()
        sp.api_client.do.assert_called_once()

    def test_all_clients_fail_returns_none(self) -> None:
        ws = _ws_with_detail({}, raises=RuntimeError("403"))
        sp = _ws_with_detail({}, raises=RuntimeError("404"))

        detail = _load_tile_detail(
            ws, sp,
            tile_id="xyz",
            endpoint_name="mas-xyz-endpoint",
        )
        assert detail is None

    def test_ttl_cache_skips_second_call(
        self, mas_fixture: dict[str, Any]
    ) -> None:
        ws = _ws_with_detail(mas_fixture)

        first = _load_tile_detail(
            ws, None,
            tile_id="93f96edd-a638-4d8e-9be9-a3eec93d1209",
            endpoint_name="mas-93f96edd-endpoint",
        )
        second = _load_tile_detail(
            ws, None,
            tile_id="93f96edd-a638-4d8e-9be9-a3eec93d1209",
            endpoint_name="mas-93f96edd-endpoint",
        )

        assert first is second
        ws.api_client.do.assert_called_once()

    def test_invalidate_cache_forces_refresh(
        self, mas_fixture: dict[str, Any]
    ) -> None:
        ws = _ws_with_detail(mas_fixture)
        _load_tile_detail(
            ws, None,
            tile_id="t", endpoint_name="mas-t-endpoint",
        )
        _invalidate_tile_detail_cache("mas-t-endpoint")
        _load_tile_detail(
            ws, None,
            tile_id="t", endpoint_name="mas-t-endpoint",
        )
        assert ws.api_client.do.call_count == 2

    def test_ttl_expiry_allows_refresh(
        self, mas_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ws = _ws_with_detail(mas_fixture)

        t = [1000.0]

        def fake_monotonic() -> float:
            return t[0]

        monkeypatch.setattr(catalog_service.time, "monotonic", fake_monotonic)

        _load_tile_detail(
            ws, None,
            tile_id="t", endpoint_name="mas-t-endpoint",
        )
        # Advance past the 60s TTL window.
        t[0] += 120.0
        _load_tile_detail(
            ws, None,
            tile_id="t", endpoint_name="mas-t-endpoint",
        )
        assert ws.api_client.do.call_count == 2

    def test_force_bypasses_cache(self, mas_fixture: dict[str, Any]) -> None:
        """The admin rescan path passes force=True so each click re-fetches."""
        ws = _ws_with_detail(mas_fixture)

        _load_tile_detail(
            ws, None,
            tile_id="t", endpoint_name="mas-t-endpoint",
        )
        # Without ``force``, this second call would be served from cache
        # (see ``test_ttl_cache_skips_second_call``). With it, we always
        # hit the network so the admin sees fresh Agent Bricks state.
        _load_tile_detail(
            ws, None,
            tile_id="t", endpoint_name="mas-t-endpoint",
            force=True,
        )
        assert ws.api_client.do.call_count == 2
