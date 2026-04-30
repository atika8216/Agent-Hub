"""Chat service -- streaming chat, conversation CRUD.

TODO(clarity-chat-polish): per-conversation "Export as report" is an
intentional follow-up (see ``.cursor/plans/clarity_chat_polish_dde484b7.plan.md``
section D). Goal: one-click rendering of a chat into a clean printable
layout -- answer text + embedded chart PNGs + SQL collapsed, no Genie
progress chatter -- openable in a new tab and printable to PDF via the
browser.

Prereqs already in place from this plan:

1. Genie progress placeholders no longer persisted in
   ``messages.content`` (see ``_persist_message`` seed + post-stream
   ``_update_message_content`` in the Genie branch).
2. Multi-chart support via the ``idx`` column on ``chart_artifacts``
   and ``GET /messages/{message_id}/charts`` so a report endpoint can
   hydrate every artifact in deterministic order.

Missing: a new ``GET /conversations/{id}/report`` route that returns
either printable HTML or a JSON bundle the frontend can render. Keep
the SQL hidden behind a ``<details>`` element so the report stays
readable but still auditable. Scope is NOT in this bundle -- do not
implement here.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Generator, Iterable
from typing import Any

import httpx
from databricks.sdk import WorkspaceClient
from sqlalchemy import Engine
from sqlmodel import Session, text

from ..core._config import logger
from ..models import (
    ConversationDetailOut,
    ConversationListOut,
    ConversationSummary,
    DeleteResult,
    MessageOut,
)
from . import (
    chart_service,
    feature_flags_service,
    memory_service,
    suggestion_service,
)
from .base import ForbiddenError, NotFoundError


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data)}\n\n"


# --------------------------------------------------------------------------- #
# Genie endpoint identifier helpers
# --------------------------------------------------------------------------- #
# Genie Spaces are persisted in ``catalog_config`` under the synthetic
# endpoint name ``genie:<space_id>`` (see catalog_service._GENIE_ENDPOINT_PREFIX).
# We re-declare the constant here rather than import it, both to avoid a
# circular import with catalog_service (which uses chat_service helpers in
# tests) and to keep this module self-contained for the streaming path.
_GENIE_ENDPOINT_PREFIX = "genie:"

# UC-tagged catalog entries (Phase 1 of the master roadmap). See
# catalog_service._UC_ENDPOINT_PREFIX / _MCP_ENDPOINT_PREFIX. Duplicated
# here to stay consistent with the _GENIE_ENDPOINT_PREFIX pattern and
# avoid import cycles.
_UC_ENDPOINT_PREFIX = "uc:"
_MCP_ENDPOINT_PREFIX = "mcp:"


def _is_genie(endpoint_name: str) -> bool:
    """Return True when the identifier is a Genie Space (``genie:<id>``)."""
    return bool(endpoint_name) and endpoint_name.startswith(_GENIE_ENDPOINT_PREFIX)


def _genie_space_id(endpoint_name: str) -> str:
    """Strip the ``genie:`` prefix; caller is expected to verify via _is_genie."""
    return endpoint_name[len(_GENIE_ENDPOINT_PREFIX):]


def _is_uc_connection(endpoint_name: str) -> bool:
    """Return True when the identifier is a UC-tagged HTTP agent (``uc:<full_name>``)."""
    return bool(endpoint_name) and endpoint_name.startswith(_UC_ENDPOINT_PREFIX)


def _is_mcp_endpoint(endpoint_name: str) -> bool:
    """Return True when the identifier is a UC-tagged MCP agent (``mcp:<full_name>``)."""
    return bool(endpoint_name) and endpoint_name.startswith(_MCP_ENDPOINT_PREFIX)


def _uc_full_name(endpoint_name: str) -> str:
    """Strip the ``uc:`` prefix; caller is expected to verify via _is_uc_connection."""
    return endpoint_name[len(_UC_ENDPOINT_PREFIX):]


def _mcp_full_name(endpoint_name: str) -> str:
    """Strip the ``mcp:`` prefix; caller is expected to verify via _is_mcp_endpoint."""
    return endpoint_name[len(_MCP_ENDPOINT_PREFIX):]


def stream_chat(
    endpoint_name: str,
    conversation_id: str | None,
    user_message: str,
    user_email: str,
    ws: WorkspaceClient,
    session: Session,
    engine: Engine | None = None,
    sp_ws: WorkspaceClient | None = None,
    tool_choice: str | None = None,
) -> Generator[str, None, None]:
    """Stream a chat response from a serving endpoint as SSE events.

    Attempts stream=True first; falls back to non-streaming on failure.
    If the active memory mode is 'long_term' or 'both', the assistant's reply is
    fed into a daemon thread that extracts insights into memory_long_term.

    All errors raised inside this generator are emitted as SSE error events so
    the client always sees a clean stream (the HTTP status is already 200 by
    the time iteration begins).
    """
    try:
        _verify_access_best_effort(endpoint_name, ws)

        conv_id, is_new = _ensure_conversation(
            session, conversation_id, endpoint_name, user_email, user_message
        )

        _persist_message(session, conv_id, "user", user_message)
    except (ForbiddenError, NotFoundError) as e:
        logger.info("Chat preflight rejected for %s: %s", endpoint_name, e)
        yield _sse({"type": "error", "error": str(e), "done": True, "conversation_id": ""})
        return
    except Exception as e:
        logger.exception("Chat preflight failed for %s", endpoint_name)
        yield _sse(
            {
                "type": "error",
                "error": f"Failed to start chat: {e}",
                "done": True,
                "conversation_id": "",
            }
        )
        return

    # Emit immediately so the client can flip the URL to /chat/$id and refresh
    # the conversation sidebar before any assistant tokens arrive.
    yield _sse({"type": "started", "conversation_id": str(conv_id)})

    # Genie spaces are NOT serving endpoints; they have their own conversation
    # API surface (/api/2.0/genie/spaces/{id}/...). Dispatch separately so the
    # rest of the pipeline (history-context build, SDK invocations) doesn't
    # try to query a non-existent endpoint.
    if _is_genie(endpoint_name):
        space_id = _genie_space_id(endpoint_name)
        try:
            kickoff_ctx = yield from _stream_genie_kickoff_and_poll(
                ws=ws,
                session=session,
                space_id=space_id,
                conv_id=conv_id,
                user_message=user_message,
            )
        except Exception as e:
            logger.error("Genie chat error for space %s: %s", endpoint_name, e)
            yield _sse(
                {
                    "type": "error",
                    "error": f"Genie error: {e}",
                    "done": True,
                    "conversation_id": str(conv_id),
                }
            )
            return

        # ``_status_prefix`` is the live-streaming concatenation of status
        # labels the kickoff/poll yielded to the SSE stream. We intentionally
        # DO NOT persist it -- the user saw those labels live; reloading
        # the transcript should show the clean answer only. See the
        # persist path below.
        _status_prefix, final_message, genie_conv_id, genie_message_id, last_status = kickoff_ctx

        # If kickoff/poll yielded an error event it returns the empty tuple
        # -- we already streamed the error and must NOT emit another `done`.
        if not final_message:
            return

        # Persist the assistant message *now* so chart_artifacts (FK on
        # messages.id) and suggestions_cache can reference it. We seed
        # with empty content on purpose: Genie's transient status
        # placeholders (``_Preparing warehouse..._`` / ``_Reviewing
        # context..._`` / ``_Generating SQL..._``) are fine as live SSE
        # tokens for the streaming UX, but persisting them bloats
        # reloaded transcripts with italic progress chatter (and doubles
        # up when Genie re-enters the same status). The real answer
        # lands via ``_update_message_content`` once the stream finishes.
        assistant_msg_id = _persist_message(
            session, conv_id, "assistant", ""
        )

        # Charts: Genie-only, gated on the feature flag and the presence
        # of one-or-more ``query`` attachments. Genie can emit several
        # per turn (primary + follow-up drill-downs) -- we iterate,
        # build one artifact per attachment, and emit one SSE ``chart``
        # event per artifact with a 0-based ``index`` so the UI can
        # render them in a stable stacked order. Per-attachment failures
        # log + skip; they must not block the textual answer or the
        # sibling charts.
        if feature_flags_service.is_enabled(session, user_email, "charts"):
            attachments = _genie_query_attachments(final_message)
            total = len(attachments)
            for idx, (attachment_id, attachment_title) in enumerate(attachments):
                try:
                    artifact = chart_service.build_chart_artifact(
                        ws=ws,
                        session=session,
                        space_id=space_id,
                        genie_conv_id=genie_conv_id,
                        genie_message_id=genie_message_id,
                        attachment_id=attachment_id,
                        assistant_message_id=assistant_msg_id,
                        conversation_id=conv_id,
                        title=attachment_title,
                        idx=idx,
                    )
                except Exception as e:
                    logger.warning(
                        "Chart build failed for attachment %d/%d: %s",
                        idx + 1, total, e,
                    )
                    artifact = None
                if artifact is not None:
                    yield _sse(
                        {
                            "type": "chart",
                            "message_id": assistant_msg_id,
                            "chart_id": artifact["chart_id"],
                            "kind": artifact["kind"],
                            "title": artifact.get("title") or "",
                            "option": artifact["option"],
                            "truncated": bool(artifact.get("truncated")),
                            "index": idx,
                            "total": total,
                        }
                    )

        # Now stream the textual answer.
        try:
            answer_text = yield from _stream_genie_answer(
                final_message=final_message,
                last_status=last_status,
            )
        except Exception as e:
            logger.error("Genie answer stream error for %s: %s", endpoint_name, e)
            yield _sse(
                {
                    "type": "error",
                    "error": f"Genie error: {e}",
                    "done": True,
                    "conversation_id": str(conv_id),
                }
            )
            return

        # Persist ONLY the clean answer body. ``answer_text`` carries a
        # leading ``\n\n`` separator from ``_stream_genie_answer`` when
        # ``last_status`` was emitted during polling -- strip it so the
        # first characters of the stored content are the actual answer.
        full_response = answer_text.lstrip("\n")
        _update_message_content(session, assistant_msg_id, full_response)

        if is_new and full_response:
            _update_conversation_title(session, conv_id, user_message)
        _touch_conversation(session, conv_id)

        # Suggestions: Genie-native first, LLM fallback off (Genie path uses
        # only the upstream suggestions to keep cost predictable).
        try:
            yield from _emit_suggestions(
                session=session,
                ws=ws,
                user_email=user_email,
                endpoint_name=endpoint_name,
                assistant_msg_id=assistant_msg_id,
                user_message=user_message,
                full_response=full_response,
                genie_message=final_message,
            )
        except Exception as e:
            logger.info("Suggestion emission skipped: %s", e)

        yield _sse({"type": "done", "done": True, "conversation_id": str(conv_id)})
        return

    # Phase 2: UC HTTP connections / functions and MCP endpoints are
    # invoked via the SP-backed dispatchers below. A kill switch
    # (SCGP_DISABLE_UC_MCP_CHAT=1) reverts to the Phase-1 friendly stub
    # so we can roll back without a code revert.
    if _is_uc_connection(endpoint_name) or _is_mcp_endpoint(endpoint_name):
        if os.environ.get("SCGP_DISABLE_UC_MCP_CHAT") == "1":
            kind_label = "MCP endpoint" if _is_mcp_endpoint(endpoint_name) else "HTTP connection"
            full_name = (
                _mcp_full_name(endpoint_name)
                if _is_mcp_endpoint(endpoint_name)
                else _uc_full_name(endpoint_name)
            )
            stub_text = (
                f"Chat for this {kind_label} (`{full_name}`) is temporarily "
                "disabled (SCGP_DISABLE_UC_MCP_CHAT). Please contact the "
                "workspace admin."
            )
            yield _sse({"type": "token", "token": stub_text, "done": False})
            _persist_message(session, conv_id, "assistant", stub_text)
            if is_new:
                _update_conversation_title(session, conv_id, user_message)
            _touch_conversation(session, conv_id)
            yield _sse({"type": "done", "done": True, "conversation_id": str(conv_id)})
            return

        full_response = ""
        try:
            if _is_uc_connection(endpoint_name):
                full_response = yield from _stream_http_connection(
                    user_ws=ws,
                    sp_ws=sp_ws,
                    session=session,
                    conv_id=conv_id,
                    endpoint_name=endpoint_name,
                    user_message=user_message,
                )
            else:
                full_response = yield from _stream_mcp(
                    user_ws=ws,
                    sp_ws=sp_ws,
                    session=session,
                    conv_id=conv_id,
                    endpoint_name=endpoint_name,
                    user_message=user_message,
                    tool_choice=tool_choice,
                )
        except Exception as e:
            kind_label = "MCP endpoint" if _is_mcp_endpoint(endpoint_name) else "HTTP connection"
            logger.exception("%s chat error for %s", kind_label, endpoint_name)
            yield _sse(
                {
                    "type": "error",
                    "error": f"{kind_label} error: {e}",
                    "done": True,
                    "conversation_id": str(conv_id),
                }
            )
            return

        assistant_msg_id = ""
        if full_response:
            assistant_msg_id = _persist_message(
                session, conv_id, "assistant", full_response
            )
        if is_new and full_response:
            _update_conversation_title(session, conv_id, user_message)
        _touch_conversation(session, conv_id)

        if assistant_msg_id and full_response:
            try:
                yield from _emit_suggestions(
                    session=session,
                    ws=ws,
                    user_email=user_email,
                    endpoint_name=endpoint_name,
                    assistant_msg_id=assistant_msg_id,
                    user_message=user_message,
                    full_response=full_response,
                )
            except Exception as e:
                logger.info("Suggestion emission skipped: %s", e)

        yield _sse({"type": "done", "done": True, "conversation_id": str(conv_id)})
        return

    history = memory_service.build_context(
        session=session,
        ws=ws,
        conv_id=conv_id,
        user_email=user_email,
        endpoint_name=endpoint_name,
    )

    full_response = ""
    try:
        full_response = yield from _stream_with_fallback(
            ws, endpoint_name, history
        )
    except Exception as e:
        logger.error("Serving endpoint error for %s: %s", endpoint_name, e)
        error_msg = f"Error calling agent: {e}"
        yield _sse(
            {
                "type": "error",
                "error": error_msg,
                "done": True,
                "conversation_id": str(conv_id),
            }
        )
        return

    assistant_msg_id = ""
    if full_response:
        assistant_msg_id = _persist_message(
            session, conv_id, "assistant", full_response
        )

    if is_new and full_response:
        _update_conversation_title(session, conv_id, user_message)

    _touch_conversation(session, conv_id)

    if full_response and engine is not None:
        mode = memory_service.get_memory_mode(session)
        if mode in ("long_term", "both"):
            _spawn_insight_extraction(
                engine=engine,
                ws=ws,
                conv_id=conv_id,
                user_email=user_email,
                endpoint_name=endpoint_name,
            )

    if assistant_msg_id and full_response:
        try:
            yield from _emit_suggestions(
                session=session,
                ws=ws,
                user_email=user_email,
                endpoint_name=endpoint_name,
                assistant_msg_id=assistant_msg_id,
                user_message=user_message,
                full_response=full_response,
            )
        except Exception as e:
            logger.info("Suggestion emission skipped: %s", e)

    yield _sse({"type": "done", "done": True, "conversation_id": str(conv_id)})


def _spawn_insight_extraction(
    engine: Engine,
    ws: WorkspaceClient,
    conv_id: str,
    user_email: str,
    endpoint_name: str,
) -> None:
    def _run() -> None:
        # Small delay to let the parent connection settle (dev PGlite is single-threaded).
        time.sleep(0.25)
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                with Session(bind=engine) as bg_session:
                    memory_service.extract_insights(
                        session=bg_session,
                        ws=ws,
                        conv_id=conv_id,
                        user_email=user_email,
                        endpoint_name=endpoint_name,
                    )
                return
            except Exception as e:
                last_err = e
                err_str = str(e).lower()
                if "closed" in err_str or "connection" in err_str:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                break
        if last_err is not None:
            logger.warning("Background insight extraction failed: %s", last_err)

    threading.Thread(target=_run, daemon=True).start()


def _verify_access_best_effort(endpoint_name: str, ws: WorkspaceClient) -> None:
    """Optional metadata read for observability only.

    OBO tokens may lack ``serving_endpoints.get`` scope while still allowing
    ``/invocations``. Real authorization is enforced by the serving layer on
    query, not by this preflight.

    Skips entirely for non-serving-endpoint identifiers (Genie spaces,
    UC-tagged functions / connections, MCP endpoints) because they have
    their own access paths and a ``serving_endpoints.get`` would always
    404 for them, drowning the logs.
    """
    if (
        _is_genie(endpoint_name)
        or _is_uc_connection(endpoint_name)
        or _is_mcp_endpoint(endpoint_name)
    ):
        return
    try:
        ws.serving_endpoints.get(endpoint_name)
    except Exception as e:
        logger.warning(
            "Skipping strict preflight for endpoint %s (proceeding to invocations): %s",
            endpoint_name,
            e,
        )


def _ensure_conversation(
    session: Session,
    conversation_id: str | None,
    endpoint_name: str,
    user_email: str,
    first_message: str,
) -> tuple[str, bool]:
    """Load existing or create new conversation. Returns (conv_id, is_new)."""
    if conversation_id:
        row = session.exec(
            text(
                "SELECT id, user_email FROM conversations WHERE id = CAST(:cid AS uuid)"
            ).bindparams(cid=conversation_id)
        ).one_or_none()
        if not row:
            raise NotFoundError(f"Conversation '{conversation_id}' not found")
        if str(row[1]) != user_email:
            raise ForbiddenError("Conversation belongs to another user")
        return str(row[0]), False

    conv_id = str(uuid.uuid4())
    title = first_message[:60].strip() or "New conversation"
    session.exec(
        text(
            """INSERT INTO conversations (id, user_email, endpoint_name, title)
            VALUES (CAST(:id AS uuid), :email, :ep, :title)"""
        ).bindparams(id=conv_id, email=user_email, ep=endpoint_name, title=title)
    )
    session.commit()
    return conv_id, True


def _persist_message(
    session: Session, conv_id: str, role: str, content: str
) -> str:
    msg_id = str(uuid.uuid4())
    session.exec(
        text(
            """INSERT INTO messages (id, conversation_id, role, content)
            VALUES (CAST(:id AS uuid), CAST(:cid AS uuid), :role, :content)"""
        ).bindparams(id=msg_id, cid=conv_id, role=role, content=content)
    )
    session.commit()
    return msg_id


def _update_message_content(session: Session, msg_id: str, content: str) -> None:
    """Replace a placeholder message body with the final streamed content.

    Used by the Genie path so we can persist the assistant message *before*
    the chart artifact (which has a FK on ``messages.id``) and still keep
    the on-disk text equal to what the user actually saw.
    """
    if not msg_id:
        return
    try:
        session.exec(
            text(
                "UPDATE messages SET content = :c WHERE id = CAST(:mid AS uuid)"
            ).bindparams(c=content, mid=msg_id)
        )
        session.commit()
    except Exception as e:
        logger.warning("Failed to update message %s content: %s", msg_id, e)
        try:
            session.rollback()
        except Exception:
            pass


def _agent_type_for(endpoint_name: str) -> str:
    """Cheap classifier for suggestion-model selection.

    Avoids importing catalog_service here to dodge a circular import. The
    suggestion router only needs broad buckets to pick the right model.
    """
    if _is_genie(endpoint_name):
        return "GENIE_SPACE"
    if _is_uc_connection(endpoint_name):
        return "HTTP_CONNECTION"
    if _is_mcp_endpoint(endpoint_name):
        return "MCP_ENDPOINT"
    # MAS / KA / Models / External all share the same /invocations shape;
    # callers can override per-endpoint via admin_settings if they need to.
    return "MAS"


def _emit_suggestions(
    session: Session,
    ws: WorkspaceClient,
    *,
    user_email: str,
    endpoint_name: str,
    assistant_msg_id: str,
    user_message: str,
    full_response: str,
    genie_message: dict[str, Any] | None = None,
) -> Generator[str, None, None]:
    """Resolve + cache + emit a ``suggestions`` SSE event when enabled.

    For Genie, prefers the upstream ``suggested_follow_ups`` (free); for
    every other path we call a configurable serving endpoint with a 1.5s
    wallclock budget. Failures are non-fatal -- we silently skip the
    event so ``done`` never blocks on the suggestion model.
    """
    if not assistant_msg_id:
        return
    if not feature_flags_service.is_enabled(session, user_email, "ai_suggestions"):
        return

    suggestions: list[str] = []
    source = "fallback"

    if genie_message is not None:
        suggestions = suggestion_service.extract_genie_suggestions(genie_message)
        if suggestions:
            source = "genie_native"

    if not suggestions:
        agent_type = _agent_type_for(endpoint_name)
        try:
            suggestions = suggestion_service.generate_llm_suggestions(
                ws,
                session,
                agent_type=agent_type,
                last_user=user_message,
                last_assistant=full_response,
            )
            if suggestions:
                source = "llm"
        except Exception as e:
            logger.info("suggestions LLM path failed: %s", e)
            suggestions = []

    if not suggestions:
        return

    suggestion_service.upsert_cache(session, assistant_msg_id, suggestions, source)
    yield _sse(
        {
            "type": "suggestions",
            "message_id": assistant_msg_id,
            "suggestions": suggestions,
            "source": source,
        }
    )


def _load_history(session: Session, conv_id: str) -> list[dict[str, str]]:
    rows = session.exec(
        text(
            "SELECT role, content FROM messages "
            "WHERE conversation_id = CAST(:cid AS uuid) ORDER BY created_at ASC"
        ).bindparams(cid=conv_id)
    ).all()
    return [{"role": str(r[0]), "content": str(r[1])} for r in rows]


# --------------------------------------------------------------------------- #
# Chunked streaming simulator (for non-streaming upstreams)
# --------------------------------------------------------------------------- #

# Default chunk size in characters. Tuned so a typical sentence (~80 chars)
# yields ~7 chunks with the cursor moving every ~20ms -- close enough to
# real model streaming that users perceive it as "live" output.
_CHUNK_CHARS_DEFAULT = 12
_CHUNK_DELAY_S_DEFAULT = 0.02


def _simulate_chunked_stream(
    full_text: str,
    chunk_chars: int = _CHUNK_CHARS_DEFAULT,
    delay_s: float = _CHUNK_DELAY_S_DEFAULT,
) -> Generator[str, None, None]:
    """Yield ``full_text`` as a sequence of SSE token events.

    Word-boundary aware: splits roughly every ``chunk_chars`` characters but
    extends to the next whitespace so we never split mid-word. Sleeps
    ``delay_s`` between yields so a one-shot upstream still produces a
    streaming UX in the browser.

    The empty string yields nothing. Single short words yield as a single
    chunk. Word delimiters (spaces, newlines) are preserved with their
    chunk so we don't lose markdown structure.
    """
    if not full_text:
        return

    n = len(full_text)
    pos = 0
    while pos < n:
        target = min(pos + max(1, chunk_chars), n)
        # Extend to the next whitespace so we don't split mid-word.
        end = target
        while end < n and not full_text[end].isspace():
            end += 1
        # Include the whitespace itself in this chunk so the next chunk
        # starts on a word boundary.
        while end < n and full_text[end].isspace() and end - target < 4:
            end += 1
        chunk = full_text[pos:end]
        if chunk:
            yield _sse({"type": "token", "token": chunk, "done": False})
            if delay_s > 0 and end < n:
                time.sleep(delay_s)
        pos = end


# --------------------------------------------------------------------------- #
# Genie Conversation API streaming
# --------------------------------------------------------------------------- #

# How long we'll wait for a Genie message to reach a terminal state before
# giving up. Genie SQL execution can be slow on large warehouses; 90s is
# generous without holding the SSE connection open indefinitely.
_GENIE_POLL_TIMEOUT_S = 90.0
_GENIE_POLL_INTERVAL_S = 1.0


def _genie_get_conv_id(session: Session, conv_id: str) -> str | None:
    """Read the linked Genie conversation_id from conversations.metadata_json."""
    try:
        row = session.exec(
            text(
                "SELECT metadata_json FROM conversations WHERE id = CAST(:cid AS uuid)"
            ).bindparams(cid=conv_id)
        ).one_or_none()
    except Exception as e:
        logger.warning("Genie conv lookup failed for %s: %s", conv_id, e)
        return None
    if not row or row[0] is None:
        return None
    raw = row[0]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return None
    if isinstance(raw, dict):
        v = raw.get("genie_conversation_id")
        return str(v) if v else None
    return None


def _genie_set_conv_id(session: Session, conv_id: str, genie_conv_id: str) -> None:
    """Persist the Genie conversation id into conversations.metadata_json."""
    try:
        session.exec(
            text(
                """UPDATE conversations
                      SET metadata_json = COALESCE(metadata_json, '{}'::jsonb)
                                          || jsonb_build_object('genie_conversation_id', :gid)
                    WHERE id = CAST(:cid AS uuid)"""
            ).bindparams(gid=str(genie_conv_id), cid=str(conv_id))
        )
        session.commit()
    except Exception as e:
        logger.warning("Failed to persist genie_conversation_id for %s: %s", conv_id, e)


def _genie_render_attachments(message: dict[str, Any]) -> str:
    """Build a human-readable answer body from a completed Genie message.

    Genie messages return ``attachments`` of two flavours:
      * ``text``: ``{"text": {"content": "..."}}`` -- the natural language answer
      * ``query``: ``{"query": {"description": "...", "query": "SELECT ..."}}``

    We render the text first (if any), then append the SQL inside a fenced
    ``sql`` markdown block so users can audit / copy what ran. Tabular
    results are out of scope for this version (link out to the Genie room
    for full row-level data).
    """
    parts: list[str] = []
    attachments = message.get("attachments") or []
    if not isinstance(attachments, list):
        return ""

    sql_blocks: list[str] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        text_att = att.get("text")
        if isinstance(text_att, dict):
            content = text_att.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
        query_att = att.get("query")
        if isinstance(query_att, dict):
            sql = query_att.get("query")
            description = query_att.get("description") or ""
            if isinstance(sql, str) and sql.strip():
                header = description.strip() if isinstance(description, str) else ""
                if header:
                    sql_blocks.append(f"_{header}_\n\n```sql\n{sql.strip()}\n```")
                else:
                    sql_blocks.append(f"```sql\n{sql.strip()}\n```")

    body = "\n\n".join(parts)
    if sql_blocks:
        body = (body + "\n\n" if body else "") + "\n\n".join(sql_blocks)
    return body


def _genie_query_attachments(message: dict[str, Any]) -> list[tuple[str, str]]:
    """Collect every ``query`` attachment on a completed Genie message.

    Returns a list of ``(attachment_id, title)`` tuples in the order
    Genie returned them -- that ordering is meaningful: Genie typically
    emits the primary query first and follow-up drill-downs after. The
    UI renders them as a stacked set so the user can scroll / pick a
    chart from a 1-of-N rail without a second round-trip.

    Title preference: the query's ``description`` (Genie's human-readable
    hint). No description falls back to an empty string; the caller
    uses the message text as a secondary title when present.

    Returns ``[]`` when the message is text-only or malformed.
    """
    if not isinstance(message, dict):
        return []
    attachments = message.get("attachments") or []
    if not isinstance(attachments, list):
        return []
    out: list[tuple[str, str]] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        query_att = att.get("query")
        if not isinstance(query_att, dict):
            continue
        att_id = att.get("attachment_id") or att.get("id") or ""
        if not att_id:
            continue
        desc = query_att.get("description")
        title = (desc or "").strip() if isinstance(desc, str) else ""
        out.append((str(att_id), title))
    return out


def _genie_status_label(status: str) -> str:
    """Map a Genie message status to a user-facing one-liner."""
    s = (status or "").upper()
    if s in ("ASKING_AI", "ASKING_AI_GENERATING_QUERY"):
        return "_Generating SQL..._\n\n"
    if s == "EXECUTING_QUERY":
        return "_Running query..._\n\n"
    if s in ("FETCHING_METADATA", "PENDING_WAREHOUSE"):
        return "_Preparing warehouse..._\n\n"
    if s == "QUERY_RESULT_EXPIRED":
        return "_Refreshing results..._\n\n"
    if s in ("SUBMITTED", "PENDING"):
        return "_Submitted..._\n\n"
    if s == "FILTERING_CONTEXT":
        return "_Reviewing context..._\n\n"
    return f"_Status: {s.lower().replace('_', ' ')}..._\n\n"


def _stream_genie_kickoff_and_poll(
    ws: WorkspaceClient,
    session: Session,
    space_id: str,
    conv_id: str,
    user_message: str,
) -> Generator[str, None, tuple[str, dict[str, Any], str, str, str | None]]:
    """Drive a Genie turn up to ``COMPLETED`` and yield status events.

    Returns ``(status_prefix_text, final_message, genie_conv_id,
    genie_message_id, last_status)`` to the caller via ``return`` so the
    next stage (chart emission + answer streaming) can run with full
    context. ``status_prefix_text`` is the running concatenation of all
    status labels we yielded so the caller can prepend it to the final
    answer when persisting.

    On error or timeout this generator yields an SSE error event and
    returns ``("", {}, "", "", None)`` so the caller can short-circuit.
    """
    genie_conv_id = _genie_get_conv_id(session, conv_id)
    empty: tuple[str, dict[str, Any], str, str, str | None] = ("", {}, "", "", None)

    # Step 1: kick off (start_conversation for first turn, message for follow-ups).
    try:
        if not genie_conv_id:
            start = ws.api_client.do(
                "POST",
                f"/api/2.0/genie/spaces/{space_id}/start-conversation",
                body={"content": user_message},
            )
            if not isinstance(start, dict):
                yield _sse({
                    "type": "error",
                    "error": "Genie start-conversation returned unexpected response",
                    "done": True,
                    "conversation_id": str(conv_id),
                })
                return empty
            genie_conv_id = str(
                start.get("conversation_id")
                or (start.get("conversation") or {}).get("id")
                or ""
            )
            message_id = str(
                start.get("message_id")
                or (start.get("message") or {}).get("id")
                or ""
            )
            if not genie_conv_id or not message_id:
                yield _sse({
                    "type": "error",
                    "error": "Genie start-conversation missing conversation_id/message_id",
                    "done": True,
                    "conversation_id": str(conv_id),
                })
                return empty
            _genie_set_conv_id(session, conv_id, genie_conv_id)
        else:
            sent = ws.api_client.do(
                "POST",
                f"/api/2.0/genie/spaces/{space_id}/conversations/{genie_conv_id}/messages",
                body={"content": user_message},
            )
            if not isinstance(sent, dict):
                yield _sse({
                    "type": "error",
                    "error": "Genie send-message returned unexpected response",
                    "done": True,
                    "conversation_id": str(conv_id),
                })
                return empty
            message_id = str(
                sent.get("message_id")
                or sent.get("id")
                or (sent.get("message") or {}).get("id")
                or ""
            )
            if not message_id:
                yield _sse({
                    "type": "error",
                    "error": "Genie send-message missing message_id",
                    "done": True,
                    "conversation_id": str(conv_id),
                })
                return empty
    except Exception as e:
        yield _sse({
            "type": "error",
            "error": f"Genie kickoff failed: {e}",
            "done": True,
            "conversation_id": str(conv_id),
        })
        return empty

    # Step 2: poll until terminal, emitting status transitions as tokens.
    deadline = time.monotonic() + _GENIE_POLL_TIMEOUT_S
    last_status: str | None = None
    full = ""
    final_message: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        try:
            msg = ws.api_client.do(
                "GET",
                f"/api/2.0/genie/spaces/{space_id}/conversations/{genie_conv_id}/messages/{message_id}",
            )
        except Exception as e:
            logger.warning("Genie poll error for %s/%s: %s", genie_conv_id, message_id, e)
            time.sleep(_GENIE_POLL_INTERVAL_S)
            continue

        if not isinstance(msg, dict):
            time.sleep(_GENIE_POLL_INTERVAL_S)
            continue

        status = str(msg.get("status") or "").upper()

        if status in ("FAILED", "CANCELLED"):
            err = msg.get("error") or {}
            err_msg = (
                err.get("message") if isinstance(err, dict) else None
            ) or "Genie returned an error"
            yield _sse({
                "type": "error",
                "error": str(err_msg),
                "done": True,
                "conversation_id": str(conv_id),
            })
            return empty

        if status == "COMPLETED":
            final_message = msg
            break

        # Emit status only on transition so we don't spam the stream.
        if status and status != last_status:
            label = _genie_status_label(status)
            full += label
            yield _sse({"type": "token", "token": label, "done": False})
            last_status = status

        time.sleep(_GENIE_POLL_INTERVAL_S)

    if final_message is None:
        yield _sse({
            "type": "error",
            "error": f"Genie response timed out after {_GENIE_POLL_TIMEOUT_S:.0f}s",
            "done": True,
            "conversation_id": str(conv_id),
        })
        return empty

    return (full, final_message, str(genie_conv_id), str(message_id), last_status)


def _stream_genie_answer(
    final_message: dict[str, Any],
    last_status: str | None,
) -> Generator[str, None, str]:
    """Stream the textual answer for a completed Genie message.

    Returns the rendered answer body so the caller can persist it. The
    leading separator (between status text and answer) is yielded only
    when there *was* a status emitted earlier so we don't introduce a
    spurious blank line when Genie returns instantly.
    """
    answer = _genie_render_attachments(final_message)
    if not answer:
        answer = "_(Genie completed without a textual answer.)_"

    full = ""
    if last_status is not None:
        sep = "\n\n"
        full += sep
        yield _sse({"type": "token", "token": sep, "done": False})

    for sse_chunk in _simulate_chunked_stream(answer):
        yield sse_chunk
    full += answer
    return full


# --------------------------------------------------------------------------- #
# Phase 2: UC HTTP connection / function streaming via SQL Statements REST
# --------------------------------------------------------------------------- #

# Admin warehouse used for UC function / HTTP connection execution. Same
# precedence as catalog_service._admin_warehouse_id (duplicated here to
# keep chat_service self-contained and avoid a circular import).
def _chat_admin_warehouse_id() -> str:
    return (
        os.environ.get("SCGP_ADMIN_WAREHOUSE_ID")
        or os.environ.get("DATABRICKS_WAREHOUSE_ID")
        or ""
    ).strip()


def _load_catalog_metadata(
    session: Session, endpoint_name: str
) -> tuple[dict[str, Any], str]:
    """Return ``(metadata_json, owner_email)`` for an endpoint, both best-effort."""
    try:
        row = session.exec(
            text(
                "SELECT metadata_json, owner_email FROM catalog_config WHERE endpoint_name = :name"
            ).bindparams(name=endpoint_name)
        ).one_or_none()
    except Exception as e:
        logger.warning("Catalog metadata lookup failed for %s: %s", endpoint_name, e)
        return {}, ""
    if not row:
        return {}, ""
    raw = row[0]
    meta: dict[str, Any] = {}
    if isinstance(raw, str):
        try:
            meta = json.loads(raw) or {}
        except (ValueError, json.JSONDecodeError):
            meta = {}
    elif isinstance(raw, dict):
        meta = raw
    return meta, str(row[1] or "")


def _quote_uc_ident(full_name: str) -> str:
    """Backtick-quote each part of a UC identifier (catalog.schema.name)."""
    parts = [p for p in (full_name or "").split(".") if p]
    return ".".join(f"`{p}`" for p in parts)


_HTTP_POLL_TIMEOUT_S = 90.0
_HTTP_POLL_INTERVAL_S = 1.0


def _check_uc_execute_privilege(
    user_ws: WorkspaceClient,
    full_name: str,
) -> tuple[bool, str | None]:
    """Best-effort ``EXECUTE`` probe via OBO against the user warehouse.

    Returns ``(granted, reason)``. ``granted=True`` means we either got a
    ``true`` row back from ``has_privilege`` or we could not run the probe
    (permissive default). ``granted=False`` returns only when the probe
    ran successfully and returned a ``false``. The probe is intentionally
    non-fatal: a missing warehouse or a transient error should NOT block
    the caller, the real authorization check happens at execution time.
    """
    warehouse_id = (
        os.environ.get("SCGP_USER_WAREHOUSE_ID")
        or os.environ.get("DATABRICKS_WAREHOUSE_ID")
        or ""
    ).strip()
    if not warehouse_id or not full_name:
        return True, "probe skipped (no user warehouse configured)"

    safe = _quote_uc_ident(full_name)
    stmt = f"SELECT has_privilege(current_user(), 'EXECUTE', 'FUNCTION', '{full_name}') AS ok"

    try:
        resp = user_ws.statement_execution.execute_statement(
            statement=stmt,
            warehouse_id=warehouse_id,
            wait_timeout="10s",
        )
        state = getattr(getattr(resp, "status", None), "state", None)
        state_s = str(state or "").upper()
        if state_s not in {"SUCCEEDED", "FINISHED"}:
            return True, f"probe state={state_s} (permissive default)"
        result = getattr(resp, "result", None)
        data = getattr(result, "data_array", None) or []
        if not data or not data[0]:
            return True, "probe returned no rows (permissive default)"
        val = data[0][0]
        if isinstance(val, str):
            granted = val.lower() == "true"
        else:
            granted = bool(val)
        return granted, None if granted else f"has_privilege(EXECUTE, FUNCTION, {safe}) returned false"
    except Exception as e:
        return True, f"probe error (permissive): {e}"


def _sp_sql_execute_and_poll(
    sp_ws: WorkspaceClient,
    warehouse_id: str,
    statement: str,
    parameters: list[dict[str, Any]] | None = None,
    *,
    on_status: "Generator[str, None, None] | None" = None,
) -> tuple[list[list[Any]], list[str]]:
    """Run a SQL statement via the SP client; poll until terminal.

    Returns ``(data_array, col_names)``. Raises ``RuntimeError`` on a
    failed / canceled state with the upstream error message attached.
    """
    kwargs: dict[str, Any] = {
        "statement": statement,
        "warehouse_id": warehouse_id,
        "wait_timeout": "30s",
    }
    if parameters:
        kwargs["parameters"] = parameters
    resp = sp_ws.statement_execution.execute_statement(**kwargs)

    statement_id = getattr(resp, "statement_id", None)
    deadline = time.monotonic() + _HTTP_POLL_TIMEOUT_S
    state = getattr(getattr(resp, "status", None), "state", None)
    state_s = str(state or "").upper()

    while state_s in {"PENDING", "RUNNING"}:
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"SQL statement {statement_id} timed out after {_HTTP_POLL_TIMEOUT_S:.0f}s"
            )
        time.sleep(_HTTP_POLL_INTERVAL_S)
        try:
            resp = sp_ws.statement_execution.get_statement(statement_id)
        except Exception as e:
            raise RuntimeError(f"SQL statement poll failed: {e}") from e
        state = getattr(getattr(resp, "status", None), "state", None)
        state_s = str(state or "").upper()

    if state_s not in {"SUCCEEDED", "FINISHED"}:
        err = getattr(getattr(resp, "status", None), "error", None)
        err_msg = getattr(err, "message", None) or str(err) if err else ""
        raise RuntimeError(
            f"SQL statement {statement_id} ended in state={state_s}"
            + (f": {err_msg}" if err_msg else "")
        )

    manifest = getattr(resp, "manifest", None)
    schema = getattr(manifest, "schema", None)
    cols = getattr(schema, "columns", None) or []
    col_names = [str(getattr(c, "name", "")) for c in cols]

    result = getattr(resp, "result", None)
    data = getattr(result, "data_array", None) or []
    return list(data), col_names


def _stream_http_connection(
    user_ws: WorkspaceClient,
    sp_ws: WorkspaceClient | None,
    session: Session,
    conv_id: str,
    endpoint_name: str,
    user_message: str,
) -> Generator[str, None, str]:
    """Invoke a UC function (or UC HTTP Connection) via SQL Statements REST.

    Runs under the SP client + admin warehouse because typical OBO tokens
    lack ``sql`` scope. Before the call we run a permissive
    ``has_privilege`` probe via OBO so a user who clearly lacks EXECUTE
    gets a clean 403 message. The actual enforcement is delegated to the
    warehouse + UC on statement execution.
    """
    meta, _owner = _load_catalog_metadata(session, endpoint_name)
    full_name = str(meta.get("uc_full_name") or _uc_full_name(endpoint_name))
    invoke_shape = str(meta.get("invoke_shape") or "uc_function_sql")
    started_at = time.monotonic()

    logger.info(
        "chat.uc_http start endpoint=%s shape=%s full_name=%s",
        endpoint_name, invoke_shape, full_name,
    )

    if sp_ws is None:
        msg = (
            "UC HTTP chat is unavailable: service principal workspace "
            "client is not configured. Ask the admin to set the SP "
            "credentials in app resources."
        )
        yield _sse({"type": "error", "error": msg, "done": True, "conversation_id": str(conv_id)})
        return ""

    warehouse_id = _chat_admin_warehouse_id()
    if not warehouse_id:
        msg = (
            "UC HTTP chat requires SCGP_ADMIN_WAREHOUSE_ID (or "
            "DATABRICKS_WAREHOUSE_ID) to route SQL to a warehouse. "
            "Please configure the admin warehouse."
        )
        yield _sse({"type": "error", "error": msg, "done": True, "conversation_id": str(conv_id)})
        return ""

    granted, reason = _check_uc_execute_privilege(user_ws, full_name)
    if not granted:
        msg = (
            f"You do not have EXECUTE on `{full_name}`. Ask the owner to "
            f"grant ``EXECUTE ON FUNCTION {full_name}`` to your account. "
            f"({reason or 'privilege check returned false'})"
        )
        yield _sse({"type": "error", "error": msg, "done": True, "conversation_id": str(conv_id)})
        return ""

    # Status token so the user sees activity during the warehouse spin-up.
    status_label = "_Calling HTTP connection..._\n\n"
    yield _sse({"type": "token", "token": status_label, "done": False})

    safe_name = _quote_uc_ident(full_name)

    if invoke_shape == "uc_connection_http":
        # UC HTTP connection invocation via system.ai.http_request. We
        # pass the user prompt as a JSON body; upstream contracts vary,
        # so we stick to a single-field {"message": "..."} shape.
        stmt = (
            "SELECT CAST(system.ai.http_request("
            f"connection => '{full_name}', "
            "method => 'POST', "
            "path => '/', "
            "json_object => named_struct('message', CAST(:msg AS STRING))"
            ") AS STRING) AS result"
        )
    else:
        # uc_function_sql -- plain UC function call with a single string arg.
        stmt = f"SELECT CAST({safe_name}(CAST(:msg AS STRING)) AS STRING) AS result"

    parameters = [{"name": "msg", "value": user_message, "type": "STRING"}]

    try:
        data, _cols = _sp_sql_execute_and_poll(
            sp_ws, warehouse_id, stmt, parameters
        )
    except RuntimeError as e:
        err_str = str(e)
        elapsed = time.monotonic() - started_at
        logger.warning(
            "chat.uc_http failed endpoint=%s shape=%s elapsed_ms=%d err=%s",
            endpoint_name, invoke_shape, int(elapsed * 1000), err_str,
        )
        yield _sse({
            "type": "error",
            "error": f"HTTP connection failed: {err_str}",
            "done": True,
            "conversation_id": str(conv_id),
        })
        return ""

    if not data or not data[0]:
        elapsed = time.monotonic() - started_at
        logger.info(
            "chat.uc_http empty_result endpoint=%s elapsed_ms=%d",
            endpoint_name, int(elapsed * 1000),
        )
        empty = "_(No result returned from the HTTP connection.)_"
        yield _sse({"type": "token", "token": empty, "done": False})
        return status_label + empty

    raw_result = data[0][0]
    result_text = "" if raw_result is None else str(raw_result)

    # Many UC functions return a JSON payload inside a STRING. If it
    # looks like JSON and has a recognizable text field, prefer that for
    # rendering; otherwise dump the whole thing.
    rendered = _render_http_result(result_text)

    elapsed = time.monotonic() - started_at
    logger.info(
        "chat.uc_http ok endpoint=%s shape=%s elapsed_ms=%d chars=%d",
        endpoint_name, invoke_shape, int(elapsed * 1000), len(rendered),
    )

    for sse_chunk in _simulate_chunked_stream(rendered):
        yield sse_chunk

    return status_label + rendered


def _render_http_result(raw: str) -> str:
    """Pretty-print a UC function / HTTP connection result for chat.

    Tries JSON decode; if the payload contains a conventional
    ``response`` / ``answer`` / ``content`` / ``text`` / ``message``
    field we render that directly. Otherwise we show the raw JSON in a
    fenced ``json`` block so markdown still renders cleanly.
    """
    if not raw:
        return ""
    stripped = raw.strip()
    if not stripped:
        return raw
    if stripped[0] not in "{[\"":
        return raw
    try:
        obj = json.loads(stripped)
    except (ValueError, json.JSONDecodeError):
        return raw

    # Scalar JSON strings: unwrap.
    if isinstance(obj, str):
        return obj

    if isinstance(obj, dict):
        for key in ("response", "answer", "content", "text", "message", "output"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val
        # No recognized text field -- dump as JSON code block.
        try:
            return "```json\n" + json.dumps(obj, indent=2, ensure_ascii=False) + "\n```"
        except Exception:
            return raw

    if isinstance(obj, list):
        try:
            return "```json\n" + json.dumps(obj, indent=2, ensure_ascii=False) + "\n```"
        except Exception:
            return raw

    return raw


# --------------------------------------------------------------------------- #
# Phase 2: MCP endpoint streaming via JSON-RPC over streamable-http / SSE
# --------------------------------------------------------------------------- #

_MCP_HTTP_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)


def _resolve_mcp_target(
    sp_ws: WorkspaceClient | None,
    user_ws: WorkspaceClient,
    meta: dict[str, Any],
    endpoint_name: str,
) -> tuple[str, str, str]:
    """Resolve ``(url, bearer_token, transport)`` for an MCP endpoint.

    Precedence for URL:
      1. ``metadata_json.mcp_url`` (cached from a previous call or manually
         set by an admin).
      2. UC Connection options via SP ``DESCRIBE CONNECTION EXTENDED
         <catalog.conn>`` (for ``mcp_connection`` shape).
      3. Databricks-managed MCP at ``{host}/api/2.0/mcp/<full_name>`` for
         ``mcp`` shape.

    Transport is ``streamable_http`` unless the URL ends with ``/sse`` or
    the options indicate ``sse``.

    Bearer:
      - External MCP: ``options.bearer_token`` if present.
      - Managed MCP: OBO ``user_ws.config.token``.
    """
    invoke_shape = str(meta.get("invoke_shape") or "mcp_connection")
    full_name = str(meta.get("uc_full_name") or _mcp_full_name(endpoint_name))
    cached_url = str(meta.get("mcp_url") or "").strip()
    cached_transport = str(meta.get("mcp_transport") or "").strip()

    url = cached_url
    bearer = ""
    transport = cached_transport or ""

    if not url and sp_ws is not None and invoke_shape == "mcp_connection" and full_name:
        options = _describe_uc_connection_options(sp_ws, full_name)
        url = str(options.get("url") or options.get("endpoint") or "").strip()
        bearer = str(
            options.get("bearer_token")
            or options.get("access_token")
            or options.get("token")
            or ""
        ).strip()
        transport = transport or str(options.get("transport") or "").strip()

    if not url:
        # Managed MCP fallback: the UC object resolved as an MCP function
        # and Databricks offers managed MCP at /api/2.0/mcp/<fn or space>.
        host = (user_ws.config.host or "").rstrip("/")
        if host and full_name:
            url = f"{host}/api/2.0/mcp/{full_name}"
            transport = transport or "streamable_http"

    if not bearer:
        # Default: call with the user's OBO token (managed MCP, any
        # Databricks-hosted MCP endpoint behind the same OAuth session).
        bearer = getattr(user_ws.config, "token", "") or ""

    if not transport:
        transport = "sse" if url.rstrip("/").endswith("/sse") else "streamable_http"

    return url, bearer, transport


def _describe_uc_connection_options(
    sp_ws: WorkspaceClient, full_name: str
) -> dict[str, str]:
    """Read a UC Connection's options as a flat string->string dict.

    Uses the SDK ``connections`` client when available (preferred; it
    returns typed options). Falls back to ``DESCRIBE CONNECTION
    EXTENDED`` on the admin warehouse if the SDK call raises.

    Returns an empty dict on any failure -- MCP dispatch will then fall
    back to the managed-MCP URL convention.
    """
    options: dict[str, str] = {}
    try:
        conn = sp_ws.connections.get(name_arg=full_name)
        raw = getattr(conn, "options", None) or {}
        if isinstance(raw, dict):
            options = {str(k): str(v) for k, v in raw.items() if v is not None}
    except Exception as e:
        logger.info("connections.get failed for %s, falling back to DESCRIBE: %s", full_name, e)

    if options:
        return options

    warehouse_id = _chat_admin_warehouse_id()
    if not warehouse_id:
        return {}
    try:
        data, cols = _sp_sql_execute_and_poll(
            sp_ws,
            warehouse_id,
            f"DESCRIBE CONNECTION EXTENDED {_quote_uc_ident(full_name)}",
        )
    except Exception as e:
        logger.warning("DESCRIBE CONNECTION failed for %s: %s", full_name, e)
        return {}

    # DESCRIBE CONNECTION EXTENDED returns rows like (name, value[, ...]).
    for row in data:
        if not row or len(row) < 2:
            continue
        key = str(row[0] or "").strip().lower()
        val = str(row[1] or "")
        if key and val and key not in options:
            options[key] = val
    return options


def _mcp_load_tools_cache(session: Session, conv_id: str) -> list[dict[str, Any]] | None:
    try:
        row = session.exec(
            text(
                "SELECT metadata_json FROM conversations WHERE id = CAST(:cid AS uuid)"
            ).bindparams(cid=conv_id)
        ).one_or_none()
    except Exception as e:
        logger.warning("MCP tools cache read failed for %s: %s", conv_id, e)
        return None
    if not row or row[0] is None:
        return None
    raw = row[0]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return None
    if isinstance(raw, dict):
        tools = raw.get("mcp_tools")
        if isinstance(tools, list):
            return [t for t in tools if isinstance(t, dict)]
    return None


def _mcp_save_tools_cache(
    session: Session, conv_id: str, tools: list[dict[str, Any]]
) -> None:
    try:
        payload = json.dumps({"mcp_tools": tools})
        session.exec(
            text(
                """UPDATE conversations
                      SET metadata_json = COALESCE(metadata_json, '{}'::jsonb)
                                          || CAST(:payload AS jsonb)
                    WHERE id = CAST(:cid AS uuid)"""
            ).bindparams(payload=payload, cid=str(conv_id))
        )
        session.commit()
    except Exception as e:
        logger.warning("MCP tools cache write failed for %s: %s", conv_id, e)


def _mcp_save_chosen_tool(session: Session, conv_id: str, tool_name: str) -> None:
    try:
        payload = json.dumps({"mcp_chosen_tool": tool_name})
        session.exec(
            text(
                """UPDATE conversations
                      SET metadata_json = COALESCE(metadata_json, '{}'::jsonb)
                                          || CAST(:payload AS jsonb)
                    WHERE id = CAST(:cid AS uuid)"""
            ).bindparams(payload=payload, cid=str(conv_id))
        )
        session.commit()
    except Exception as e:
        logger.warning("MCP chosen tool persist failed for %s: %s", conv_id, e)


def _mcp_load_chosen_tool(session: Session, conv_id: str) -> str | None:
    try:
        row = session.exec(
            text(
                "SELECT metadata_json FROM conversations WHERE id = CAST(:cid AS uuid)"
            ).bindparams(cid=conv_id)
        ).one_or_none()
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    raw = row[0]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return None
    if isinstance(raw, dict):
        v = raw.get("mcp_chosen_tool")
        if isinstance(v, str) and v:
            return v
    return None


def _mcp_pick_default_tool(tools: list[dict[str, Any]]) -> str | None:
    """Prefer an obvious chat tool; return its name or None if ambiguous."""
    if not tools:
        return None
    names_lower = {str(t.get("name") or "").lower(): t for t in tools}
    for preferred in ("chat", "ask", "query", "respond", "answer"):
        if preferred in names_lower:
            return str(names_lower[preferred].get("name") or preferred)
    if len(tools) == 1:
        return str(tools[0].get("name") or "")
    return None


def _mcp_jsonrpc(
    url: str,
    bearer: str,
    *,
    method: str,
    params: dict[str, Any] | None = None,
    request_id: int = 1,
    accept: str = "application/json, text/event-stream",
) -> dict[str, Any]:
    """POST a single JSON-RPC request to an MCP server and return the result.

    Handles both streamable-http (immediate JSON response) and SSE
    (chunked ``text/event-stream`` with a final ``data: {...}`` frame
    carrying the JSON-RPC response).
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": accept,
    }
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        body["params"] = params

    with httpx.Client(timeout=_MCP_HTTP_TIMEOUT) as client:
        resp = client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        ctype = (resp.headers.get("content-type") or "").lower()
        if "text/event-stream" in ctype:
            payload: dict[str, Any] | None = None
            for raw in resp.iter_lines():
                if not raw:
                    continue
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].lstrip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    parsed = json.loads(chunk)
                except (ValueError, json.JSONDecodeError):
                    continue
                # Last frame wins; MCP servers typically send a single
                # response frame but may prepend notifications.
                if isinstance(parsed, dict) and parsed.get("id") == request_id:
                    payload = parsed
            if payload is None:
                raise RuntimeError("MCP SSE stream ended without a response frame")
            return payload

        return resp.json()


