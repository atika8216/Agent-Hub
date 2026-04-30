"""Unit tests for the OBO scope helpers added in the close-obo-gaps rollout.

Covers:
- ``catalog_service._extract_required_scope`` (P2b — greppable logging of
  the scope the tiles API wants)
- ``debug_service._jwt_payload`` / ``_scopes_from_claim`` (P2a — token
  introspection without secrets leaking)

These are pure-function tests; no Databricks SDK, no network.
"""

from __future__ import annotations

import base64
import json

from scgp_agent_hub.backend.services.catalog_service import _extract_required_scope
from scgp_agent_hub.backend.services.debug_service import (
    _jwt_payload,
    _scopes_from_claim,
)


# --------------------------------------------------------------------------- #
# _extract_required_scope
# --------------------------------------------------------------------------- #

class TestExtractRequiredScope:
    def test_extracts_from_standard_databricks_message(self) -> None:
        msg = "Provided OAuth token does not have required scopes: tiles.manage"
        assert _extract_required_scope(msg) == "tiles.manage"

    def test_extracts_multi_scope_list(self) -> None:
        msg = "403 required scopes: scope-a, scope-b, scope-c"
        # We keep the full list; callers can split if needed.
        assert _extract_required_scope(msg) == "scope-a, scope-b, scope-c"

    def test_extracts_singular_form(self) -> None:
        msg = "Error: required scope: serving.serving-endpoints"
        assert _extract_required_scope(msg) == "serving.serving-endpoints"

    def test_is_case_insensitive(self) -> None:
        msg = "Required Scopes: Foo.Bar"
        assert _extract_required_scope(msg) == "Foo.Bar"

    def test_stops_at_period_or_pipe(self) -> None:
        # Some SDK errors include a trailing hint after a period or pipe.
        msg = "required scopes: foo.bar | other stuff"
        assert _extract_required_scope(msg) == "foo.bar"

    def test_trims_quotes(self) -> None:
        msg = "required scopes: 'foo.bar'"
        assert _extract_required_scope(msg) == "foo.bar"

    def test_returns_none_when_no_scope_hint(self) -> None:
        msg = "Some unrelated error"
        assert _extract_required_scope(msg) is None

    def test_returns_none_on_empty(self) -> None:
        assert _extract_required_scope("") is None


# --------------------------------------------------------------------------- #
# _jwt_payload / _scopes_from_claim
# --------------------------------------------------------------------------- #

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_jwt(payload: dict) -> str:
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = _b64url(json.dumps(payload).encode())
    signature = "deadbeef"
    return f"{header}.{body}.{signature}"


class TestJwtPayload:
    def test_decodes_space_separated_scopes(self) -> None:
        token = _make_jwt({"scope": "serving.serving-endpoints sql"})
        payload = _jwt_payload(token)
        assert payload is not None
        assert _scopes_from_claim(payload) == [
            "serving.serving-endpoints",
            "sql",
        ]

    def test_decodes_list_scp_claim(self) -> None:
        token = _make_jwt({"scp": ["a", "b", "c"]})
        payload = _jwt_payload(token)
        assert payload is not None
        assert _scopes_from_claim(payload) == ["a", "b", "c"]

    def test_decodes_comma_separated(self) -> None:
        token = _make_jwt({"scope": "a, b , c"})
        payload = _jwt_payload(token)
        assert payload is not None
        assert _scopes_from_claim(payload) == ["a", "b", "c"]

    def test_returns_none_for_opaque_token(self) -> None:
        assert _jwt_payload("opaque_pat_token") is None

    def test_returns_none_for_malformed_base64(self) -> None:
        assert _jwt_payload("not.valid.jwt") is None

    def test_empty_scope_claim(self) -> None:
        token = _make_jwt({"scope": ""})
        payload = _jwt_payload(token)
        assert payload is not None
        assert _scopes_from_claim(payload) == []

    def test_missing_scope_claim_returns_empty_list(self) -> None:
        token = _make_jwt({"sub": "user@example.com"})
        payload = _jwt_payload(token)
        assert payload is not None
        assert _scopes_from_claim(payload) == []
