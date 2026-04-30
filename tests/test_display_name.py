"""Display-name derivation precedence: tile > UC model > prettified endpoint.

Guards the fallback chain introduced to stop raw endpoint names like
``mas-94fa1c3b-endpoint`` from leaking into the catalog UI when the Agent
Bricks Tiles API is unreachable (F3 platform gap).
"""

from __future__ import annotations

from scgp_agent_hub.backend.services.catalog_service import (
    _derive_display_name,
    _smart_title,
)


class TestSmartTitle:
    def test_capitalizes_plain_words(self) -> None:
        assert _smart_title("my supply chain copilot") == "My Supply Chain Copilot"

    def test_preserves_hex_tokens(self) -> None:
        # Hex IDs look nonsensical under str.title() (``94Fa1C3B``). The
        # helper preserves them as-is so ``Mas 94fa1c3b`` stays readable.
        assert _smart_title("mas 94fa1c3b") == "Mas 94fa1c3b"

    def test_capitalizes_alpha_only_words(self) -> None:
        assert _smart_title("ka customer support") == "Ka Customer Support"

    def test_empty_string(self) -> None:
        assert _smart_title("") == ""


class TestTilePrecedence:
    def test_tile_wins_over_uc_model(self) -> None:
        # Tile is the canonical UI label -- when present, nothing else
        # should override it (even a prettier-looking UC model name).
        got = _derive_display_name(
            endpoint_name="mas-94fa1c3b-endpoint",
            tile={"name": "My Supervisor Agent"},
            uc_model_name="main.default.ma_my_agent",
        )
        assert got == "My Supervisor Agent"

    def test_tile_empty_name_falls_through(self) -> None:
        # A tile with no ``name`` isn't useful; fall through to UC model.
        got = _derive_display_name(
            endpoint_name="mas-94fa1c3b-endpoint",
            tile={"name": ""},
            uc_model_name="main.default.ma_real_name",
        )
        assert got == "Ma Real Name"


class TestUcModelFallback:
    def test_strips_catalog_and_titlecases(self) -> None:
        got = _derive_display_name(
            endpoint_name="ignored-name-endpoint",
            tile=None,
            uc_model_name="main.default.ma_supply_chain_copilot",
        )
        assert got == "Ma Supply Chain Copilot"

    def test_handles_single_segment(self) -> None:
        # No catalog prefix; still prettified.
        got = _derive_display_name("x", None, "customer_service_bot")
        assert got == "Customer Service Bot"

    def test_empty_tail_falls_through_to_endpoint_prettify(self) -> None:
        # Trailing dot -> empty tail -> fall through to prettifier.
        got = _derive_display_name("my-endpoint", None, "main.default.")
        # Empty uc tail means we prettify the endpoint name instead.
        assert got == "My"


class TestExtendedTileKeys:
    """Newer precedence: also accept ``display_name`` and ``title``.

    The multi-agent-supervisors detail API returns the tile under
    ``multi_agent_supervisor.tile.name`` (which we already supported),
    but other tile projections surfaced ``display_name``/``title``.
    The key lookup must accept all three and tolerate nesting under a
    ``metadata`` dict.
    """

    def test_display_name_key(self) -> None:
        got = _derive_display_name(
            "mas-abc-endpoint",
            {"display_name": "Human Label"},
            None,
        )
        assert got == "Human Label"

    def test_title_key(self) -> None:
        got = _derive_display_name(
            "mas-abc-endpoint",
            {"title": "Alt Title"},
            None,
        )
        assert got == "Alt Title"

    def test_name_precedence_over_others(self) -> None:
        got = _derive_display_name(
            "x",
            {
                "name": "Canonical",
                "display_name": "Secondary",
                "title": "Tertiary",
            },
            None,
        )
        assert got == "Canonical"

    def test_metadata_nested_name(self) -> None:
        got = _derive_display_name(
            "x",
            {"metadata": {"display_name": "Nested Display"}},
            None,
        )
        assert got == "Nested Display"


class TestEndpointPrettify:
    def test_mas_hex_id_preserved(self) -> None:
        # Exactly the case the user reported: ``mas-94fa1c3b-endpoint``
        # should never leak into the UI verbatim.
        assert _derive_display_name("mas-94fa1c3b-endpoint", None, None) == "Mas 94fa1c3b"

    def test_ka_prefix_visible(self) -> None:
        assert _derive_display_name("ka-customer-support", None, None) == "Ka Customer Support"

    def test_agent_prefix_visible(self) -> None:
        assert (
            _derive_display_name("agent-my-helper-endpoint", None, None)
            == "Agent My Helper"
        )

    def test_no_prefix_still_prettified(self) -> None:
        assert _derive_display_name("my_custom_model", None, None) == "My Custom Model"

    def test_empty_endpoint_returns_empty(self) -> None:
        assert _derive_display_name("", None, None) == ""