def _mcp_result_to_text(result: Any) -> str:
    """Flatten an MCP ``tools/call`` result into plain text for chat rendering."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result

    if isinstance(result, dict):
        # MCP convention: {"content": [{"type": "text", "text": "..."}], "isError": bool}
        content = result.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                itype = str(item.get("type") or "").lower()
                if itype == "text":
                    t = item.get("text")
                    if isinstance(t, str):
                        parts.append(t)
                elif itype == "image":
                    mime = item.get("mimeType") or "image/png"
                    parts.append(f"_(image/{mime} returned)_")
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            if parts:
                return "\n\n".join(parts)
        # Non-standard shape: dump as JSON block.
        try:
            return "```json\n" + json.dumps(result, indent=2, ensure_ascii=False) + "\n```"
        except Exception:
            return str(result)

    try:
        return "```json\n" + json.dumps(result, indent=2, ensure_ascii=False) + "\n```"
    except Exception:
        return str(result)


def _stream_mcp(
    user_ws: WorkspaceClient,
    sp_ws: WorkspaceClient | None,
    session: Session,
    conv_id: str,
    endpoint_name: str,
    user_message: str,
    tool_choice: str | None = None,
) -> Generator[str, None, str]:
    """Invoke an MCP server (managed or external) and stream the tool result.

    Flow:
      1. Resolve ``(url, bearer, transport)``.
      2. ``initialize`` + ``tools/list`` (cache the tool list on the
         conversation for subsequent turns).
      3. Pick a tool: the explicit ``tool_choice`` param wins; otherwise
         a previously chosen tool cached on the conversation; otherwise
         a conventional chat tool (``chat`` / ``ask`` / ...); otherwise
         emit a ``needs_tool_choice`` event so the UI can render a picker.
      4. Call ``tools/call``; emit ``tool_call`` then ``tool_result`` SSE
         events and stream the flattened text.
    """
    meta, _owner = _load_catalog_metadata(session, endpoint_name)
    url, bearer, transport = _resolve_mcp_target(sp_ws, user_ws, meta, endpoint_name)
    started_at = time.monotonic()

    if not url:
        msg = (
            "MCP chat is unavailable: the UC Connection options do not "
            "include an MCP URL and no managed MCP host is configured."
        )
        yield _sse({"type": "error", "error": msg, "done": True, "conversation_id": str(conv_id)})
        return ""

    logger.info(
        "chat.mcp start endpoint=%s transport=%s url_host=%s",
        endpoint_name, transport, _extract_host(url),
    )

    try:
        init_resp = _mcp_jsonrpc(
            url,
            bearer,
            method="initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "scgp-agent-hub", "version": "0.1"},
            },
            request_id=1,
        )
        if init_resp.get("error"):
            raise RuntimeError(str(init_resp["error"].get("message") or init_resp["error"]))
    except Exception as e:
        yield _sse({
            "type": "error",
            "error": f"MCP initialize failed: {e}",
            "done": True,
            "conversation_id": str(conv_id),
        })
        return ""

    tools = _mcp_load_tools_cache(session, conv_id)
    if tools is None:
        try:
            tools_resp = _mcp_jsonrpc(
                url, bearer, method="tools/list", params={}, request_id=2,
            )
            if tools_resp.get("error"):
                raise RuntimeError(
                    str(tools_resp["error"].get("message") or tools_resp["error"])
                )
            raw_tools = (tools_resp.get("result") or {}).get("tools") or []
            tools = []
            for t in raw_tools:
                if not isinstance(t, dict):
                    continue
                tools.append({
                    "name": str(t.get("name") or ""),
                    "description": str(t.get("description") or ""),
                    "inputSchema": t.get("inputSchema") or {},
                })
            tools = [t for t in tools if t["name"]]
            _mcp_save_tools_cache(session, conv_id, tools)
        except Exception as e:
            yield _sse({
                "type": "error",
                "error": f"MCP tools/list failed: {e}",
                "done": True,
                "conversation_id": str(conv_id),
            })
            return ""

    chosen_name = tool_choice or _mcp_load_chosen_tool(session, conv_id)
    if chosen_name and not any(t["name"] == chosen_name for t in tools):
        # Stale choice; re-pick.
        chosen_name = None

    if not chosen_name:
        chosen_name = _mcp_pick_default_tool(tools)

    if not chosen_name:
        # Ambiguous -- ask the UI to render a picker.
        yield _sse({
            "type": "needs_tool_choice",
            "tools": tools,
            "done": False,
        })
        msg = (
            f"This MCP server exposes {len(tools)} tool(s). Pick one from "
            "the list above to continue the conversation."
        )
        yield _sse({"type": "token", "token": msg, "done": False})
        return msg

    _mcp_save_chosen_tool(session, conv_id, chosen_name)

    chosen_tool = next((t for t in tools if t["name"] == chosen_name), None)
    tool_input = _build_mcp_arguments(chosen_tool or {}, user_message)

    yield _sse({
        "type": "tool_call",
        "name": chosen_name,
        "input": tool_input,
        "done": False,
    })

    try:
        call_resp = _mcp_jsonrpc(
            url,
            bearer,
            method="tools/call",
            params={"name": chosen_name, "arguments": tool_input},
            request_id=3,
        )
        if call_resp.get("error"):
            raise RuntimeError(str(call_resp["error"].get("message") or call_resp["error"]))
        result = call_resp.get("result")
    except Exception as e:
        elapsed = time.monotonic() - started_at
        logger.warning(
            "chat.mcp tool_call_failed endpoint=%s tool=%s elapsed_ms=%d err=%s",
            endpoint_name, chosen_name, int(elapsed * 1000), e,
        )
        yield _sse({
            "type": "error",
            "error": f"MCP tools/call failed: {e}",
            "done": True,
            "conversation_id": str(conv_id),
        })
        return ""

    yield _sse({
        "type": "tool_result",
        "name": chosen_name,
        "is_error": bool((result or {}).get("isError")),
        "done": False,
    })

    rendered = _mcp_result_to_text(result) or "_(MCP tool returned no content.)_"

    for sse_chunk in _simulate_chunked_stream(rendered):
        yield sse_chunk

    elapsed = time.monotonic() - started_at
    logger.info(
        "chat.mcp ok endpoint=%s tool=%s elapsed_ms=%d chars=%d",
        endpoint_name, chosen_name, int(elapsed * 1000), len(rendered),
    )
    return rendered


def _build_mcp_arguments(
    tool: dict[str, Any], user_message: str
) -> dict[str, Any]:
    """Build the ``arguments`` payload for ``tools/call``.

    If the tool advertises an ``inputSchema`` with a required string
    property (e.g. ``message``, ``prompt``, ``query``, ``input``), we
    pass the user message under that key. Otherwise we default to
    ``{"message": user_message}`` which matches the plurality of chat
    MCP servers.
    """
    schema = tool.get("inputSchema") if isinstance(tool, dict) else {}
    if isinstance(schema, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        if isinstance(properties, dict):
            # Prefer a required string field, then any string field.
            for key in required if isinstance(required, list) else []:
                prop = properties.get(str(key))
                if isinstance(prop, dict) and str(prop.get("type") or "").lower() == "string":
                    return {str(key): user_message}
            for key, prop in properties.items():
                if isinstance(prop, dict) and str(prop.get("type") or "").lower() == "string":
                    return {str(key): user_message}
    return {"message": user_message}


def _extract_host(url: str) -> str:
    """Lightweight host extraction for structured logging (never logs full URL)."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc or ""
    except Exception:
        return ""


