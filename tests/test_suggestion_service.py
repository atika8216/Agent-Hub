"""Suggestion service -- Genie-native extractor + LLM fallback parser.

Three concerns are critical:

1. **Genie-native extraction** -- the field name has shifted across
   versions (``suggested_follow_ups`` / ``suggested_questions`` /
   nested in ``attachments[].text``). A regression here means we
   silently fall back to the paid LLM path even though Genie already
   handed us free suggestions.

2. **LLM JSON parsing** -- the model output is rarely strict JSON; we
   accept fenced blocks, trailing prose, bullet-list fallbacks. If the
   parser is too strict we ship empty chips; too loose and we surface
   garbage tokens as questions. Parametrize the malformed inputs we've
   seen in production logs.

3. **Cache hit / source preservation** -- the conversation reload path
   reads from ``suggestions_cache`` via :func:`get_cached_with_source`.
   It must return the previously-recorded source so the analytics split
   between Genie-native and LLM stays accurate.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from scgp_agent_hub.backend.services import suggestion_service as ss


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #


class FakeResult:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def one_or_none(self) -> tuple[Any, ...] | None:
        return self._row


class CacheSession:
    """Minimal session that lets us seed a cached row + observe writes."""

    def __init__(self, *, cached_row: tuple[Any, ...] | None = None) -> None:
        self._row = cached_row
        self.executed: list[str] = []
        self.commits = 0

    def exec(self, stmt: Any) -> FakeResult:
        sql = str(getattr(stmt, "text", None) or stmt)
        self.executed.append(sql)
        if "SELECT" in sql.upper():
            return FakeResult(self._row)
        return FakeResult(None)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# Genie-native extractor
# --------------------------------------------------------------------------- #


class TestExtractGenieSuggestions:
    def test_top_level_suggested_follow_ups(self) -> None:
        msg = {"suggested_follow_ups": ["What about Q4?", "Show by region", "Why?"]}
        out = ss.extract_genie_suggestions(msg)
        assert out == ["What about Q4?", "Show by region", "Why?"]

    def test_legacy_suggested_questions(self) -> None:
        msg = {"suggested_questions": ["First?", "Second?"]}
        assert ss.extract_genie_suggestions(msg) == ["First?", "Second?"]

    def test_nested_in_attachment_text(self) -> None:
        # Enterprise spaces sometimes nest suggestions inside a "text"
        # attachment payload -- the extractor must drill in.
        msg = {
            "attachments": [
                {"text": {"suggested_follow_ups": ["Drill into Texas?"]}},
                {"query": {"sql": "SELECT 1"}},
            ]
        }
        assert ss.extract_genie_suggestions(msg) == ["Drill into Texas?"]

    def test_dict_items_with_text_field(self) -> None:
        # Some upstream payloads wrap each suggestion in a dict.
        msg = {
            "suggested_follow_ups": [
                {"text": "Trend over time?"},
                {"question": "By segment?"},
            ]
        }
        assert ss.extract_genie_suggestions(msg) == ["Trend over time?", "By segment?"]

    def test_returns_max_three_dedup_and_clamped(self) -> None:
        msg = {
            "suggested_follow_ups": [
                "A?",
                "a?",  # dedup case-insensitive
                "B?",
                "C?",
                "D?",
                "  ",  # blank skipped
            ]
        }
        # Max of 3 chips even if upstream gave more.
        assert ss.extract_genie_suggestions(msg) == ["A?", "B?", "C?"]

    @pytest.mark.parametrize("payload", [None, {}, {"foo": "bar"}, "string", 42])
    def test_unknown_or_missing_returns_empty(self, payload: Any) -> None:
        assert ss.extract_genie_suggestions(payload) == []

    def test_long_suggestion_is_trimmed(self) -> None:
        # The model occasionally produces a paragraph instead of a
        # question -- we trim to MAX_SUGGESTION_CHARS at a word boundary.
        long = "Why " + ("very " * 80) + "long?"
        msg = {"suggested_follow_ups": [long]}
        out = ss.extract_genie_suggestions(msg)
        assert len(out) == 1
        assert len(out[0]) <= ss.MAX_SUGGESTION_CHARS
        assert out[0].endswith("?")


# --------------------------------------------------------------------------- #
# LLM JSON parser -- this is the malformed-input regression suite.
# --------------------------------------------------------------------------- #


class TestParseLLMOutput:
    def test_strict_json(self) -> None:
        raw = '{"suggestions": ["A?", "B?", "C?"]}'
        assert ss._parse_llm_output(raw) == ["A?", "B?", "C?"]

    def test_fenced_json_block(self) -> None:
        # Models love wrapping in ```json fences despite instructions.
        raw = '```json\n{"suggestions": ["A?", "B?"]}\n```'
        assert ss._parse_llm_output(raw) == ["A?", "B?"]

    def test_unfenced_with_prose_prefix(self) -> None:
        raw = (
            "Sure, here are some good follow-ups:\n"
            '{"suggestions": ["A?", "B?", "C?"]}\n'
            "Hope that helps!"
        )
        # Falls back to "first {...}" extraction.
        assert ss._parse_llm_output(raw) == ["A?", "B?", "C?"]

    def test_alternate_key_follow_ups(self) -> None:
        raw = '{"follow_ups": ["x?", "y?"]}'
        assert ss._parse_llm_output(raw) == ["x?", "y?"]

    def test_alternate_key_questions(self) -> None:
        raw = '{"questions": ["x?"]}'
        assert ss._parse_llm_output(raw) == ["x?"]

    def test_bullet_list_fallback(self) -> None:
        # No JSON at all; model returned a markdown bullet list.
        raw = "Here are three:\n- What about Q4?\n- Drill in by region?\n- Outliers?"
        out = ss._parse_llm_output(raw)
        assert out == ["What about Q4?", "Drill in by region?", "Outliers?"]

    def test_numbered_list_fallback(self) -> None:
        raw = "1. What about Q4?\n2. Drill in by region?\n3. Outliers?"
        out = ss._parse_llm_output(raw)
        assert out == ["What about Q4?", "Drill in by region?", "Outliers?"]

    def test_empty_or_whitespace(self) -> None:
        assert ss._parse_llm_output("") == []
        assert ss._parse_llm_output("   \n\t") == []

    def test_unparseable_text(self) -> None:
        # No JSON, no bullets, no question marks -- nothing to extract.
        assert ss._parse_llm_output("blah blah no questions here at all") == []

    def test_dedup_and_cap(self) -> None:
        raw = '{"suggestions": ["A?", "a?", "B?", "C?", "D?"]}'
        # Dedup case-insensitively, clamp to 3.
        assert ss._parse_llm_output(raw) == ["A?", "B?", "C?"]


# --------------------------------------------------------------------------- #
# generate_llm_suggestions -- end-to-end with mocked WorkspaceClient
# --------------------------------------------------------------------------- #


class TestGenerateLLMSuggestions:
    def test_short_circuit_on_empty_context(self) -> None:
        ws = MagicMock()
        session = CacheSession()
        out = ss.generate_llm_suggestions(
            ws, session, agent_type="MAS", last_user="", last_assistant=""
        )
        assert out == []
        ws.serving_endpoints.query.assert_not_called()

    def test_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ws = MagicMock()
        session = CacheSession()
        monkeypatch.setattr(
            ss.feature_flags_service,
            "suggestion_model_for",
            lambda *_a, **_kw: "model-x",
        )

        # Fake the `choices[0].message.content` chain.
        choice = MagicMock()
        choice.message.content = '{"suggestions": ["Why X?", "What if Y?", "How Z?"]}'
        ws.serving_endpoints.query.return_value = MagicMock(choices=[choice])

        out = ss.generate_llm_suggestions(
            ws,
            session,
            agent_type="MAS",
            last_user="What's the quarterly revenue?",
            last_assistant="Q3 revenue is $12M.",
        )
        assert out == ["Why X?", "What if Y?", "How Z?"]
        # Must have hit the configured model exactly once.
        assert ws.serving_endpoints.query.call_count == 1
        kwargs = ws.serving_endpoints.query.call_args.kwargs
        assert kwargs["name"] == "model-x"
        assert kwargs["stream"] is False

    def test_endpoint_exception_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = MagicMock()
        session = CacheSession()
        monkeypatch.setattr(
            ss.feature_flags_service,
            "suggestion_model_for",
            lambda *_a, **_kw: "model-x",
        )
        ws.serving_endpoints.query.side_effect = RuntimeError("boom")
        out = ss.generate_llm_suggestions(
            ws, session, agent_type="MAS", last_user="q", last_assistant="a"
        )
        assert out == []

    def test_malformed_response_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = MagicMock()
        session = CacheSession()
        monkeypatch.setattr(
            ss.feature_flags_service,
            "suggestion_model_for",
            lambda *_a, **_kw: "model-x",
        )
        choice = MagicMock()
        choice.message.content = "I am very sorry I cannot help with that."
        ws.serving_endpoints.query.return_value = MagicMock(choices=[choice])
        assert (
            ss.generate_llm_suggestions(
                ws, session, agent_type="MAS", last_user="q", last_assistant="a"
            )
            == []
        )


# --------------------------------------------------------------------------- #
# Cache layer
# --------------------------------------------------------------------------- #


class TestCacheLayer:
    def test_hit_returns_payload_and_source(self) -> None:
        # The cache row stores the suggestions JSON in column 0 and the
        # source string in column 1 -- the helper unpacks both.
        row = (json.dumps(["A?", "B?"]), "genie_native")
        session = CacheSession(cached_row=row)
        result = ss.get_cached_with_source(session, "msg-123")
        assert result is not None
        suggestions, source = result
        assert suggestions == ["A?", "B?"]
        assert source == "genie_native"

    def test_miss_returns_none(self) -> None:
        session = CacheSession(cached_row=None)
        assert ss.get_cached_with_source(session, "msg-x") is None

    def test_get_cached_strips_source(self) -> None:
        row = (json.dumps(["A?"]), "llm")
        session = CacheSession(cached_row=row)
        # Convenience wrapper drops the source for callers that don't care.
        assert ss.get_cached(session, "msg") == ["A?"]

    def test_empty_message_id_returns_none(self) -> None:
        session = CacheSession(cached_row=(json.dumps(["x?"]), "llm"))
        assert ss.get_cached_with_source(session, "") is None

    def test_corrupt_json_returns_none(self) -> None:
        # If a row stores a non-JSON / non-list payload (e.g. someone
        # wrote a string by mistake), surface a miss rather than 500.
        row = ("not-a-list", "llm")
        session = CacheSession(cached_row=row)
        assert ss.get_cached_with_source(session, "msg") is None

    def test_upsert_no_op_on_empty_input(self) -> None:
        # Guard against caching nothing -- the SQL would still write a
        # row with [] which would mask future LLM successes.
        session = CacheSession()
        ss.upsert_cache(session, "msg", [], "llm")
        assert session.commits == 0
        assert session.executed == []

    def test_upsert_writes_and_commits(self) -> None:
        session = CacheSession()
        ss.upsert_cache(session, "msg-1", ["A?", "B?"], "genie_native")
        assert session.commits == 1
        assert any("INSERT INTO suggestions_cache" in q for q in session.executed)


# --------------------------------------------------------------------------- #
# Normalization invariants
# --------------------------------------------------------------------------- #


class TestNormalizeList:
    def test_collapses_whitespace_and_dedups(self) -> None:
        out = ss._normalize_list(["  Why    X?  ", "why x?", "How?"])
        assert out == ["Why X?", "How?"]

    def test_clamps_to_max(self) -> None:
        out = ss._normalize_list([f"q{i}?" for i in range(20)])
        assert len(out) == ss.MAX_SUGGESTIONS

    def test_strips_non_strings(self) -> None:
        # Mixed-type lists from upstream JSON shouldn't crash.
        out = ss._normalize_list(["A?", None, 5, ["nope"], "B?"])
        assert out == ["A?", "B?"]
