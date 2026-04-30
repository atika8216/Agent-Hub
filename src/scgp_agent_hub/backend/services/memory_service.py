"""Memory service -- short-term and long-term context for chat agents.

Modes (admin_settings.memory_mode):
  - "off"        : no history, no insights
  - "short_term" : recent conversation messages only (default)
  - "long_term"  : per-(user, endpoint) insights extracted from past conversations
  - "both"       : short-term history + long-term insights as a system prefix
"""

from __future__ import annotations

import json
from typing import Any, Literal

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import ChatMessage
from sqlmodel import Session, text

from ..core._config import logger

MemoryMode = Literal["off", "short_term", "long_term", "both"]
DEFAULT_MEMORY_MODE: MemoryMode = "short_term"
DEFAULT_LTM_MODEL = "databricks-meta-llama-3-3-70b-instruct"

MAX_HISTORY_MESSAGES = 20
MAX_LTM_INSIGHTS = 10


def get_memory_mode(session: Session) -> MemoryMode:
    """Read the memory_mode admin setting; default to 'short_term'."""
    try:
        row = session.exec(
            text("SELECT value FROM admin_settings WHERE key = 'memory_mode'")
        ).one_or_none()
    except Exception as e:
        logger.warning("Failed to read memory_mode setting: %s", e)
        return DEFAULT_MEMORY_MODE

    if row is None:
        return DEFAULT_MEMORY_MODE

    value = str(row[0]).strip().lower()
    if value in ("off", "short_term", "long_term", "both"):
        return value  # type: ignore[return-value]
    return DEFAULT_MEMORY_MODE


def _get_ltm_model(session: Session) -> str:
    """Read the optional ltm_model admin setting; default to llama-3.3-70b."""
    try:
        row = session.exec(
            text("SELECT value FROM admin_settings WHERE key = 'ltm_model'")
        ).one_or_none()
        if row and str(row[0]).strip():
            return str(row[0]).strip()
    except Exception:
        pass
    return DEFAULT_LTM_MODEL


def get_short_term_context(
    session: Session,
    conv_id: str,
    max_messages: int = MAX_HISTORY_MESSAGES,
) -> list[dict[str, str]]:
    """Load the most recent N messages from the conversation, in chronological order."""
    rows = session.exec(
        text(
            """SELECT role, content FROM messages
            WHERE conversation_id = CAST(:cid AS uuid)
            ORDER BY created_at DESC
            LIMIT :lim"""
        ).bindparams(cid=conv_id, lim=max_messages)
    ).all()
    history = [{"role": str(r[0]), "content": str(r[1])} for r in rows]
    history.reverse()
    return history


def get_long_term_context(
    session: Session,
    user_email: str,
    endpoint_name: str,
    max_insights: int = MAX_LTM_INSIGHTS,
) -> str:
    """Return a formatted system message containing past insights, or empty string."""
    try:
        rows = session.exec(
            text(
                """SELECT insight FROM memory_long_term
                WHERE user_email = :email AND endpoint_name = :ep
                  AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY created_at DESC
                LIMIT :lim"""
            ).bindparams(email=user_email, ep=endpoint_name, lim=max_insights)
        ).all()
    except Exception as e:
        logger.warning("Failed to load long-term insights: %s", e)
        return ""

    if not rows:
        return ""

    bullet_lines = "\n".join(f"- {str(r[0])}" for r in rows)
    return (
        "Context from prior conversations with this user (use only if relevant):\n"
        f"{bullet_lines}"
    )


def build_context(
    session: Session,
    ws: WorkspaceClient,
    conv_id: str,
    user_email: str,
    endpoint_name: str,
) -> list[dict[str, str]]:
    """Assemble the full message list to send to the agent, based on memory mode.

    The returned list is what we pass through to `serving_endpoints.query`.
    The most recent user message is ALWAYS included regardless of mode -- without
    it the agent has no prompt to respond to.
    """
    mode = get_memory_mode(session)

    history: list[dict[str, str]] = []
    if mode in ("short_term", "both"):
        history = get_short_term_context(session, conv_id)

    if mode in ("long_term", "both"):
        ltm_block = get_long_term_context(session, user_email, endpoint_name)
        if ltm_block:
            history = [{"role": "system", "content": ltm_block}, *history]

    if not _has_user_message(history):
        latest = get_short_term_context(session, conv_id, max_messages=1)
        history = [*history, *latest]

    return history