_STREAM_TIMEOUT = httpx.Timeout(connect=15.0, read=600.0, write=30.0, pool=15.0)


def _invocations_url(ws: WorkspaceClient, endpoint_name: str) -> str:
    host = (ws.config.host or "").rstrip("/")
    return f"{host}/serving-endpoints/{endpoint_name}/invocations"


def _auth_headers(ws: WorkspaceClient) -> dict[str, str]:
    token = ws.config.token or ""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream, application/json",
    }


def _is_field_mismatch(msg: str) -> bool:
    lower = msg.lower()
    return (
        "'messages'" in lower
        or "'input'" in lower
        or "input field" in lower
        or "messages field" in lower
    )


def _post_stream(
    ws: WorkspaceClient,
    endpoint_name: str,
    history: list[dict[str, str]],
) -> httpx.Response:
    """Open a streaming POST to /invocations with messages->input retry.

    Returns an httpx.Response with ``stream=True`` semantics. The caller MUST
    close it (use as a context manager).

    We send ``Accept: text/event-stream`` (no fallback) so any upstream that
    supports content negotiation will pick streaming. Upstreams that ignore
    the header still respond with ``application/json``; ``_emit_streamed``
    handles both shapes.
    """
    url = _invocations_url(ws, endpoint_name)
    headers = _auth_headers(ws)
    # Force streaming preference for the actual stream POST. This is more
    # specific than the default Accept set in _auth_headers (which keeps
    # JSON as a fallback for the non-streaming /invocations callers).
    headers["Accept"] = "text/event-stream"
    base_body = {"stream": True}

    client = httpx.Client(timeout=_STREAM_TIMEOUT)
    try:
        body = {"messages": history, **base_body}
        req = client.build_request("POST", url, headers=headers, json=body)
        resp = client.send(req, stream=True)
        if resp.status_code == 400:
            err_text = resp.read().decode("utf-8", errors="replace")
            resp.close()
            if _is_field_mismatch(err_text):
                logger.info("Endpoint %s uses 'input' field, retrying streaming", endpoint_name)
                body = {"input": history, **base_body}
                req = client.build_request("POST", url, headers=headers, json=body)
                resp = client.send(req, stream=True)
            else:
                client.close()
                raise httpx.HTTPStatusError(
                    f"{resp.status_code}: {err_text}", request=req, response=resp
                )
        resp.raise_for_status()
        # Attach client so caller can close it alongside response.
        resp._scgp_client = client  # type: ignore[attr-defined]
        return resp
    except Exception:
        client.close()
        raise


