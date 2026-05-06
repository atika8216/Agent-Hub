"""``GET /app/config`` — public frontend flags.

The endpoint is the wire-level contract for the Phase 3 ``AGENT_HUB_LEGACY_UI``
rollback lever. ThemeProvider fetches it before hydrating, so a regression
here would prevent operators from disengaging the Clarity redesign without
a code revert.

Phase 4 extended the response with the resolved Phase-4 feature flags
(``ai_suggestions``, ``charts``, ``pinned``). The legacy assertions still
own the ``legacy_ui`` contract; we add a small section at the bottom to
cover the feature-flag wiring.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

from agent_hub.backend.router import get_app_config


def _fake_request(*, engine: Any = None, workspace_client: Any = None) -> Any:
    """Build a minimal duck-typed ``Request``.

    Just enough surface for ``get_app_config`` to do its work without
    pulling in starlette / a TestClient. We deliberately don't populate
    ``headers`` or a ``workspace_client`` so the OBO resolution path
    falls through to "anonymous" via the try/except in
    ``_resolve_user_email``.
    """
    app = SimpleNamespace(
        state=SimpleNamespace(
            engine=engine,
            workspace_client=workspace_client,
        )
    )
    return SimpleNamespace(app=app, headers={})


class TestAppConfigLegacyUI:
    def test_default_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Unset the flag explicitly so we're not affected by the shell env.
        monkeypatch.delenv("AGENT_HUB_LEGACY_UI", raising=False)
        out = get_app_config(_fake_request())
        assert out.legacy_ui is False

    def test_flag_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_HUB_LEGACY_UI", "1")
        out = get_app_config(_fake_request())
        assert out.legacy_ui is True

    @pytest.mark.parametrize("raw", ["", "0", "false", "no", "yes", " 1 "])
    def test_only_exact_1_activates(
        self,
        monkeypatch: pytest.MonkeyPatch,
        raw: str,
    ) -> None:
        # We require the literal string ``"1"`` (after stripping) so operators
        # cannot accidentally trip the flag with a truthy-looking value.
        monkeypatch.setenv("AGENT_HUB_LEGACY_UI", raw)
        out = get_app_config(_fake_request())
        expected = raw.strip() == "1"
        assert out.legacy_ui is expected

    def test_surfaces_on_env_not_on_module_import(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The env is read at request time, so toggling it after the module
        # imports must take effect on the next call without reload.
        monkeypatch.delenv("AGENT_HUB_LEGACY_UI", raising=False)
        assert get_app_config(_fake_request()).legacy_ui is False
        monkeypatch.setenv("AGENT_HUB_LEGACY_UI", "1")
        assert get_app_config(_fake_request()).legacy_ui is True
        monkeypatch.delenv("AGENT_HUB_LEGACY_UI")
        assert get_app_config(_fake_request()).legacy_ui is False

    def test_not_affected_by_other_flags(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Make sure the Phase 2 kill switch doesn't leak into Phase 3.
        monkeypatch.setenv("AGENT_HUB_DISABLE_UC_MCP_CHAT", "1")
        monkeypatch.delenv("AGENT_HUB_LEGACY_UI", raising=False)
        assert get_app_config(_fake_request()).legacy_ui is False


class TestAppConfigFeatureFlags:
    """Phase 4 feature-flag fields surfaced on ``/app/config``.

    The frontend reads ``feature_flags`` to decide whether to render the
    suggestion chips, chart cards, and pinned-question rail. When
    Lakebase isn't reachable the resolver must fall back to "all off"
    rather than 500ing the cold-boot path.
    """

    def test_no_engine_returns_all_off_defaults(self) -> None:
        # No DB engine -> safe defaults across all three features.
        out = get_app_config(_fake_request(engine=None))
        for flag in (
            out.feature_flags.ai_suggestions,
            out.feature_flags.charts,
            out.feature_flags.pinned,
        ) if out.feature_flags else ():
            assert flag.master_on is False
            assert flag.effective_on is False


def test_module_has_no_import_time_env_read() -> None:
    # Guard: if anyone inlines ``os.environ.get("AGENT_HUB_LEGACY_UI")`` at module
    # top level the flag would be frozen at import time and rollback would
    # require a full app restart. The test fails fast in that case.
    import importlib
    import agent_hub.backend.router as router

    os.environ.pop("AGENT_HUB_LEGACY_UI", None)
    importlib.reload(router)
    os.environ["AGENT_HUB_LEGACY_UI"] = "1"
    try:
        # The freshly reloaded module should still observe the new value.
        assert router.get_app_config(_fake_request()).legacy_ui is True
    finally:
        os.environ.pop("AGENT_HUB_LEGACY_UI", None)
