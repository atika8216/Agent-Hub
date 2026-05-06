"""Unit tests for catalog_service helpers (sub-component access probes)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_hub.backend.models import SubComponentType
from agent_hub.backend.services import catalog_service as cs


class TestCoerceSubComponentType:
    def test_value_string(self) -> None:
        assert cs._coerce_sub_component_type("knowledge_assistant") is (
            SubComponentType.KNOWLEDGE_ASSISTANT
        )

    def test_enum_member_name_string(self) -> None:
        assert cs._coerce_sub_component_type("KNOWLEDGE_ASSISTANT") is (
            SubComponentType.KNOWLEDGE_ASSISTANT
        )

    def test_hyphenated_hint(self) -> None:
        assert cs._coerce_sub_component_type("genie-space") is SubComponentType.GENIE_SPACE

    def test_title_case_with_spaces(self) -> None:
        assert cs._coerce_sub_component_type("Knowledge Assistant") is (
            SubComponentType.KNOWLEDGE_ASSISTANT
        )
        assert cs._coerce_sub_component_type("Genie Space") is SubComponentType.GENIE_SPACE


def _ws_mock() -> MagicMock:
    return MagicMock()


class TestComponentHasAccess:
    def test_ka_with_ref_obo_succeeds(self) -> None:
        ws = _ws_mock()
        comp = {
            "name": "policy_agent",
            "type": SubComponentType.KNOWLEDGE_ASSISTANT.value,
            "endpoint_ref": "ka-endpoint-1",
        }
        assert cs._component_has_access(ws, comp) is True
        ws.serving_endpoints.get.assert_called_once_with("ka-endpoint-1")

    def test_ka_type_member_name_probes_endpoint_ref(self) -> None:
        ws = _ws_mock()
        comp = {
            "name": "policy_agent",
            "type": "KNOWLEDGE_ASSISTANT",
            "endpoint_ref": "ka-endpoint-2",
        }
        assert cs._component_has_access(ws, comp) is True
        ws.serving_endpoints.get.assert_called_once_with("ka-endpoint-2")

    def test_ka_with_ref_obo_403(self) -> None:
        ws = _ws_mock()
        ws.serving_endpoints.get.side_effect = Exception("403 Forbidden")

        comp = {
            "name": "policy_agent",
            "type": SubComponentType.KNOWLEDGE_ASSISTANT.value,
            "endpoint_ref": "ka-endpoint-1",
        }
        assert cs._component_has_access(ws, comp) is False

    def test_ka_with_ref_obo_scope_error_optimistic(self) -> None:
        ws = _ws_mock()
        ws.serving_endpoints.get.side_effect = Exception(
            "Provided OAuth token does not have required scopes: foo"
        )

        comp = {
            "name": "policy_agent",
            "type": SubComponentType.KNOWLEDGE_ASSISTANT.value,
            "endpoint_ref": "ka-endpoint-1",
        }
        assert cs._component_has_access(ws, comp) is True

    def test_ka_without_endpoint_ref_optimistic(self) -> None:
        ws = _ws_mock()
        comp = {
            "name": "policy_agent",
            "type": SubComponentType.KNOWLEDGE_ASSISTANT.value,
            "endpoint_ref": "",
        }
        assert cs._component_has_access(ws, comp) is True
        ws.serving_endpoints.get.assert_not_called()

    def test_genie_with_ref_probe_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cs, "_genie_has_access", lambda _ws, _sid: True)
        ws = _ws_mock()
        comp = {
            "name": "analytics_agent",
            "type": SubComponentType.GENIE_SPACE.value,
            "endpoint_ref": "space-uuid-1",
        }
        assert cs._component_has_access(ws, comp) is True

    def test_genie_with_ref_probe_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cs, "_genie_has_access", lambda _ws, _sid: False)
        ws = _ws_mock()
        comp = {
            "name": "analytics_agent",
            "type": SubComponentType.GENIE_SPACE.value,
            "endpoint_ref": "space-uuid-1",
        }
        assert cs._component_has_access(ws, comp) is False

    def test_genie_probe_none_optimistic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cs, "_genie_has_access", lambda _ws, _sid: None)
        ws = _ws_mock()
        comp = {
            "name": "analytics_agent",
            "type": SubComponentType.GENIE_SPACE.value,
            "endpoint_ref": "space-uuid-1",
        }
        assert cs._component_has_access(ws, comp) is True

    def test_genie_falls_back_to_space_id_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        def _capture(_ws: MagicMock, sid: str) -> bool:
            seen.append(sid)
            return True

        monkeypatch.setattr(cs, "_genie_has_access", _capture)
        ws = _ws_mock()
        comp = {
            "name": "analytics_agent",
            "type": SubComponentType.GENIE_SPACE.value,
            "endpoint_ref": "",
            "space_id": "from-metadata",
        }
        assert cs._component_has_access(ws, comp) is True
        assert seen == ["from-metadata"]

    def test_external_mcp_optimistic(self) -> None:
        ws = _ws_mock()
        comp = {
            "name": "tools_agent",
            "type": SubComponentType.EXTERNAL_MCP.value,
            "endpoint_ref": "",
        }
        assert cs._component_has_access(ws, comp) is True

    def test_served_model_still_true_on_success(self) -> None:
        ws = _ws_mock()
        comp = {
            "name": "my-model-endpoint",
            "type": SubComponentType.SERVED_MODEL.value,
        }
        assert cs._component_has_access(ws, comp) is True
        ws.serving_endpoints.get.assert_called_once_with("my-model-endpoint")

    def test_uc_function_scope_error_optimistic(self) -> None:
        ws = _ws_mock()
        ws.functions.get.side_effect = Exception("invalid scope for caller")

        comp = {
            "name": "catalog.schema.fn",
            "type": SubComponentType.UC_FUNCTION.value,
        }
        assert cs._component_has_access(ws, comp) is True

    def test_vector_search_403_false(self) -> None:
        ws = _ws_mock()
        ws.vector_search_indexes.get_index.side_effect = Exception("403 denied")

        comp = {
            "name": "idx_name",
            "type": SubComponentType.VECTOR_SEARCH.value,
        }
        assert cs._component_has_access(ws, comp) is False


# --------------------------------------------------------------------------- #
# Transitive access: parent MAS access implies sub-component access
# --------------------------------------------------------------------------- #


class _Row(tuple):
    """Tuple subclass mirroring ``sqlmodel.Row`` for session-mock tests."""


def _mock_session_returning(row: tuple | None) -> MagicMock:
    sess = MagicMock()
    result = MagicMock()
    result.one_or_none.return_value = row
    result.all.return_value = [row] if row is not None else []
    sess.exec.return_value = result
    return sess


class TestCheckAccessTransitive:
    """When the parent MAS grants access, every sub-component must report
    True -- MAS orchestrators forward to children via their own service
    principal, so the caller's direct ACL on the child surface is
    irrelevant. The UI must not paint 'Request' chips on components the
    user can already invoke through the MAS.
    """

    def _metadata_with_subs(self) -> dict[str, list[dict[str, str]]]:
        return {
            "sub_agents": [
                {
                    "name": "policy_operations_agent",
                    "type": SubComponentType.KNOWLEDGE_ASSISTANT.value,
                    "endpoint_ref": "ka-endpoint-1",
                },
                {
                    "name": "analytics_agent",
                    "type": SubComponentType.GENIE_SPACE.value,
                    "endpoint_ref": "space-uuid-1",
                },
                {
                    "name": "thailand_news_agent",
                    "type": SubComponentType.UC_FUNCTION.value,
                },
            ]
        }

    def test_parent_access_grants_transitive_access_to_all_subs(self) -> None:
        row = _Row(
            (
                self._metadata_with_subs(),
                "owner@example.com",
            )
        )
        sess = _mock_session_returning(row)

        ws = MagicMock()
        # Parent MAS probe succeeds -> user has CAN_QUERY on the MAS.
        ws.serving_endpoints.get.return_value = MagicMock()

        access = cs.check_access(
            endpoint_name="mas-parent-endpoint",
            user_ws=ws,
            session=sess,
            user_email="caller@example.com",
        )

        assert access.has_access is True
        # Every sub-component -- even ones whose direct OBO probe would
        # have returned 403 -- must be marked accessible.
        assert access.sub_agent_access == {
            "policy_operations_agent": True,
            "analytics_agent": True,
            "thailand_news_agent": True,
        }

    def test_parent_access_skips_per_sub_probes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the parent grants access, we must not call
        ``_component_has_access`` at all -- it's both wasted latency and
        the probe has historically produced false negatives.
        """
        row = _Row(
            (
                self._metadata_with_subs(),
                "owner@example.com",
            )
        )
        sess = _mock_session_returning(row)

        ws = MagicMock()
        ws.serving_endpoints.get.return_value = MagicMock()

        call_count = {"n": 0}

        def _spy(*_args: object, **_kwargs: object) -> bool:
            call_count["n"] += 1
            return False

        monkeypatch.setattr(cs, "_component_has_access", _spy)

        access = cs.check_access(
            endpoint_name="mas-parent-endpoint",
            user_ws=ws,
            session=sess,
            user_email="caller@example.com",
        )

        assert access.has_access is True
        assert call_count["n"] == 0
        assert all(v is True for v in access.sub_agent_access.values())

    def test_no_parent_access_falls_back_to_per_sub_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the user cannot access the parent MAS, we still want the
        per-sub probe to run so partial-access edge cases (e.g. an owner
        of one specific child) surface correctly.
        """
        row = _Row(
            (
                self._metadata_with_subs(),
                "owner@example.com",
            )
        )
        sess = _mock_session_returning(row)

        ws = MagicMock()
        ws.serving_endpoints.get.side_effect = Exception("403 Forbidden")

        per_sub: dict[str, bool] = {
            "policy_operations_agent": False,
            "analytics_agent": False,
            "thailand_news_agent": True,
        }

        def _probe(_ws: object, comp: dict[str, object]) -> bool:
            return per_sub[str(comp["name"])]

        monkeypatch.setattr(cs, "_component_has_access", _probe)

        access = cs.check_access(
            endpoint_name="mas-parent-endpoint",
            user_ws=ws,
            session=sess,
            user_email="caller@example.com",
        )

        assert access.has_access is False
        assert access.sub_agent_access == per_sub


class TestBuildSubAgentInfosTransitive:
    def test_parent_has_access_true_marks_all_subs_accessible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The parent-access signal must short-circuit the per-sub probe
        so KA / Genie rows render green when the MAS is accessible.
        """
        call_count = {"n": 0}

        def _spy(*_args: object, **_kwargs: object) -> bool:
            call_count["n"] += 1
            return False

        monkeypatch.setattr(cs, "_component_has_access", _spy)

        meta = {
            "sub_agents": [
                {
                    "name": "policy_operations_agent",
                    "type": SubComponentType.KNOWLEDGE_ASSISTANT.value,
                    "endpoint_ref": "ka-endpoint-1",
                },
                {
                    "name": "analytics_agent",
                    "type": SubComponentType.GENIE_SPACE.value,
                    "endpoint_ref": "space-uuid-1",
                },
            ]
        }

        infos = cs._build_sub_agent_infos(
            endpoint_name="mas-parent-endpoint",
            ws=MagicMock(),
            cached_meta=meta,
            sp_ws=None,
            parent_has_access=True,
        )

        assert [i.has_access for i in infos] == [True, True]
        assert call_count["n"] == 0

    def test_parent_has_access_false_falls_back_to_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        def _probe(_ws: object, comp: dict[str, object]) -> bool:
            seen.append(str(comp["name"]))
            return str(comp["name"]) == "thailand_news_agent"

        monkeypatch.setattr(cs, "_component_has_access", _probe)

        meta = {
            "sub_agents": [
                {
                    "name": "policy_operations_agent",
                    "type": SubComponentType.KNOWLEDGE_ASSISTANT.value,
                    "endpoint_ref": "ka-endpoint-1",
                },
                {
                    "name": "thailand_news_agent",
                    "type": SubComponentType.UC_FUNCTION.value,
                },
            ]
        }

        infos = cs._build_sub_agent_infos(
            endpoint_name="mas-parent-endpoint",
            ws=MagicMock(),
            cached_meta=meta,
            sp_ws=None,
            parent_has_access=False,
        )

        assert [i.has_access for i in infos] == [False, True]
        assert seen == ["policy_operations_agent", "thailand_news_agent"]

    def test_default_behavior_unchanged_when_parent_not_provided(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Old callers that don't pass ``parent_has_access`` must keep
        probing per-sub -- preserves backwards-compatible semantics for
        any integration path we haven't re-threaded.
        """
        seen: list[str] = []

        def _probe(_ws: object, comp: dict[str, object]) -> bool:
            seen.append(str(comp["name"]))
            return True

        monkeypatch.setattr(cs, "_component_has_access", _probe)

        meta = {
            "sub_agents": [
                {
                    "name": "x",
                    "type": SubComponentType.KNOWLEDGE_ASSISTANT.value,
                    "endpoint_ref": "ka-x",
                }
            ]
        }

        infos = cs._build_sub_agent_infos(
            endpoint_name="mas-parent-endpoint",
            ws=MagicMock(),
            cached_meta=meta,
            sp_ws=None,
        )

        assert [i.has_access for i in infos] == [True]
        assert seen == ["x"]