def _close_stream(resp: httpx.Response | None) -> None:
    if resp is None:
        return
    try:
        resp.close()
    except Exception:
        pass
    client = getattr(resp, "_scgp_client", None)
    if client is not None:
        try:
            client.close()
        except Exception:
            pass


def _query_endpoint(
    ws: WorkspaceClient,
    endpoint_name: str,
    history: list[dict[str, str]],
    stream: bool = False,
) -> Any:
    """Non-streaming POST to /invocations with messages->input retry.

    Streaming calls go through :func:`_post_stream`; this path is used for the
    fallback and for long-term memory insight extraction.
    """
    path = f"/serving-endpoints/{endpoint_name}/invocations"
    base_body = {"stream": bool(stream)}

    try:
        body = {"messages": history, **base_body}
        return ws.api_client.do("POST", path, body=body)
    except Exception as e:
        if _is_field_mismatch(str(e)):
            logger.info("Endpoint %s uses 'input' field, retrying", endpoint_name)
            body = {"input": history, **base_body}
            return ws.api_client.do("POST", path, body=body)
        raise


def _iter_sse_lines(lines: Iterable[str]) -> Generator[Any, None, None]:
    """Parse an SSE byte/line stream into JSON payloads.

    - Skips blank lines and non-``data:`` lines (comments, ``event:``, ``id:``).
    - Skips the terminator ``data: [DONE]``.
    - Silently ignores lines whose payload is not valid JSON.
    """
    for raw in lines:
        if raw is None:
            continue
        line = raw.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].lstrip()
        if not payload or payload == "[DONE]":
            continue
        try:
            yield json.loads(payload)
        except (ValueError, json.JSONDecodeError):
            continue


