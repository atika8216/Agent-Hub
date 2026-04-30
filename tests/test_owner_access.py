"""Owner-email fallback for ``has_access``.

Guards the conservative fallback introduced on 2026-04-17 after prod owners
were locked out of their own agents whenever ``serving_endpoints.get`` via
OBO failed (usually because of stale consent / 970-byte tokens). The
fallback only fires when the OBO probe actually fails -- it never inflates
access for non-owners. See docs/obo-auth-design.md §14.
"""

from __future__ import annotations

from scgp_agent_hub.backend.services.catalog_service import _owner_has_access


class TestOwnerHasAccess:
    def test_exact_match(self) -> None:
        assert _owner_has_access("user@example.com", "user@example.com") is True

    def test_case_insensitive(self) -> None:
        # Databricks proxy chain normalizes casing inconsistently; owners
        # shouldn't be locked out over a character case difference.
        assert _owner_has_access("User@Example.COM", "user@example.com") is True

    def test_whitespace_tolerant(self) -> None:
        assert _owner_has_access("  user@example.com  ", "user@example.com") is True

    def test_non_owner_is_rejected(self) -> None:
        assert _owner_has_access("stranger@example.com", "user@example.com") is False

    def test_empty_user_email(self) -> None:
        # Unauthenticated callers never match -- don't leak owner access
        # to missing / anonymous identity.
        assert _owner_has_access("", "user@example.com") is False
        assert _owner_has_access(None, "user@example.com") is False

    def test_empty_owner_email(self) -> None:
        # Rows without a recorded owner shouldn't grant access to anyone.
        assert _owner_has_access("user@example.com", "") is False
        assert _owner_has_access("user@example.com", None) is False

    def test_both_empty(self) -> None:
        assert _owner_has_access("", "") is False
        assert _owner_has_access(None, None) is False
