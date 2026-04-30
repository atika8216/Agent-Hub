"""Unit tests for the chat streaming pipeline.

Covers the three helpers that drive true token-by-token streaming and the
``messages`` -> ``input`` body retry:

* :func:`_iter_sse_lines` -- parse upstream SSE into JSON dicts.
* :func:`_emit_streamed` -- turn parsed chunks into our own ``data: {...}``
  SSE lines, yielding one event per token.
* :func:`_stream_with_fallback` -- streaming path with graceful non-streaming
  fallback; also exercises the 400/`messages`->`input` retry.

The upstream HTTP layer is stubbed with a lightweight stand-in that mimics
the slice of ``httpx.Response`` we touch (``status_code``, ``headers``,
``iter_lines`` / ``read`` / ``close``) so these tests don't need a real
network stack.
"""

from __future__ import annotations

import json
from collections.abc import Generator, Iterable
from typing import Any
from unittest.mock import MagicMock, patch

import httpx

from scgp_agent_hub.backend.services import chat_service


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #


class _StubResp:
    """Minimal stand-in for an httpx streaming response."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        content_type: str = "text/event-stream",
        lines: Iterable[str] | None = None,
        body: bytes | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._lines = list(lines or [])
        self._body = body or b""
        self.closed = False

    def iter_lines(self) -> Iterable[str]:
        yield from self._lines

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        self.closed = True

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}",
                request=MagicMock(),
                response=MagicMock(status_code=self.status_code),
            )


def _ws(host: str = "https://ws.example.com", token: str = "tok-abc") -> MagicMock:
    ws = MagicMock()
    ws.config.host = host
    ws.config.token = token
    return ws


def _sse_line(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}"


# --------------------------------------------------------------------------- #
# _iter_sse_lines
# --------------------------------------------------------------------------- #


def test_iter_sse_lines_parses_data_lines_and_skips_others() -> None:
    lines = [
        ": keepalive",
        "event: delta",
        "id: 1",
        "",
        _sse_line({"choices": [{"delta": {"content": "hi"}}]}),
        "data: [DONE]",
        "data: not-json",
        _sse_line({"choices": [{"delta": {"content": " there"}}]}),
    ]
    out = list(chat_service._iter_sse_lines(lines))
    assert out == [
        {"choices": [{"delta": {"content": "hi"}}]},
        {"choices": [{"delta": {"content": " there"}}]},
    ]


def test_iter_sse_lines_handles_blank_input() -> None:
    assert list(chat_service._iter_sse_lines([])) == []
    assert list(chat_service._iter_sse_lines(["", "   ", None])) == []  # type: ignore[list-item]


# --------------------------------------------------------------------------- #
# _emit_streamed
# --------------------------------------------------------------------------- #


def _parse_emitted(events: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in events:
        assert e.startswith("data: ") and e.endswith("\n\n"), e
        out.append(json.loads(e[6:].rstrip()))
    return out


def test_emit_streamed_sse_yields_one_event_per_token() -> None:
    resp = _StubResp(
        lines=[
            _sse_line({"choices": [{"delta": {"content": "Hello"}}]}),
            _sse_line({"choices": [{"delta": {"content": ", "}}]}),
            _sse_line({"choices": [{"delta": {"content": "world!"}}]}),
            "data: [DONE]",
        ],
    )
    gen = chat_service._emit_streamed(resp)  # type: ignore[arg-type]
    events: list[str] = []
    for ev in gen:
        events.append(ev)
    full = gen.value if hasattr(gen, "value") else None  # generator return value
    parsed = _parse_emitted(events)
    assert [p["token"] for p in parsed] == ["Hello", ", ", "world!"]
    assert all(p.get("type") == "token" for p in parsed)
    assert full in (None, "Hello, world!")  # value accessible only via yield-from


def _drain(gen: Any) -> tuple[list[str], Any]:
    """Drain a generator and capture its StopIteration.value.

    ``list(gen)`` swallows the return value, so we hand-roll the loop.
    """
    events: list[str] = []
    while True:
        try:
            events.append(next(gen))
        except StopIteration as stop:
            return events, stop.value


def test_emit_streamed_yielded_from_generator_returns_full_text() -> None:
    resp = _StubResp(
        lines=[
            _sse_line({"choices": [{"delta": {"content": "foo"}}]}),
            _sse_line({"choices": [{"delta": {"content": "bar"}}]}),
        ],
    )

    events, full = _drain(chat_service._emit_streamed(resp))  # type: ignore[arg-type]
    assert len(events) == 2
    assert full == "foobar"


def test_emit_streamed_single_json_fallback_chunks(monkeypatch: Any) -> None:
    """Upstream returned one JSON payload; we chunk it for streaming UX."""
    monkeypatch.setattr(chat_service, "_CHUNK_DELAY_S_DEFAULT", 0.0)
    resp = _StubResp(
        content_type="application/json",
        body=json.dumps(
            {"choices": [{"message": {"content": "The answer is 42."}}]}
        ).encode(),
    )

    events = list(chat_service._emit_streamed(resp))  # type: ignore[arg-type]
    parsed = _parse_emitted(events)
    # Should be multiple token events (chunked), not a single dump.
    assert len(parsed) > 1
    assert all(p.get("type") == "token" for p in parsed)
    # Concatenation must equal the original answer.
    assert "".join(p["token"] for p in parsed) == "The answer is 42."
    # No mid-word splits: every chunk except possibly the last ends
    # on whitespace or matches the full text.
    for p in parsed[:-1]:
        tok = p["token"]
        assert tok and (tok[-1].isspace() or tok == "The answer is 42."), tok


def test_emit_streamed_mas_output_chunks() -> None:
    """MAS-style chunks have ``output[*].content[*].text`` rather than choices."""
    resp = _StubResp(
        lines=[
            _sse_line({"output": [{"type": "message", "content": [{"text": "Part A "}]}]}),
            _sse_line({"output": [{"type": "message", "content": [{"text": "Part B"}]}]}),
        ],
    )
    events = list(chat_service._emit_streamed(resp))  # type: ignore[arg-type]
    parsed = _parse_emitted(events)
    assert [p["token"] for p in parsed] == ["Part A ", "Part B"]


# --------------------------------------------------------------------------- #
# _stream_with_fallback
# --------------------------------------------------------------------------- #


def test_stream_with_fallback_streaming_path() -> None:
    """Happy path: httpx returns SSE; generator yields token events and returns full text."""
    stub = _StubResp(
        lines=[
            _sse_line({"choices": [{"delta": {"content": "A"}}]}),
            _sse_line({"choices": [{"delta": {"content": "B"}}]}),
            _sse_line({"choices": [{"delta": {"content": "C"}}]}),
        ],
    )

    with patch.object(chat_service, "_post_stream", return_value=stub) as p:
        gen = chat_service._stream_with_fallback(
            ws=_ws(), endpoint_name="ep-1", history=[{"role": "user", "content": "hi"}]
        )
        events = list(gen)

    assert p.call_count == 1
    parsed = _parse_emitted(events)
    assert [e["token"] for e in parsed] == ["A", "B", "C"]
    assert stub.closed is True  # generator must close the response


def test_stream_with_fallback_falls_back_on_stream_error(monkeypatch: Any) -> None:
    """If streaming raises, we call non-streaming ``_query_endpoint`` and chunk the text."""
    monkeypatch.setattr(chat_service, "_CHUNK_DELAY_S_DEFAULT", 0.0)
    with (
        patch.object(chat_service, "_post_stream", side_effect=RuntimeError("boom")),
        patch.object(
            chat_service,
            "_query_endpoint",
            return_value={"choices": [{"message": {"content": "fallback text"}}]},
        ) as q,
    ):
        events = list(
            chat_service._stream_with_fallback(
                ws=_ws(),
                endpoint_name="ep-1",
                history=[{"role": "user", "content": "hi"}],
            )
        )

    q.assert_called_once()
    # non-streaming call must use stream=False
    _, kwargs = q.call_args
    assert kwargs.get("stream") is False
    parsed = _parse_emitted(events)
    assert all(p.get("type") == "token" for p in parsed)
    assert "".join(p["token"] for p in parsed) == "fallback text"


def test_stream_with_fallback_empty_stream_falls_back(monkeypatch: Any) -> None:
    """SSE response with no parseable content should also fall back."""
    monkeypatch.setattr(chat_service, "_CHUNK_DELAY_S_DEFAULT", 0.0)
    empty = _StubResp(lines=["data: [DONE]"])

    with (
        patch.object(chat_service, "_post_stream", return_value=empty),
        patch.object(
            chat_service,
            "_query_endpoint",
            return_value={"choices": [{"message": {"content": "from fallback"}}]},
        ) as q,
    ):
        events = list(
            chat_service._stream_with_fallback(
                ws=_ws(),
                endpoint_name="ep-1",
                history=[{"role": "user", "content": "hi"}],
            )
        )

    q.assert_called_once()
    parsed = _parse_emitted(events)
    assert "".join(p["token"] for p in parsed) == "from fallback"
    assert empty.closed is True


# --------------------------------------------------------------------------- #
# messages -> input retry (non-streaming path)
# --------------------------------------------------------------------------- #


def test_query_endpoint_retries_with_input_body_on_field_mismatch() -> None:
    """If the endpoint rejects the ``messages`` body, we retry with ``input``."""
    ws = _ws()
    calls: list[dict[str, Any]] = []

    def _fake_do(method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        calls.append({"method": method, "path": path, "body": body})
        if "messages" in body and len(calls) == 1:
            raise RuntimeError(
                "ValidationError: expected 'input' field, got 'messages'"
            )
        return {"choices": [{"message": {"content": "ok"}}]}

    ws.api_client.do.side_effect = _fake_do

    out = chat_service._query_endpoint(
        ws=ws,
        endpoint_name="new-mas-endpoint",
        history=[{"role": "user", "content": "hi"}],
        stream=False,
    )

    assert out == {"choices": [{"message": {"content": "ok"}}]}
    assert len(calls) == 2
    assert "messages" in calls[0]["body"]
    assert "input" in calls[1]["body"]
    assert calls[1]["path"] == "/serving-endpoints/new-mas-endpoint/invocations"


def test_query_endpoint_reraises_non_field_errors() -> None:
    ws = _ws()
    ws.api_client.do.side_effect = RuntimeError("internal server error")
    try:
        chat_service._query_endpoint(
            ws=ws,
            endpoint_name="ep",
            history=[{"role": "user", "content": "x"}],
            stream=False,
        )
    except RuntimeError as e:
        assert "internal" in str(e)
    else:
        raise AssertionError("should have re-raised")


def test_is_field_mismatch_matches_common_error_phrasings() -> None:
    mismatch = [
        "Validation error: missing 'input' field",
        "Endpoint expects 'messages' field",
        "unknown input field in request body",
        "bad messages field in body",
    ]
    not_mismatch = [
        "internal server error",
        "unauthorized",
        "endpoint is scaled to zero",
    ]
    for m in mismatch:
        assert chat_service._is_field_mismatch(m), m
    for m in not_mismatch:
        assert not chat_service._is_field_mismatch(m), m


# --------------------------------------------------------------------------- #
# stream_chat -- verifies the 'started' and 'done' SSE envelope
# --------------------------------------------------------------------------- #


def test_stream_chat_emits_started_then_tokens_then_done(monkeypatch: Any) -> None:
    """End-to-end: `started` fires before tokens; `done` closes the stream."""

    # Mock all DB helpers to be no-ops that return sensible values.
    monkeypatch.setattr(
        chat_service,
        "_verify_access_best_effort",
        lambda endpoint_name, ws: None,
    )
    monkeypatch.setattr(
        chat_service,
        "_ensure_conversation",
        lambda session, conversation_id, endpoint_name, user_email, user_message: (
            "conv-123",
            True,
        ),
    )
    monkeypatch.setattr(chat_service, "_persist_message", lambda *a, **k: "msg-1")
    monkeypatch.setattr(chat_service, "_update_conversation_title", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "_touch_conversation", lambda *a, **k: None)

    # Return the user's message as the context; no long-term memory.
    monkeypatch.setattr(
        chat_service.memory_service,
        "build_context",
        lambda **kwargs: [{"role": "user", "content": kwargs.get("conv_id", "")}],
    )
    monkeypatch.setattr(
        chat_service.memory_service,
        "get_memory_mode",
        lambda session: "short_term",
    )

    # Pretend streaming succeeded with three tokens.
    stub = _StubResp(
        lines=[
            _sse_line({"choices": [{"delta": {"content": "Hel"}}]}),
            _sse_line({"choices": [{"delta": {"content": "lo"}}]}),
            _sse_line({"choices": [{"delta": {"content": "!"}}]}),
        ],
    )
    monkeypatch.setattr(chat_service, "_post_stream", lambda *a, **k: stub)

    gen = chat_service.stream_chat(
        endpoint_name="ep-x",
        conversation_id=None,
        user_message="hi",
        user_email="atika@example.com",
        ws=_ws(),
        session=MagicMock(),
        engine=None,
    )
    raw_events = list(gen)
    parsed = _parse_emitted(raw_events)

    # started -> three token -> done
    assert parsed[0] == {"type": "started", "conversation_id": "conv-123"}
    tokens = [e for e in parsed if e.get("type") == "token"]
    assert [t["token"] for t in tokens] == ["Hel", "lo", "!"]
    assert parsed[-1] == {
        "type": "done",
        "done": True,
        "conversation_id": "conv-123",
    }


def test_stream_chat_emits_error_envelope_when_preflight_fails(
    monkeypatch: Any,
) -> None:
    from scgp_agent_hub.backend.services.base import ForbiddenError

    monkeypatch.setattr(
        chat_service,
        "_verify_access_best_effort",
        lambda endpoint_name, ws: None,
    )

    def _raise(*_a: Any, **_k: Any) -> None:
        raise ForbiddenError("no access")

    monkeypatch.setattr(chat_service, "_ensure_conversation", _raise)

    gen = chat_service.stream_chat(
        endpoint_name="ep-x",
        conversation_id=None,
        user_message="hi",
        user_email="atika@example.com",
        ws=_ws(),
        session=MagicMock(),
        engine=None,
    )
    parsed = _parse_emitted(list(gen))
    assert len(parsed) == 1
    assert parsed[0]["type"] == "error"
    assert parsed[0]["error"] == "no access"
    assert parsed[0]["done"] is True


# --------------------------------------------------------------------------- #
# _simulate_chunked_stream -- word-aware chunking
# --------------------------------------------------------------------------- #


def test_simulate_chunked_stream_word_boundaries(monkeypatch: Any) -> None:
    """Multi-token output for a one-shot response, no mid-word splits."""
    monkeypatch.setattr(chat_service, "_CHUNK_DELAY_S_DEFAULT", 0.0)
    text = "The quick brown fox jumps over the lazy dog. " * 3
    events = list(chat_service._simulate_chunked_stream(text, chunk_chars=12, delay_s=0.0))
    parsed = _parse_emitted(events)
    # Reconstruct -- chunking must be lossless.
    assert "".join(p["token"] for p in parsed) == text
    # Must produce more than one chunk (otherwise we haven't simulated streaming).
    assert len(parsed) > 1
    # No chunk should split a word: every chunk except possibly the last
    # must end on whitespace.
    for p in parsed[:-1]:
        tok = p["token"]
        assert tok and tok[-1].isspace(), f"chunk ends mid-word: {tok!r}"


def test_simulate_chunked_stream_empty() -> None:
    """Empty input yields zero events (no SSE noise)."""
    events = list(chat_service._simulate_chunked_stream(""))
    assert events == []


def test_simulate_chunked_stream_single_short_word() -> None:
    """One short word emits as a single chunk."""
    events = list(chat_service._simulate_chunked_stream("hi", chunk_chars=12, delay_s=0.0))
    parsed = _parse_emitted(events)
    assert len(parsed) == 1
    assert parsed[0]["token"] == "hi"


# --------------------------------------------------------------------------- #
# _post_stream Accept header
# --------------------------------------------------------------------------- #


def test_post_stream_sets_accept_sse(monkeypatch: Any) -> None:
    """The streaming POST must hint upstreams with Accept: text/event-stream."""
    captured: dict[str, Any] = {}

    class _FakeReq:
        pass

    class _FakeResp:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        def read(self) -> bytes:  # pragma: no cover - unused
            return b""

        def close(self) -> None:
            pass

        def raise_for_status(self) -> None:
            pass

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def build_request(
            self, method: str, url: str, headers: dict[str, str], json: Any
        ) -> _FakeReq:
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = dict(headers)
            captured["json"] = json
            return _FakeReq()

        def send(self, req: Any, stream: bool) -> _FakeResp:
            return _FakeResp()

        def close(self) -> None:
            pass

    monkeypatch.setattr(chat_service.httpx, "Client", _FakeClient)

    resp = chat_service._post_stream(
        ws=_ws(),
        endpoint_name="ep-x",
        history=[{"role": "user", "content": "hi"}],
    )
    # Make sure we don't leak the fake client back to the pool -- it has
    # no _scgp_client attribute hookup we care about here.
    chat_service._close_stream(resp)  # type: ignore[arg-type]

    assert captured["headers"].get("Accept") == "text/event-stream"
    assert captured["json"]["stream"] is True
    assert "Bearer " in captured["headers"]["Authorization"]
    assert captured["url"].endswith("/serving-endpoints/ep-x/invocations")


# --------------------------------------------------------------------------- #
# Genie helpers + _stream_genie_kickoff_and_poll / _stream_genie_answer
#
# The Phase-4 chart pipeline split the original ``_stream_genie`` into two
# generators: ``_stream_genie_kickoff_and_poll`` returns the final Genie
# message via the generator's ``return`` value (so the caller can persist
# the assistant message id, build a chart artifact, and *then* stream the
# textual answer above the chart). For these unit tests we want to keep
# verifying the same end-to-end semantics, so we drive both stages from a
# tiny local helper that mimics the relevant slice of ``stream_chat``
# without touching the chart/suggestion/persistence code paths.
# --------------------------------------------------------------------------- #


def _run_stream_genie(
    *, ws: Any, session: Any, space_id: str, conv_id: str, user_message: str
) -> Generator[str, None, str]:
    """Drive both Genie stages and return the rendered answer body.

    Mirrors :func:`chat_service.stream_chat`'s Genie branch with the
    chart / suggestion / persistence concerns stripped out.
    """
    kickoff_ctx = yield from chat_service._stream_genie_kickoff_and_poll(
        ws=ws,
        session=session,
        space_id=space_id,
        conv_id=conv_id,
        user_message=user_message,
    )
    _, final_message, _, _, last_status = kickoff_ctx
    if not final_message:
        return ""
    answer = yield from chat_service._stream_genie_answer(
        final_message=final_message,
        last_status=last_status,
    )
    return answer


def test_is_genie_helpers() -> None:
    assert chat_service._is_genie("genie:abc123") is True
    assert chat_service._is_genie("ep-mas") is False
    assert chat_service._is_genie("") is False
    assert chat_service._genie_space_id("genie:abc123") == "abc123"


class _GenieFakeWS:
    """WS stub whose ``api_client.do`` plays back a scripted sequence."""

    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.api_client = MagicMock()
        self.api_client.do.side_effect = self._do
        self.config = MagicMock()
        self.config.host = "https://ws.example.com"
        self.config.token = "tok-abc"

    def _do(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, path, body))
        if not self.responses:
            raise RuntimeError("no scripted response")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class _SessSpy:
    """SQLModel session stub that records execs and supports COMPLETED Genie metadata."""

    def __init__(self, genie_conv_id: str | None = None) -> None:
        self.genie_conv_id = genie_conv_id
        self.committed_calls: list[Any] = []
        self.execs: list[Any] = []

    def exec(self, statement: Any) -> Any:  # noqa: A003
        self.execs.append(statement)
        # Pretend the SELECT returns a metadata row when caller asks for it.
        sql = str(getattr(statement, "_orig", statement))
        result = MagicMock()
        if "SELECT metadata_json FROM conversations" in sql:
            if self.genie_conv_id is None:
                result.one_or_none.return_value = None
            else:
                result.one_or_none.return_value = (
                    {"genie_conversation_id": self.genie_conv_id},
                )
        else:
            result.one_or_none.return_value = None
        return result

    def commit(self) -> None:
        self.committed_calls.append("commit")


def test_stream_genie_first_turn_persists_conv_id(monkeypatch: Any) -> None:
    """First turn calls start-conversation, persists the Genie conv id, then polls."""
    monkeypatch.setattr(chat_service, "_GENIE_POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(chat_service, "_CHUNK_DELAY_S_DEFAULT", 0.0)

    ws = _GenieFakeWS(
        responses=[
            {"conversation_id": "g-conv-1", "message_id": "g-msg-1"},
            {"status": "COMPLETED", "attachments": [{"text": {"content": "Answer."}}]},
        ]
    )
    sess = _SessSpy(genie_conv_id=None)

    # _genie_get_conv_id reads via session.exec; use monkeypatch shortcut
    # so we don't need to reconstruct a real SQL parser.
    monkeypatch.setattr(chat_service, "_genie_get_conv_id", lambda s, c: None)
    set_calls: list[tuple[Any, str, str]] = []
    monkeypatch.setattr(
        chat_service,
        "_genie_set_conv_id",
        lambda s, c, gid: set_calls.append((s, c, gid)),
    )

    events, full = _drain(
        _run_stream_genie(
            ws=ws,
            session=sess,
            space_id="space-xyz",
            conv_id="conv-internal-1",
            user_message="how many orders?",
        )
    )

    # First call: start-conversation
    assert ws.calls[0][0] == "POST"
    assert ws.calls[0][1] == "/api/2.0/genie/spaces/space-xyz/start-conversation"
    assert ws.calls[0][2] == {"content": "how many orders?"}

    # Genie conv id was persisted
    assert set_calls and set_calls[0][2] == "g-conv-1"

    # Final answer streamed via chunker
    parsed = _parse_emitted(events)
    assert any(p.get("type") == "token" for p in parsed)
    assert "Answer." in "".join(
        p["token"] for p in parsed if p.get("type") == "token"
    )
    assert full and "Answer." in full


def test_stream_genie_followup_uses_existing_conv_id(monkeypatch: Any) -> None:
    """When a Genie conv id already exists, hit /messages instead of start-conversation."""
    monkeypatch.setattr(chat_service, "_GENIE_POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(chat_service, "_CHUNK_DELAY_S_DEFAULT", 0.0)

    ws = _GenieFakeWS(
        responses=[
            {"message_id": "g-msg-2"},
            {"status": "COMPLETED", "attachments": [{"text": {"content": "Followup."}}]},
        ]
    )
    sess = _SessSpy(genie_conv_id="g-conv-existing")
    monkeypatch.setattr(chat_service, "_genie_get_conv_id", lambda s, c: "g-conv-existing")
    set_calls: list[Any] = []
    monkeypatch.setattr(
        chat_service,
        "_genie_set_conv_id",
        lambda *a, **k: set_calls.append(a),
    )

    events, full = _drain(
        _run_stream_genie(
            ws=ws,
            session=sess,
            space_id="space-xyz",
            conv_id="conv-internal-2",
            user_message="and by region?",
        )
    )

    # First call: messages, not start-conversation
    assert ws.calls[0][0] == "POST"
    assert ws.calls[0][1] == "/api/2.0/genie/spaces/space-xyz/conversations/g-conv-existing/messages"
    # We must NOT re-persist the conv id.
    assert not set_calls
    assert full and "Followup." in full


def test_stream_genie_completed_emits_answer_and_sql(monkeypatch: Any) -> None:
    """Final answer renders text + a fenced sql block when Genie returns a query."""
    monkeypatch.setattr(chat_service, "_GENIE_POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(chat_service, "_CHUNK_DELAY_S_DEFAULT", 0.0)

    ws = _GenieFakeWS(
        responses=[
            {"conversation_id": "g-conv-3", "message_id": "g-msg-3"},
            # First poll: still asking AI
            {"status": "ASKING_AI"},
            # Second poll: executing
            {"status": "EXECUTING_QUERY"},
            # Final
            {
                "status": "COMPLETED",
                "attachments": [
                    {"text": {"content": "There are 42 active orders."}},
                    {
                        "query": {
                            "description": "Active orders by region",
                            "query": "SELECT region, COUNT(*) FROM orders WHERE active=true GROUP BY region",
                        }
                    },
                ],
            },
        ]
    )
    sess = _SessSpy()
    monkeypatch.setattr(chat_service, "_genie_get_conv_id", lambda s, c: None)
    monkeypatch.setattr(chat_service, "_genie_set_conv_id", lambda *a, **k: None)

    events, full = _drain(
        _run_stream_genie(
            ws=ws,
            session=sess,
            space_id="space-xyz",
            conv_id="conv-internal-3",
            user_message="orders?",
        )
    )

    parsed = _parse_emitted(events)
    body = "".join(p["token"] for p in parsed if p.get("type") == "token")
    assert "There are 42 active orders." in body
    assert "```sql" in body
    assert "SELECT region" in body
    # Status hints are visible too.
    assert "Generating SQL" in body or "Running query" in body
    assert full and "There are 42 active orders." in full


def test_stream_genie_failed_emits_error(monkeypatch: Any) -> None:
    """Genie FAILED status must surface as an error SSE event, not silently swallow."""
    monkeypatch.setattr(chat_service, "_GENIE_POLL_INTERVAL_S", 0.0)

    ws = _GenieFakeWS(
        responses=[
            {"conversation_id": "g-conv-4", "message_id": "g-msg-4"},
            {"status": "FAILED", "error": {"message": "warehouse went away"}},
        ]
    )
    sess = _SessSpy()
    monkeypatch.setattr(chat_service, "_genie_get_conv_id", lambda s, c: None)
    monkeypatch.setattr(chat_service, "_genie_set_conv_id", lambda *a, **k: None)

    events, full = _drain(
        _run_stream_genie(
            ws=ws,
            session=sess,
            space_id="space-xyz",
            conv_id="conv-internal-4",
            user_message="?",
        )
    )

    parsed = _parse_emitted(events)
    assert any(p.get("type") == "error" for p in parsed)
    err = next(p for p in parsed if p.get("type") == "error")
    assert "warehouse went away" in err["error"]
    assert full == ""


def test_stream_genie_timeout_emits_error(monkeypatch: Any) -> None:
    """If the poll deadline elapses without COMPLETED, emit an error event."""
    monkeypatch.setattr(chat_service, "_GENIE_POLL_TIMEOUT_S", 0.0)
    monkeypatch.setattr(chat_service, "_GENIE_POLL_INTERVAL_S", 0.0)

    # Even if we have a kickoff response, timeout triggers before any poll
    # iteration runs because the deadline is 0.
    ws = _GenieFakeWS(
        responses=[
            {"conversation_id": "g-conv-5", "message_id": "g-msg-5"},
        ]
    )
    sess = _SessSpy()
    monkeypatch.setattr(chat_service, "_genie_get_conv_id", lambda s, c: None)
    monkeypatch.setattr(chat_service, "_genie_set_conv_id", lambda *a, **k: None)

    events, full = _drain(
        _run_stream_genie(
            ws=ws,
            session=sess,
            space_id="space-xyz",
            conv_id="conv-internal-5",
            user_message="?",
        )
    )
    parsed = _parse_emitted(events)
    err = [p for p in parsed if p.get("type") == "error"]
    assert err and "timed out" in err[0]["error"]
    assert full == ""


# --------------------------------------------------------------------------- #
# UC HTTP + MCP prefix stubs (Phase 1 of the master roadmap).
# --------------------------------------------------------------------------- #


def test_uc_and_mcp_prefix_helpers() -> None:
    """The prefix helpers classify identifiers and strip their namespaces."""
    assert chat_service._is_uc_connection("uc:main.fn_ask") is True
    assert chat_service._is_uc_connection("mcp:main.fn_ask") is False
    assert chat_service._is_uc_connection("ep-1") is False
    assert chat_service._is_mcp_endpoint("mcp:main.srv_chat") is True
    assert chat_service._is_mcp_endpoint("uc:main.fn") is False
    assert chat_service._uc_full_name("uc:main.schema.fn") == "main.schema.fn"
    assert chat_service._mcp_full_name("mcp:main.schema.srv") == "main.schema.srv"


def _stub_stream_chat_io(monkeypatch: Any) -> None:
    """Shared no-op DB helpers used by the UC/MCP stub tests."""
    monkeypatch.setattr(chat_service, "_verify_access_best_effort", lambda *a, **k: None)
    monkeypatch.setattr(
        chat_service,
        "_ensure_conversation",
        lambda *a, **k: ("conv-stub", True),
    )
    monkeypatch.setattr(chat_service, "_persist_message", lambda *a, **k: "msg-1")
    monkeypatch.setattr(chat_service, "_update_conversation_title", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "_touch_conversation", lambda *a, **k: None)
    # The stub path must NOT build context or touch the MAS pipeline.
    monkeypatch.setattr(
        chat_service.memory_service,
        "build_context",
        lambda **k: (_ for _ in ()).throw(AssertionError("UC/MCP stub should skip memory")),
    )


def test_stream_chat_uc_kill_switch_emits_stub(monkeypatch: Any) -> None:
    """Setting SCGP_DISABLE_UC_MCP_CHAT=1 reverts uc:* to the Phase-1 stub."""
    _stub_stream_chat_io(monkeypatch)
    monkeypatch.setenv("SCGP_DISABLE_UC_MCP_CHAT", "1")

    # Neither the MAS path nor the new HTTP invoker should be reached.
    monkeypatch.setattr(
        chat_service,
        "_stream_with_fallback",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("MAS path called for uc:* endpoint")
        ),
    )
    monkeypatch.setattr(
        chat_service,
        "_stream_http_connection",
        lambda **k: (_ for _ in ()).throw(
            AssertionError("HTTP invoker called while kill switch is on")
        ),
    )

    gen = chat_service.stream_chat(
        endpoint_name="uc:main.sales.ask_agent",
        conversation_id=None,
        user_message="hi",
        user_email="atika@example.com",
        ws=_ws(),
        session=MagicMock(),
        engine=None,
    )
    parsed = _parse_emitted(list(gen))
    body = "".join(p["token"] for p in parsed if p.get("type") == "token")

    assert parsed[0]["type"] == "started"
    assert parsed[-1]["type"] == "done"
    assert "main.sales.ask_agent" in body
    assert "HTTP connection" in body
    assert "SCGP_DISABLE_UC_MCP_CHAT" in body


def test_stream_chat_mcp_kill_switch_emits_stub(monkeypatch: Any) -> None:
    """Setting SCGP_DISABLE_UC_MCP_CHAT=1 reverts mcp:* to the Phase-1 stub."""
    _stub_stream_chat_io(monkeypatch)
    monkeypatch.setenv("SCGP_DISABLE_UC_MCP_CHAT", "1")

    monkeypatch.setattr(
        chat_service,
        "_stream_with_fallback",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("MAS path called for mcp:* endpoint")
        ),
    )
    monkeypatch.setattr(
        chat_service,
        "_stream_mcp",
        lambda **k: (_ for _ in ()).throw(
            AssertionError("MCP invoker called while kill switch is on")
        ),
    )

    gen = chat_service.stream_chat(
        endpoint_name="mcp:main.tools.chat_server",
        conversation_id=None,
        user_message="hi",
        user_email="atika@example.com",
        ws=_ws(),
        session=MagicMock(),
        engine=None,
    )
    parsed = _parse_emitted(list(gen))
    body = "".join(p["token"] for p in parsed if p.get("type") == "token")

    assert parsed[0]["type"] == "started"
    assert parsed[-1]["type"] == "done"
    assert "main.tools.chat_server" in body
    assert "MCP endpoint" in body


def test_stream_chat_uc_dispatches_to_http_invoker(monkeypatch: Any) -> None:
    """Without the kill switch, uc:* endpoints go through _stream_http_connection."""
    _stub_stream_chat_io(monkeypatch)
    monkeypatch.delenv("SCGP_DISABLE_UC_MCP_CHAT", raising=False)

    monkeypatch.setattr(
        chat_service,
        "_stream_with_fallback",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("MAS path called for uc:* endpoint")
        ),
    )

    called: dict[str, Any] = {}

    def _fake_http(
        *,
        user_ws: Any,
        sp_ws: Any,
        session: Any,
        conv_id: str,
        endpoint_name: str,
        user_message: str,
    ) -> Any:
        called["endpoint_name"] = endpoint_name
        called["user_message"] = user_message
        yield chat_service._sse({"type": "token", "token": "done ", "done": False})
        return "done "

    monkeypatch.setattr(chat_service, "_stream_http_connection", _fake_http)

    gen = chat_service.stream_chat(
        endpoint_name="uc:main.sales.ask_agent",
        conversation_id=None,
        user_message="hi",
        user_email="atika@example.com",
        ws=_ws(),
        session=MagicMock(),
        engine=None,
        sp_ws=_ws(host="https://sp.example.com", token="sp-tok"),
    )
    parsed = _parse_emitted(list(gen))
    assert called["endpoint_name"] == "uc:main.sales.ask_agent"
    assert called["user_message"] == "hi"
    assert parsed[0]["type"] == "started"
    assert any(p.get("type") == "token" for p in parsed)
    assert parsed[-1]["type"] == "done"


def test_stream_chat_mcp_dispatches_to_mcp_invoker(monkeypatch: Any) -> None:
    """Without the kill switch, mcp:* endpoints go through _stream_mcp."""
    _stub_stream_chat_io(monkeypatch)
    monkeypatch.delenv("SCGP_DISABLE_UC_MCP_CHAT", raising=False)

    called: dict[str, Any] = {}

    def _fake_mcp(
        *,
        user_ws: Any,
        sp_ws: Any,
        session: Any,
        conv_id: str,
        endpoint_name: str,
        user_message: str,
        tool_choice: Any = None,
    ) -> Any:
        called["endpoint_name"] = endpoint_name
        called["tool_choice"] = tool_choice
        yield chat_service._sse({"type": "tool_call", "name": "chat", "done": False})
        yield chat_service._sse({"type": "token", "token": "hi!", "done": False})
        return "hi!"

    monkeypatch.setattr(chat_service, "_stream_mcp", _fake_mcp)

    gen = chat_service.stream_chat(
        endpoint_name="mcp:main.tools.chat_server",
        conversation_id=None,
        user_message="q",
        user_email="atika@example.com",
        ws=_ws(),
        session=MagicMock(),
        engine=None,
        sp_ws=_ws(),
        tool_choice="chat",
    )
    parsed = _parse_emitted(list(gen))
    assert called["endpoint_name"] == "mcp:main.tools.chat_server"
    assert called["tool_choice"] == "chat"
    types = [p.get("type") for p in parsed]
    assert "tool_call" in types
    assert parsed[0]["type"] == "started"
    assert parsed[-1]["type"] == "done"


# --------------------------------------------------------------------------- #
# _genie_query_attachments -- collects every ``query`` block from a Genie
# completion message so the chart pipeline can build one artifact per one.
# --------------------------------------------------------------------------- #


def test_genie_query_attachments_collects_multiple() -> None:
    msg = {
        "attachments": [
            {"text": {"content": "lead-in"}},  # no query -> skip
            {
                "attachment_id": "a-1",
                "query": {
                    "description": "Orders by region",
                    "query": "SELECT region, COUNT(*) FROM orders GROUP BY 1",
                },
            },
            {
                "attachment_id": "a-2",
                "query": {
                    "description": "Orders by month",
                    "query": "SELECT month, COUNT(*) FROM orders GROUP BY 1",
                },
            },
            {"attachment_id": "a-3", "query": "string-not-dict"},  # malformed
        ],
    }
    out = chat_service._genie_query_attachments(msg)
    assert out == [
        ("a-1", "Orders by region"),
        ("a-2", "Orders by month"),
    ]


def test_genie_query_attachments_handles_malformed_inputs() -> None:
    assert chat_service._genie_query_attachments({}) == []  # type: ignore[arg-type]
    assert chat_service._genie_query_attachments({"attachments": None}) == []
    assert chat_service._genie_query_attachments({"attachments": "nope"}) == []
    assert chat_service._genie_query_attachments(None) == []  # type: ignore[arg-type]
    # Attachment without an id is skipped so we never emit an artifact
    # we can't re-fetch from Genie later.
    assert chat_service._genie_query_attachments(
        {"attachments": [{"query": {"description": "d"}}]}
    ) == []


# --------------------------------------------------------------------------- #
# Genie dispatch persists CLEAN assistant content (no progress placeholders).
# This is the regression guard for the historic bug where ``_Preparing
# warehouse..._`` / ``_Generating SQL..._`` tokens were saved into
# ``messages.content`` and then rendered on every reload.
# --------------------------------------------------------------------------- #


def test_stream_chat_genie_persists_clean_content(monkeypatch: Any) -> None:
    """The persisted assistant content must NOT include Genie progress labels.

    The kickoff/poll generator emits those labels over SSE (live UX) and
    stuffs them into the generator's return value so tests can verify
    the live stream. The persisted content is managed separately: seed
    empty; update with the stripped answer body at completion. Any
    regression that pipes ``status_prefix`` back into persistence would
    re-surface the stale progress chatter on reload.
    """
    monkeypatch.setattr(
        chat_service, "_verify_access_best_effort", lambda *a, **k: None
    )
    monkeypatch.setattr(
        chat_service,
        "_ensure_conversation",
        lambda *a, **k: ("conv-clean", True),
    )
    monkeypatch.setattr(chat_service, "_update_conversation_title", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "_touch_conversation", lambda *a, **k: None)

    # Capture what content gets persisted at each _persist_message call.
    persist_calls: list[tuple[Any, Any, str, str]] = []

    def _capture_persist(session: Any, conv_id: Any, role: str, content: str) -> str:
        persist_calls.append((session, conv_id, role, content))
        return f"msg-{len(persist_calls)}"

    monkeypatch.setattr(chat_service, "_persist_message", _capture_persist)

    # Capture _update_message_content calls (what replaces the seeded "" row).
    update_calls: list[tuple[str, str]] = []

    def _capture_update(session: Any, msg_id: str, content: str) -> None:
        update_calls.append((msg_id, content))

    monkeypatch.setattr(chat_service, "_update_message_content", _capture_update)

    # Fake kickoff/poll yields progress labels and returns the full
    # concatenated ``status_prefix`` via the generator return.
    def _fake_kickoff(
        ws: Any, session: Any, space_id: str, conv_id: str, user_message: str
    ) -> Any:
        yield chat_service._sse(
            {"type": "token", "token": "_Preparing warehouse..._\n\n", "done": False}
        )
        yield chat_service._sse(
            {"type": "token", "token": "_Generating SQL..._\n\n", "done": False}
        )
        return (
            "_Preparing warehouse..._\n\n_Generating SQL..._\n\n",
            {"attachments": [{"text": {"content": "There are 42 orders."}}]},
            "g-conv",
            "g-msg",
            "COMPLETED",
        )

    # Fake answer yields the clean text body with the leading \n\n that
    # _stream_genie_answer would normally produce when ``last_status``
    # was set. ``stream_chat`` must strip that \n\n before persisting.
    def _fake_answer(final_message: dict[str, Any], last_status: str | None) -> Any:
        yield chat_service._sse(
            {"type": "token", "token": "\n\nThere are 42 orders.", "done": False}
        )
        return "\n\nThere are 42 orders."

    monkeypatch.setattr(chat_service, "_stream_genie_kickoff_and_poll", _fake_kickoff)
    monkeypatch.setattr(chat_service, "_stream_genie_answer", _fake_answer)

    # Charts off so we don't exercise the chart pipeline here.
    monkeypatch.setattr(
        chat_service.feature_flags_service,
        "is_enabled",
        lambda *a, **k: False,
    )

    # Suggestions off -- same reasoning, keeps the dispatch test tight.
    monkeypatch.setattr(
        chat_service, "_emit_suggestions", lambda *a, **k: iter(())
    )

    gen = chat_service.stream_chat(
        endpoint_name="genie:abc",
        conversation_id=None,
        user_message="how many orders?",
        user_email="atika@example.com",
        ws=_ws(),
        session=MagicMock(),
        engine=None,
    )
    parsed = _parse_emitted(list(gen))

    # Live stream MUST still include the progress labels (user sees them).
    live_tokens = [p["token"] for p in parsed if p.get("type") == "token"]
    joined = "".join(live_tokens)
    assert "Preparing warehouse" in joined
    assert "Generating SQL" in joined

    # Two persist_message calls: user msg + seeded assistant row.
    assert len(persist_calls) == 2
    user_row = persist_calls[0]
    assert user_row[2] == "user"
    assistant_seed = persist_calls[1]
    assert assistant_seed[2] == "assistant"
    assert assistant_seed[3] == "", (
        "assistant row must be seeded empty so reloads don't include progress placeholders"
    )

    # Exactly one _update_message_content call with the clean answer body.
    assert len(update_calls) == 1
    _, stored_content = update_calls[0]
    assert stored_content == "There are 42 orders."
    # The leading \n\n separator from _stream_genie_answer must be stripped.
    assert not stored_content.startswith("\n")
    # No progress prefix must leak into the stored row.
    assert "Preparing warehouse" not in stored_content
    assert "Generating SQL" not in stored_content


# --------------------------------------------------------------------------- #
# Multi-chart Genie dispatch: two ``query`` attachments -> two SSE chart
# events with indices 0 and 1, each accompanied by a persisted artifact.
# --------------------------------------------------------------------------- #


def test_stream_chat_genie_emits_one_chart_per_query_attachment(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        chat_service, "_verify_access_best_effort", lambda *a, **k: None
    )
    monkeypatch.setattr(
        chat_service,
        "_ensure_conversation",
        lambda *a, **k: ("conv-multi", True),
    )
    monkeypatch.setattr(chat_service, "_persist_message", lambda *a, **k: "msg-multi")
    monkeypatch.setattr(chat_service, "_update_message_content", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "_update_conversation_title", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "_touch_conversation", lambda *a, **k: None)
    monkeypatch.setattr(
        chat_service, "_emit_suggestions", lambda *a, **k: iter(())
    )

    final_message = {
        "attachments": [
            {
                "attachment_id": "a-primary",
                "query": {"description": "Primary", "query": "SELECT 1"},
            },
            {
                "attachment_id": "a-drilldown",
                "query": {"description": "Drill-down", "query": "SELECT 2"},
            },
        ],
    }

    def _fake_kickoff(
        ws: Any, session: Any, space_id: str, conv_id: str, user_message: str
    ) -> Any:
        if False:  # pragma: no cover - generator shape
            yield  # noqa: F821
        return ("", final_message, "g-conv", "g-msg", "COMPLETED")

    def _fake_answer(final_message: dict[str, Any], last_status: str | None) -> Any:
        yield chat_service._sse(
            {"type": "token", "token": "Answer body.", "done": False}
        )
        return "Answer body."

    monkeypatch.setattr(chat_service, "_stream_genie_kickoff_and_poll", _fake_kickoff)
    monkeypatch.setattr(chat_service, "_stream_genie_answer", _fake_answer)

    # Force the chart feature flag on so the attachment loop runs.
    monkeypatch.setattr(
        chat_service.feature_flags_service,
        "is_enabled",
        lambda *a, **k: True,
    )

    # Record every chart_service.build_chart_artifact call, mirror it back
    # as a minimal artifact so the SSE event can serialize cleanly.
    build_calls: list[dict[str, Any]] = []

    def _fake_build(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(dict(kwargs))
        return {
            "chart_id": f"chart-{kwargs['idx']}",
            "kind": "bar",
            "title": kwargs.get("title") or "",
            "option": {"series": []},
            "truncated": False,
        }

    monkeypatch.setattr(chat_service.chart_service, "build_chart_artifact", _fake_build)

    gen = chat_service.stream_chat(
        endpoint_name="genie:space-multi",
        conversation_id=None,
        user_message="segment me",
        user_email="atika@example.com",
        ws=_ws(),
        session=MagicMock(),
        engine=None,
    )
    parsed = _parse_emitted(list(gen))

    chart_events = [p for p in parsed if p.get("type") == "chart"]
    assert len(chart_events) == 2
    # Events must carry monotonically increasing indices and the same total.
    assert [c["index"] for c in chart_events] == [0, 1]
    assert all(c["total"] == 2 for c in chart_events)
    assert [c["chart_id"] for c in chart_events] == ["chart-0", "chart-1"]
    # Persistence gets one call per attachment, with the index propagated.
    assert [c["idx"] for c in build_calls] == [0, 1]
    assert [c["attachment_id"] for c in build_calls] == [
        "a-primary",
        "a-drilldown",
    ]
    # Textual answer still streams after the chart events.
    tokens = [p for p in parsed if p.get("type") == "token"]
    assert tokens, "answer tokens must follow the chart events"


def test_stream_chat_genie_continues_when_one_chart_build_fails(
    monkeypatch: Any,
) -> None:
    """A failing chart build must not block the textual answer or sibling charts."""
    monkeypatch.setattr(
        chat_service, "_verify_access_best_effort", lambda *a, **k: None
    )
    monkeypatch.setattr(
        chat_service, "_ensure_conversation", lambda *a, **k: ("conv-x", True)
    )
    monkeypatch.setattr(chat_service, "_persist_message", lambda *a, **k: "msg-x")
    monkeypatch.setattr(chat_service, "_update_message_content", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "_update_conversation_title", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "_touch_conversation", lambda *a, **k: None)
    monkeypatch.setattr(
        chat_service, "_emit_suggestions", lambda *a, **k: iter(())
    )
    monkeypatch.setattr(
        chat_service.feature_flags_service,
        "is_enabled",
        lambda *a, **k: True,
    )

    final_message = {
        "attachments": [
            {"attachment_id": "a-0", "query": {"description": "", "query": "SELECT 1"}},
            {"attachment_id": "a-1", "query": {"description": "", "query": "SELECT 2"}},
        ],
    }

    def _fake_kickoff(*a: Any, **k: Any) -> Any:
        if False:  # pragma: no cover - empty yield
            yield  # noqa: F821
        return ("", final_message, "g-conv", "g-msg", "COMPLETED")

    def _fake_answer(*_a: Any, **_k: Any) -> Any:
        yield chat_service._sse({"type": "token", "token": "Hi.", "done": False})
        return "Hi."

    monkeypatch.setattr(chat_service, "_stream_genie_kickoff_and_poll", _fake_kickoff)
    monkeypatch.setattr(chat_service, "_stream_genie_answer", _fake_answer)

    def _raising_build(**kwargs: Any) -> dict[str, Any]:
        if kwargs["idx"] == 0:
            raise RuntimeError("genie rate limited")
        return {
            "chart_id": "chart-1",
            "kind": "bar",
            "title": "",
            "option": {"series": []},
            "truncated": False,
        }

    monkeypatch.setattr(
        chat_service.chart_service, "build_chart_artifact", _raising_build
    )

    gen = chat_service.stream_chat(
        endpoint_name="genie:space",
        conversation_id=None,
        user_message="q",
        user_email="atika@example.com",
        ws=_ws(),
        session=MagicMock(),
        engine=None,
    )
    parsed = _parse_emitted(list(gen))

    chart_events = [p for p in parsed if p.get("type") == "chart"]
    # Only the second attachment emits a chart event -- the first failed
    # but the dispatch continued instead of aborting.
    assert len(chart_events) == 1
    assert chart_events[0]["index"] == 1
    # The answer still reaches the user.
    tokens = [p for p in parsed if p.get("type") == "token"]
    assert tokens and any("Hi." in t["token"] for t in tokens)


def test_stream_chat_dispatches_to_genie(monkeypatch: Any) -> None:
    """``stream_chat`` delegates Genie endpoints to the kickoff/poll + answer pair.

    Phase 4 split the original ``_stream_genie`` into two sub-generators
    (``_stream_genie_kickoff_and_poll`` returns the final message via
    ``return`` so the caller can persist the message id and build a
    chart artifact before streaming the answer). We exercise the
    dispatch path end-to-end and only assert on the public SSE shape so
    we don't pin to internal helper names.
    """
    monkeypatch.setattr(
        chat_service, "_verify_access_best_effort", lambda *a, **k: None
    )
    monkeypatch.setattr(
        chat_service,
        "_ensure_conversation",
        lambda *a, **k: ("conv-g-1", True),
    )
    monkeypatch.setattr(chat_service, "_persist_message", lambda *a, **k: "msg-1")
    monkeypatch.setattr(chat_service, "_update_message_content", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "_update_conversation_title", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "_touch_conversation", lambda *a, **k: None)

    called: dict[str, Any] = {"answer_calls": 0}

    def _fake_kickoff(
        ws: Any, session: Any, space_id: str, conv_id: str, user_message: str
    ) -> Any:
        called["space_id"] = space_id
        called["conv_id"] = conv_id
        called["user_message"] = user_message
        yield chat_service._sse(
            {"type": "token", "token": "_status_", "done": False}
        )
        return (
            "_status_",
            {"attachments": [{"text": {"content": "G"}}]},
            "g-conv-1",
            "g-msg-1",
            "COMPLETED",
        )

    def _fake_answer(final_message: dict[str, Any], last_status: str | None) -> Any:
        called["answer_calls"] += 1
        yield chat_service._sse({"type": "token", "token": "G", "done": False})
        return "G"

    monkeypatch.setattr(
        chat_service, "_stream_genie_kickoff_and_poll", _fake_kickoff
    )
    monkeypatch.setattr(chat_service, "_stream_genie_answer", _fake_answer)

    # Charts/suggestions are gated behind feature flags + Lakebase. Force
    # them off so we don't call into the live services for this dispatch
    # test -- they have their own coverage in the service-level suites.
    monkeypatch.setattr(
        chat_service.feature_flags_service,
        "is_enabled",
        lambda *a, **k: False,
    )

    # The MAS path should not be exercised.
    monkeypatch.setattr(
        chat_service,
        "_stream_with_fallback",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("MAS path called for Genie")),
    )

    gen = chat_service.stream_chat(
        endpoint_name="genie:abc-123",
        conversation_id=None,
        user_message="hi genie",
        user_email="atika@example.com",
        ws=_ws(),
        session=MagicMock(),
        engine=None,
    )
    parsed = _parse_emitted(list(gen))

    assert called["space_id"] == "abc-123"
    assert called["user_message"] == "hi genie"
    assert called["answer_calls"] == 1
    types = [p.get("type") for p in parsed]
    assert types[0] == "started"
    assert types[-1] == "done"
    assert "token" in types