def _stream_with_fallback(
    ws: WorkspaceClient,
    endpoint_name: str,
    history: list[dict[str, str]],
) -> Generator[str, None, str]:
    """Try streaming; fall back to non-streaming if the response isn't streamed."""
    full_response = ""
    resp: httpx.Response | None = None
    try:
        resp = _post_stream(ws, endpoint_name, history)
        logger.info(
            "Streaming chat to %s (stream=true, content-type=%s)",
            endpoint_name,
            resp.headers.get("content-type", "?"),
        )
        full_response = yield from _emit_streamed(resp)
    except Exception as e:
        logger.info(
            "Streaming failed for %s (%s), falling back to non-streaming",
            endpoint_name,
            e,
        )
        full_response = ""
    finally:
        _close_stream(resp)

    if full_response:
        return full_response

    logger.info("Falling back to non-streaming invocation for %s", endpoint_name)
    response = _query_endpoint(ws, endpoint_name, history, stream=False)
    full_response = _extract_content(response)
    if not full_response:
        try:
            sample = str(response)[:600]
            logger.warning(
                "Empty extracted content for %s; sample: %s",
                endpoint_name,
                sample,
            )
        except Exception:
            pass
    if full_response:
        # Even though the upstream gave us a single blob, animate it into
        # the UI so the user sees progressive output (matches the streaming
        # path's UX). Logs the simulation so deploy verification can tell
        # which branch ran.
        logger.info(
            "Simulated chunked stream applied for %s (chars=%d)",
            endpoint_name,
            len(full_response),
        )
        yield from _simulate_chunked_stream(full_response)
    return full_response


