"""Suggestion service -- context-aware follow-up question chips.

Two paths, one cache:

1. **Genie-native** -- Genie messages (``space.../messages/<id>``) include a
   ``suggested_questions`` / ``suggested_follow_ups`` field after a turn
   completes. These are free (already produced by the warehouse-side AI),
   so we always prefer them when they exist. ``extract_genie_suggestions``
   parses the message dict in scope from ``_stream_genie``.

2. **LLM fallback** -- everything else (MAS, KA, Models, External, UC HTTP,
   MCP) calls a configurable serving endpoint with the last user prompt
   and the assistant's reply, asking for three short, distinct follow-up
   questions. Per-agent-type model selection lives under
   ``feature_flags.ai_suggestions.models.<AGENT_TYPE>`` with a single
   default fallback (``DEFAULT_SUGGESTION_MODEL``).

Both paths upsert into ``suggestions_cache(message_id)`` so a conversation
reload never re-spends tokens. Suggestions stay scoped to a single
assistant message because the chips are a function of "what the user
just asked + what the agent just said" -- a future turn invalidates the
old context.

The streaming caller wraps the LLM path in a 1.5s wallclock budget via a
threadpool so a slow suggestion model never delays the ``done`` event.
The caller decides what to emit on timeout (we just expose the helpers).
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import ChatMessage
from sqlmodel import Session, text

from ..core._config import logger
from . import feature_flags_service

# Cap on the number of chips we surface. ECharts-style suggestion rails
# get noisy past three -- and three lets the UI keep them on a single line
# for typical desktop widths without wrapping.
MAX_SUGGESTIONS = 3

# Wallclock budget for the LLM call, including network. The streaming
# caller enforces this via a ThreadPoolExecutor; if exceeded we emit
# nothing so the ``done`` event is never blocked.
LLM_TIMEOUT_S = 1.5

# Hard cap on a single suggestion text. Anything longer is almost
# certainly the model rambling rather than producing a clean question.
MAX_SUGGESTION_CHARS = 140

# Trim user/assistant text to keep the prompt small (~1k tokens worst
# case). The point is "ask the next question", not summarize the world.
MAX_CONTEXT_CHARS = 600


# --------------------------------------------------------------------------- #
# Cache layer
# --------------------------------------------------------------------------- #


def get_cached(session: Session, message_id: str) -> list[str] | None:
    """Return cached suggestions for ``message_id`` or ``None`` on miss."""
    cached = get_cached_with_source(session, message_id)
    if cached is None:
        return None
    return cached[0]


def get_cached_with_source(
    session: Session, message_id: str
) -> tuple[list[str], str] | None:
    """Like :func:`get_cached` but also returns the recorded ``source``.

    The router's ``GET /messages/{id}/suggestions`` endpoint exposes
    ``source`` so the frontend can label chips ("from Genie" vs LLM
    fallback) and so analytics can split adoption by path. Returns
    ``None`` on a cache miss; the body is `(suggestions, source)`.
    """
    if not message_id:
        return None
    try:
        row = session.exec(
            text(
                """SELECT suggestions, source FROM suggestions_cache
                   WHERE message_id = CAST(:mid AS uuid)"""
            ).bindparams(mid=str(message_id))
        ).one_or_none()
    except Exception as e:
        logger.warning("suggestions_cache read failed for %s: %s", message_id, e)
        return None
    if not row or row[0] is None:
        return None
    raw = row[0]
    parsed: Any = raw if isinstance(raw, list) else None
    if parsed is None:
        try:
            parsed = json.loads(str(raw))
        except (ValueError, json.JSONDecodeError):
            return None
    if not isinstance(parsed, list):
        return None
    suggestions = _normalize_list(parsed)
    source = str(row[1]) if row[1] is not None else "fallback"
    return suggestions, source


def upsert_cache(
    session: Session, message_id: str, suggestions: list[str], source: str
) -> None:
    """Persist suggestions for ``message_id`` (idempotent)."""
    if not message_id or not suggestions:
        return
    try:
        session.exec(
            text(
                """INSERT INTO suggestions_cache (message_id, suggestions, source)
                   VALUES (CAST(:mid AS uuid), CAST(:sugg AS jsonb), :src)
                   ON CONFLICT (message_id) DO UPDATE SET
                        suggestions = EXCLUDED.suggestions,
                        source      = EXCLUDED.source,
                        created_at  = now()"""
            ).bindparams(
                mid=str(message_id),
                sugg=json.dumps(suggestions),
                src=source[:20],
            )
        )
        session.commit()
    except Exception as e:
        logger.warning("suggestions_cache write failed for %s: %s", message_id, e)
        try:
            session.rollback()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Genie-native path
# --------------------------------------------------------------------------- #


def extract_genie_suggestions(message: dict[str, Any]) -> list[str]:
    """Pull suggestion strings out of a completed Genie message dict.

    Genie has shipped multiple field names over time -- ``suggested_follow_ups``
    is the current name; older spaces still surface ``suggested_questions``;
    enterprise spaces sometimes nest both inside an ``attachments`` entry of
    type ``"text"``. Iterate every reasonable shape so we never miss a free
    set of suggestions.
    """
    if not isinstance(message, dict):
        return []

    candidates: list[Any] = []
    for key in ("suggested_follow_ups", "suggested_questions", "follow_ups"):
        v = message.get(key)
        if v:
            candidates.append(v)

    attachments = message.get("attachments")
    if isinstance(attachments, list):
        for att in attachments:
            if not isinstance(att, dict):
                continue
            for key in ("suggested_follow_ups", "suggested_questions", "follow_ups"):
                v = att.get(key)
                if v:
                    candidates.append(v)
            text_att = att.get("text")
            if isinstance(text_att, dict):
                for key in ("suggested_follow_ups", "suggested_questions", "follow_ups"):
                    v = text_att.get(key)
                    if v:
                        candidates.append(v)

    flat: list[str] = []
    for cand in candidates:
        if isinstance(cand, str):
            flat.append(cand)
            continue
        if isinstance(cand, list):
            for item in cand:
                if isinstance(item, str):
                    flat.append(item)
                elif isinstance(item, dict):
                    for k in ("text", "content", "question", "value"):
                        v = item.get(k)
                        if isinstance(v, str) and v.strip():
                            flat.append(v)
                            break
    return _normalize_list(flat)


# --------------------------------------------------------------------------- #
# LLM fallback path
# --------------------------------------------------------------------------- #


def generate_llm_suggestions(
    ws: WorkspaceClient,
    session: Session,
    *,
    agent_type: str,
    last_user: str,
    last_assistant: str,
    timeout_s: float = LLM_TIMEOUT_S,
) -> list[str]:
    """Call the configured suggestion model and return up to 3 question chips.

    Returns an empty list on any error / timeout / bad JSON. The caller is
    expected to fall back to "no suggestions" rather than retrying because
    the chips are a nice-to-have, not a blocking step in the chat flow.
    """
    if not (last_user or last_assistant):
        return []

    model = feature_flags_service.suggestion_model_for(session, agent_type or "")
    prompt = _build_prompt(last_user, last_assistant)

    def _call() -> str:
        try:
            response = ws.serving_endpoints.query(
                name=model,
                messages=[ChatMessage.from_dict({"role": "user", "content": prompt})],
                stream=False,
                max_tokens=200,
                temperature=0.4,
            )
        except Exception as e:
            logger.info("suggestion LLM call failed (%s): %s", model, e)
            return ""
        if hasattr(response, "choices") and response.choices:
            first = response.choices[0]
            if hasattr(first, "message") and first.message:
                content = getattr(first.message, "content", None)
                if isinstance(content, str):
                    return content
        return ""

    raw = ""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_call)
        try:
            raw = future.result(timeout=max(0.1, timeout_s))
        except FuturesTimeoutError:
            logger.info(
                "suggestion LLM call exceeded %.1fs budget on %s; skipping",
                timeout_s,
                model,
            )
            return []
        except Exception as e:
            logger.info("suggestion LLM call raised: %s", e)
            return []

    return _parse_llm_output(raw)


def _build_prompt(last_user: str, last_assistant: str) -> str:
    user_part = (last_user or "").strip()[:MAX_CONTEXT_CHARS]
    asst_part = (last_assistant or "").strip()[:MAX_CONTEXT_CHARS]
    return (
        "You are helping a user explore a chat conversation. Suggest exactly "
        f"{MAX_SUGGESTIONS} short, distinct follow-up questions the user could "
        "ask next based on the assistant's reply. Each question must:\n"
        "- be a single complete question ending with '?'\n"
        "- be no longer than 100 characters\n"
        "- not repeat the user's previous question\n"
        "- avoid yes/no questions when an open-ended one is more useful\n\n"
        f'Return ONLY strict JSON of the form: {{"suggestions": ["...", "...", "..."]}}.\n'
        "Do not include any other text, prose, or code fences.\n\n"
        f"USER: {user_part}\n\n"
        f"ASSISTANT: {asst_part}\n\n"
        "JSON:"
    )


def _parse_llm_output(raw: str) -> list[str]:
    """Best-effort parse of the LLM's JSON-ish output into a string list."""
    if not raw:
        return []
    text_blob = raw.strip()
    if not text_blob:
        return []

    candidate = text_blob
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text_blob, re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()

    parsed: Any = None
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        # Fallback: find the first JSON object in the body.
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(candidate[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                parsed = None

    if isinstance(parsed, dict):
        for key in ("suggestions", "follow_ups", "questions"):
            v = parsed.get(key)
            if isinstance(v, list):
                return _normalize_list(v)
        # Last-resort: dict-of-strings (rare).
        items = [v for v in parsed.values() if isinstance(v, str)]
        if items:
            return _normalize_list(items)
    if isinstance(parsed, list):
        return _normalize_list(parsed)

    # Final fallback: parse line-prefixed bullets ("1. ...", "- ...").
    bullets: list[str] = []
    for line in text_blob.splitlines():
        m = re.match(r"^\s*(?:[-*\u2022]|\d+[\.\)])\s+(.*\?)", line)
        if m:
            bullets.append(m.group(1).strip())
    return _normalize_list(bullets)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _normalize_list(items: list[Any]) -> list[str]:
    """De-dupe, trim, and clamp to ``MAX_SUGGESTIONS``.

    Preserves first-occurrence order so the most prominent suggestion stays
    in slot 0 (matters for keyboard navigation in the UI).
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            if isinstance(item, dict):
                for k in ("text", "content", "question", "value"):
                    v = item.get(k)
                    if isinstance(v, str):
                        item = v
                        break
                else:
                    continue
            else:
                continue
        s = " ".join(item.split()).strip()
        if not s:
            continue
        if len(s) > MAX_SUGGESTION_CHARS:
            # Trim on the last whitespace before the cap so we don't slice
            # mid-word.
            cut = s.rfind(" ", 0, MAX_SUGGESTION_CHARS)
            s = (s[:cut] if cut > 0 else s[:MAX_SUGGESTION_CHARS]).rstrip(",;:.")
            s = s + "?" if not s.endswith("?") else s
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out