def _has_user_message(history: list[dict[str, str]]) -> bool:
    return any(msg.get("role") == "user" for msg in history)


def extract_insights(
    session: Session,
    ws: WorkspaceClient,
    conv_id: str,
    user_email: str,
    endpoint_name: str,
) -> int:
    """Extract durable insights from the latest exchange and persist them.

    Returns the number of insights inserted. Designed to be safe to call from
    a daemon thread -- all errors are logged and swallowed.
    """
    try:
        recent = session.exec(
            text(
                """SELECT id, role, content FROM messages
                WHERE conversation_id = CAST(:cid AS uuid)
                ORDER BY created_at DESC
                LIMIT 4"""
            ).bindparams(cid=conv_id)
        ).all()
    except Exception as e:
        logger.warning("Insight extraction: failed to load messages: %s", e)
        return 0

    if not recent:
        return 0

    ordered = list(reversed(recent))
    last_user_msg_id = None
    for msg_id, role, _ in ordered:
        if str(role) == "user":
            last_user_msg_id = str(msg_id)

    transcript_lines = [f"{str(role).upper()}: {str(content)}" for _, role, content in ordered]
    transcript = "\n".join(transcript_lines)

    prompt = (
        "Extract durable, factual insights about the user from the conversation below. "
        "Insights should be reusable across future sessions (preferences, recurring goals, "
        "domain context, names, projects). Do NOT extract one-off facts, jokes, or transient state.\n\n"
        "Return strict JSON: an array of objects with fields `type` and `content`. "
        "`type` is one of: preference, fact, goal, context. "
        "Return [] if nothing durable was learned.\n\n"
        "Conversation:\n"
        f"{transcript}\n\n"
        "JSON:"
    )

    model = _get_ltm_model(session)
    raw_text = ""
    try:
        response = ws.serving_endpoints.query(
            name=model,
            messages=[ChatMessage.from_dict({"role": "user", "content": prompt})],
            stream=False,
            max_tokens=512,
            temperature=0.0,
        )
        if hasattr(response, "choices") and response.choices:
            first = response.choices[0]
            if hasattr(first, "message") and first.message and first.message.content:
                raw_text = first.message.content
    except Exception as e:
        logger.warning("Insight extraction LLM call failed: %s", e)
        return 0

    insights = _parse_insights(raw_text)
    if not insights:
        return 0

    inserted = 0
    for ins in insights:
        ins_type = ins.get("type", "fact")
        content = ins.get("content", "").strip()
        if not content:
            continue
        line = f"[{ins_type}] {content}"
        try:
            session.exec(
                text(
                    """INSERT INTO memory_long_term
                        (user_email, endpoint_name, insight, source_msg_id)
                    VALUES (:email, :ep, :ins, CAST(:src AS uuid))"""
                ).bindparams(
                    email=user_email,
                    ep=endpoint_name,
                    ins=line,
                    src=last_user_msg_id,
                )
            )
            inserted += 1
        except Exception as e:
            logger.warning("Failed to insert insight: %s", e)

    if inserted:
        try:
            session.commit()
        except Exception as e:
            logger.warning("Insight commit failed: %s", e)
            return 0

    logger.info(
        "Extracted %d insight(s) for %s on %s", inserted, user_email, endpoint_name
    )
    return inserted


def _parse_insights(raw: str) -> list[dict[str, Any]]:
    """Best-effort parse of the LLM's JSON output."""
    if not raw:
        return []
    text_blob = raw.strip()
    start = text_blob.find("[")
    end = text_blob.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    candidate = text_blob[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in parsed:
        if isinstance(item, dict):
            cleaned.append(item)
        elif isinstance(item, str) and item.strip():
            cleaned.append({"type": "fact", "content": item})
    return cleaned