def _emit_streamed(resp: httpx.Response) -> Generator[str, None, str]:
    """Consume an httpx streaming response and emit SSE token events.

    Handles two upstream shapes:

    1) ``text/event-stream``: ChatCompletion-style delta lines (``data: {...}``)
    2) ``application/json`` single-shot: the server ignored ``stream:true`` and
       sent back a full payload; we then animate that payload via
       :func:`_simulate_chunked_stream` so the UI still gets a streaming feel.
    """
    full = ""
    ctype = (resp.headers.get("content-type") or "").lower()

    if "text/event-stream" in ctype or "stream" in ctype:
        for chunk in _iter_sse_lines(resp.iter_lines()):
            token = _extract_chunk_token(chunk)
            if token:
                full += token
                yield _sse({"type": "token", "token": token, "done": False})
        return full

    body = resp.read()
    if not body:
        return full
    try:
        payload = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return full

    full = _extract_content(payload)
    if full:
        # Upstream returned application/json despite stream=True. Animate
        # it so the UI still feels streaming; without this users perceive
        # a single freeze + dump and assume streaming is broken.
        logger.info(
            "Upstream returned application/json (one-shot); applying simulated chunking (chars=%d)",
            len(full),
        )
        yield from _simulate_chunked_stream(full)
    return full


