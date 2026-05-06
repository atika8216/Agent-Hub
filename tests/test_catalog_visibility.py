"""Default catalog visibility rules.

Regression guard for the 2026-04-17 fix that promoted Genie Spaces from
default-hidden to default-visible. The original rule hid 19/20 Genie
Spaces that OBO had legitimately surfaced; the fix brings the catalog
defaults in line with user expectations while keeping plain models
hidden (since they are building blocks, not agents).
"""

from __future__ import annotations

from agent_hub.backend.models import AgentType
from agent_hub.backend.services.catalog_service import _default_visible_for


class TestDefaultVisibility:
    def test_agent_surfaces_are_visible(self) -> None:
        # These are the seven types a user can actually chat with in the UI.
        # If any of these flips to hidden, the catalog will silently drop
        # real agents on the next discovery run. HTTP / MCP are first-class
        # agents as of the Phase 1 UC-tag expansion -- admins opt objects in
        # by tagging them in Unity Catalog, so a second per-row opt-in gate
        # would just duplicate that decision.
        for t in (
            AgentType.MAS,
            AgentType.AGENT,
            AgentType.KA,
            AgentType.EXTERNAL,
            AgentType.GENIE_SPACE,
            AgentType.HTTP_CONNECTION,
            AgentType.MCP_ENDPOINT,
        ):
            assert _default_visible_for(t) is True, f"{t} should be default-visible"

    def test_plain_model_is_hidden(self) -> None:
        # Plain served models / embedding endpoints aren't agents; admin
        # opts them in per-workspace via /admin/catalog.
        assert _default_visible_for(AgentType.MODEL) is False