def _extract_chunk_token(chunk: Any) -> str:
    """Extract a single token from a streaming chunk (ChatCompletion or MAS)."""
    if isinstance(chunk, dict):
        choices = chunk.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                delta = first.get("delta") or {}
                if isinstance(delta, dict):
                    c = delta.get("content")
                    if isinstance(c, str) and c:
                        return c
                msg = first.get("message") or {}
                if isinstance(msg, dict):
                    c = msg.get("content")
                    if isinstance(c, str) and c:
                        return c
                t = first.get("text")
                if isinstance(t, str) and t:
                    return t
        delta = chunk.get("delta")
        if isinstance(delta, dict):
            t = delta.get("text") or delta.get("content")
            if isinstance(t, str) and t:
                return t
        out = chunk.get("output")
        if isinstance(out, list):
            return "".join(_collect_output_text(out))
    if hasattr(chunk, "choices") and chunk.choices:
        first = chunk.choices[0]
        if hasattr(first, "delta") and hasattr(first.delta, "content"):
            return first.delta.content or ""
        if hasattr(first, "message") and hasattr(first.message, "content"):
            return first.message.content or ""
        if hasattr(first, "text"):
            return first.text or ""
    return ""


def _extract_content(response: Any) -> str:
    """Extract assistant text from a serving endpoint response.

    Handles three formats:
      1) ChatCompletion-style: response.choices[0].message.content / .text
      2) MAS Responses-style:  response.output[*].content[*].text
      3) Generic dict fallback for response.as_dict() / response.__dict__
    """
    if hasattr(response, "choices") and response.choices:
        first = response.choices[0]
        if hasattr(first, "message") and first.message and getattr(first.message, "content", None):
            return first.message.content
        if hasattr(first, "text") and first.text:
            return first.text

    output = getattr(response, "output", None)
    if output:
        text_parts = _collect_output_text(output)
        if text_parts:
            return "".join(text_parts)

    raw: dict[str, Any] | None = None
    if hasattr(response, "as_dict") and callable(response.as_dict):
        try:
            raw = response.as_dict()
        except Exception:
            raw = None
    if raw is None:
        try:
            raw = dict(response)  # type: ignore[arg-type]
        except Exception:
            raw = None
    if isinstance(raw, dict):
        out = raw.get("output")
        if isinstance(out, list):
            text_parts = _collect_output_text(out)
            if text_parts:
                return "".join(text_parts)
        choices = raw.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str) and content:
                    return content
        for key in ("content", "text", "output_text"):
            v = raw.get(key)
            if isinstance(v, str) and v:
                return v

    return ""


def _collect_output_text(output: Any) -> list[str]:
    """Walk a MAS-style `output` array and collect text from message items."""
    parts: list[str] = []
    if not isinstance(output, list):
        return parts
    for item in output:
        item_type = _attr_or_key(item, "type")
        if item_type and item_type != "message":
            continue
        contents = _attr_or_key(item, "content")
        if isinstance(contents, str):
            parts.append(contents)
            continue
        if isinstance(contents, list):
            for c in contents:
                t = _attr_or_key(c, "text")
                if isinstance(t, str) and t:
                    parts.append(t)
    return parts


def _attr_or_key(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _update_conversation_title(
    session: Session, conv_id: str, user_message: str
) -> None:
    title = user_message[:60].strip()
    if len(user_message) > 60:
        title = title.rsplit(" ", 1)[0] + "..."
    try:
        session.exec(
            text(
                "UPDATE conversations SET title = :title WHERE id = CAST(:cid AS uuid)"
            ).bindparams(title=title, cid=conv_id)
        )
        session.commit()
    except Exception as e:
        logger.warning("Failed to update title for %s: %s", conv_id, e)


def _touch_conversation(session: Session, conv_id: str) -> None:
    try:
        session.exec(
            text(
                "UPDATE conversations SET updated_at = NOW() WHERE id = CAST(:cid AS uuid)"
            ).bindparams(cid=conv_id)
        )
        session.commit()
    except Exception:
        pass


def list_conversations(
    user_email: str,
    session: Session,
) -> ConversationListOut:
    rows = session.exec(
        text(
            """SELECT c.id, c.title, c.endpoint_name, c.created_at, c.updated_at,
                COALESCE(cc.display_name, c.endpoint_name) as display_name,
                (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) as msg_count,
                (SELECT content FROM messages m2 WHERE m2.conversation_id = c.id ORDER BY m2.created_at DESC LIMIT 1) as last_msg
            FROM conversations c
            LEFT JOIN catalog_config cc ON cc.endpoint_name = c.endpoint_name
            WHERE c.user_email = :email
            ORDER BY c.updated_at DESC"""
        ).bindparams(email=user_email)
    ).all()

    conversations = [
        ConversationSummary(
            id=str(r[0]),
            title=r[1] or "Untitled",
            endpoint_name=r[2] or "",
            created_at=r[3],
            updated_at=r[4],
            display_name=r[5] or r[2] or "",
            message_count=r[6] or 0,
            last_message_preview=(str(r[7])[:100] if r[7] else None),
        )
        for r in rows
    ]

    return ConversationListOut(conversations=conversations, total=len(conversations))


def get_conversation(
    conversation_id: str,
    user_email: str,
    session: Session,
) -> ConversationDetailOut:
    row = session.exec(
        text(
            """SELECT c.id, c.title, c.endpoint_name, c.user_email,
                COALESCE(cc.display_name, c.endpoint_name) as display_name
            FROM conversations c
            LEFT JOIN catalog_config cc ON cc.endpoint_name = c.endpoint_name
            WHERE c.id = CAST(:cid AS uuid)"""
        ).bindparams(cid=conversation_id)
    ).one_or_none()

    if not row:
        raise NotFoundError(f"Conversation '{conversation_id}' not found")
    if str(row[3]) != user_email:
        raise ForbiddenError("Conversation belongs to another user")

    # LEFT JOIN chart_artifacts + suggestions_cache so a single SELECT
    # returns everything the conversation pane needs to rehydrate. Both
    # tables FK on messages.id, so the joins are at most 1:1 per message.
    # Genie now emits one artifact per ``query`` attachment (can be >1 per
    # turn); we surface:
    #   - ``chart_id`` = the primary (idx=0, then oldest) artifact, so
    #     single-chart clients keep working unchanged.
    #   - ``chart_count`` = total artifacts on the message, so the UI
    #     knows whether to call ``GET /messages/{id}/charts`` for the
    #     full stacked set.
    msg_rows = session.exec(
        text(
            """
            SELECT
                m.id, m.role, m.content, m.created_at,
                (
                    SELECT ca.id::text
                    FROM chart_artifacts ca
                    WHERE ca.message_id = m.id
                    ORDER BY ca.idx ASC, ca.created_at ASC
                    LIMIT 1
                ) AS chart_id,
                (
                    SELECT COUNT(*) FROM chart_artifacts ca
                    WHERE ca.message_id = m.id
                ) AS chart_count,
                EXISTS(
                    SELECT 1 FROM suggestions_cache sc WHERE sc.message_id = m.id
                ) AS has_suggestions
            FROM messages m
            WHERE m.conversation_id = CAST(:cid AS uuid)
            ORDER BY m.created_at ASC
            """
        ).bindparams(cid=conversation_id)
    ).all()

    messages = [
        MessageOut(
            id=str(mr[0]),
            role=str(mr[1]),
            content=str(mr[2]),
            created_at=mr[3],
            chart_id=(str(mr[4]) if mr[4] is not None else None),
            chart_count=int(mr[5]) if mr[5] is not None else 0,
            has_suggestions=bool(mr[6]) if mr[6] is not None else False,
        )
        for mr in msg_rows
    ]

    return ConversationDetailOut(
        id=str(row[0]),
        title=row[1] or "Untitled",
        endpoint_name=row[2] or "",
        display_name=str(row[4]) if row[4] else (row[2] or ""),
        messages=messages,
    )


def delete_conversation(
    conversation_id: str,
    user_email: str,
    session: Session,
) -> DeleteResult:
    row = session.exec(
        text(
            "SELECT id, user_email FROM conversations WHERE id = CAST(:cid AS uuid)"
        ).bindparams(cid=conversation_id)
    ).one_or_none()

    if not row:
        raise NotFoundError(f"Conversation '{conversation_id}' not found")
    if str(row[1]) != user_email:
        raise ForbiddenError("Conversation belongs to another user")

    session.exec(
        text("DELETE FROM conversations WHERE id = CAST(:cid AS uuid)").bindparams(
            cid=conversation_id
        )
    )
    session.commit()

    return DeleteResult(deleted=True, id=conversation_id)
