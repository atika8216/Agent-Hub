"""Pin service -- per-user, per-agent saved questions.

Backed by ``pinned_questions`` (see :mod:`backend.core.lakebase`). The
service owns:

- duplicate detection (the unique key
  ``(user_email, endpoint_name, text)`` is enforced at the DB level; we
  surface it as a :class:`ConflictError` instead of letting a 500 leak)
- per-agent quota enforcement via
  ``feature_flags.pinned.max_per_agent``
- ordering: a pin's ``position`` is an integer sort key (lower first);
  ``created_at DESC`` is the secondary sort so newer pins float to the
  top inside a tie. Reorder operations rewrite ``position`` only.

The service NEVER reads or mutates pins owned by a different user. The
router layer is responsible for resolving ``user_email`` from the OAuth
identity and passing it down -- this module trusts that scoping and uses
it as the partition key.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, text

from ..core._config import logger
from . import feature_flags_service, pin_event_service
from .base import ConflictError, NotFoundError, ValidationError

# Hard caps to keep a runaway client from sending pathological payloads
# even if the UI lets them through. Numbers chosen to be generous: the
# typical pin is one sentence (<200 chars) and a label is a short tag.
MAX_TEXT_CHARS = 2000
MAX_LABEL_CHARS = 120


def _normalize_text(value: str | None, *, field: str, max_len: int, required: bool) -> str | None:
    """Trim + length-cap a free-form pin field. Returns ``None`` for unset.

    ``required=True`` raises :class:`ValidationError` on empty, otherwise
    we coerce empty to ``None`` (so a PATCH that omits a field is a
    no-op rather than clobbering existing data).
    """
    if value is None:
        if required:
            raise ValidationError(f"{field} is required")
        return None
    s = " ".join(value.split()).strip()
    if not s:
        if required:
            raise ValidationError(f"{field} cannot be empty")
        return None
    if len(s) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters")
    return s


def _row_to_pin(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "user_email": str(row[1]),
        "endpoint_name": str(row[2]),
        "text": str(row[3]),
        "label": str(row[4]) if row[4] is not None else None,
        "position": int(row[5]) if row[5] is not None else 0,
        "created_at": row[6],
    }


def list_pins(
    session: Session, *, user_email: str, endpoint_name: str
) -> list[dict[str, Any]]:
    """Return this user's pins for ``endpoint_name`` in display order."""
    if not user_email or not endpoint_name:
        return []
    try:
        rows = session.exec(
            text(
                """SELECT id, user_email, endpoint_name, text, label, position, created_at
                   FROM pinned_questions
                   WHERE user_email = :email AND endpoint_name = :ep
                   ORDER BY position ASC, created_at DESC"""
            ).bindparams(email=user_email, ep=endpoint_name)
        ).all()
    except Exception as e:
        logger.warning(
            "list_pins read failed for %s / %s: %s", user_email, endpoint_name, e
        )
        return []
    return [_row_to_pin(r) for r in rows]


def create_pin(
    session: Session,
    *,
    user_email: str,
    endpoint_name: str,
    text_value: str,
    label: str | None = None,
    position: int | None = None,
) -> dict[str, Any]:
    """Insert a new pin. Enforces dedup + per-agent quota."""
    if not user_email:
        raise ValidationError("user_email is required")
    if not endpoint_name:
        raise ValidationError("endpoint_name is required")

    norm_text = _normalize_text(
        text_value, field="text", max_len=MAX_TEXT_CHARS, required=True
    )
    norm_label = _normalize_text(
        label, field="label", max_len=MAX_LABEL_CHARS, required=False
    )

    # Quota enforcement: read first, insert second. We accept the small
    # race window (two concurrent inserts could both pass the count check
    # and put us 1 over the cap) because the cap is advisory and the user
    # would just see ``max+1`` pins until they delete one. The DB unique
    # key still protects against duplicate text.
    cap = feature_flags_service.pin_max_per_agent(session)
    try:
        n_row = session.exec(
            text(
                """SELECT COUNT(*) FROM pinned_questions
                   WHERE user_email = :email AND endpoint_name = :ep"""
            ).bindparams(email=user_email, ep=endpoint_name)
        ).one_or_none()
    except Exception as e:
        logger.warning("pin count read failed: %s", e)
        n_row = None
    n = int(n_row[0]) if n_row else 0
    if n >= cap:
        raise ValidationError(
            f"You've hit the pin limit for this agent ({cap}). "
            "Remove an existing pin before adding another."
        )

    # Default position = max(position) + 1 so a new pin appears at the
    # end of the list. Callers can override by passing ``position`` (used
    # by drag-and-drop reordering when constructing a fresh pin).
    if position is None:
        try:
            mp_row = session.exec(
                text(
                    """SELECT COALESCE(MAX(position), -1) + 1
                       FROM pinned_questions
                       WHERE user_email = :email AND endpoint_name = :ep"""
                ).bindparams(email=user_email, ep=endpoint_name)
            ).one_or_none()
        except Exception:
            mp_row = None
        position = int(mp_row[0]) if mp_row and mp_row[0] is not None else 0

    pin_id = str(uuid.uuid4())
    try:
        session.exec(
            text(
                """INSERT INTO pinned_questions
                       (id, user_email, endpoint_name, text, label, position)
                   VALUES (CAST(:id AS uuid), :email, :ep, :txt, :label, :pos)"""
            ).bindparams(
                id=pin_id,
                email=user_email,
                ep=endpoint_name,
                txt=norm_text,
                label=norm_label,
                pos=int(position),
            )
        )
        session.commit()
    except IntegrityError as e:
        try:
            session.rollback()
        except Exception:
            pass
        # Unique violation = duplicate pin text for this (user, endpoint).
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            raise ConflictError(
                "You've already pinned that question for this agent."
            )
        raise
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        raise

    created = _fetch_one(session, pin_id) or {
        # Should never happen -- defensive only.
        "id": pin_id,
        "user_email": user_email,
        "endpoint_name": endpoint_name,
        "text": norm_text,
        "label": norm_label,
        "position": int(position),
        "created_at": None,
    }

    pin_event_service.record_event(
        session,
        user_email=user_email,
        endpoint_name=endpoint_name,
        pin_id=pin_id,
        event_type="create",
        text_value=created.get("text"),
        label=created.get("label"),
        metadata={"position": created.get("position", 0)},
    )

    return created


def update_pin(
    session: Session,
    *,
    user_email: str,
    endpoint_name: str,
    pin_id: str,
    label: str | None = None,
    position: int | None = None,
    label_set: bool = False,
    position_set: bool = False,
) -> dict[str, Any]:
    """Patch ``label`` and/or ``position`` on a pin. Owner-only."""
    if not pin_id:
        raise ValidationError("pin_id is required")

    existing = _fetch_one(session, pin_id)
    if (
        existing is None
        or existing.get("user_email") != user_email
        or existing.get("endpoint_name") != endpoint_name
    ):
        raise NotFoundError("Pin not found")

    if not label_set and not position_set:
        return existing

    new_label = existing.get("label")
    if label_set:
        new_label = _normalize_text(
            label, field="label", max_len=MAX_LABEL_CHARS, required=False
        )

    new_position = existing.get("position", 0)
    if position_set:
        if position is None:
            raise ValidationError("position cannot be null")
        try:
            new_position = int(position)
        except (TypeError, ValueError):
            raise ValidationError("position must be an integer")

    try:
        session.exec(
            text(
                """UPDATE pinned_questions
                       SET label = :label, position = :pos
                     WHERE id = CAST(:id AS uuid)
                       AND user_email = :email
                       AND endpoint_name = :ep"""
            ).bindparams(
                label=new_label,
                pos=int(new_position),
                id=pin_id,
                email=user_email,
                ep=endpoint_name,
            )
        )
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        raise

    refreshed = _fetch_one(session, pin_id)
    if refreshed is None:
        raise NotFoundError("Pin not found")

    # Emit a telemetry event only when the patch actually changed
    # something. We compare against the pre-computed ``new_label`` /
    # ``new_position`` (the normalized values we just wrote) rather than
    # the refreshed row so the diff doesn't depend on a post-UPDATE
    # re-read -- the UPDATE's own success is the authority on what's
    # currently in the row. Matters because the UI sends full-field
    # PATCH bodies on every drag-reorder, and recording a no-op would
    # pollute downstream activity counts.
    old_position = int(existing.get("position", 0) or 0)
    old_label = existing.get("label")
    label_changed = bool(label_set) and old_label != new_label
    position_changed = bool(position_set) and old_position != int(new_position)
    if label_changed or position_changed:
        pin_event_service.record_event(
            session,
            user_email=user_email,
            endpoint_name=endpoint_name,
            pin_id=pin_id,
            event_type="update",
            text_value=refreshed.get("text"),
            label=new_label,
            metadata={
                "position_from": old_position,
                "position_to": int(new_position),
                "label_changed": label_changed,
            },
        )

    return refreshed


def delete_pin(
    session: Session,
    *,
    user_email: str,
    endpoint_name: str,
    pin_id: str,
) -> None:
    """Delete a pin. Owner-only; missing/foreign pins -> 404."""
    if not pin_id:
        raise ValidationError("pin_id is required")

    # Snapshot the row BEFORE we delete so the telemetry event can
    # preserve the text + label (the pin row will be gone afterwards).
    # A missing snapshot is non-fatal; the DELETE itself is the source of
    # truth for whether the pin existed.
    snapshot = _fetch_one(session, pin_id)

    try:
        result = session.exec(
            text(
                """DELETE FROM pinned_questions
                    WHERE id = CAST(:id AS uuid)
                      AND user_email = :email
                      AND endpoint_name = :ep"""
            ).bindparams(id=pin_id, email=user_email, ep=endpoint_name)
        )
        rows = getattr(result, "rowcount", None)
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        raise

    if rows == 0:
        raise NotFoundError("Pin not found")

    pin_event_service.record_event(
        session,
        user_email=user_email,
        endpoint_name=endpoint_name,
        pin_id=pin_id,
        event_type="delete",
        text_value=(snapshot or {}).get("text"),
        label=(snapshot or {}).get("label"),
        metadata={"position": (snapshot or {}).get("position", 0)},
    )


def record_click(
    session: Session,
    *,
    user_email: str,
    endpoint_name: str,
    pin_id: str,
) -> bool:
    """Ownership-checked click telemetry. Returns whether a row was written.

    Verifies the pin exists AND belongs to ``user_email`` AND is pinned
    against ``endpoint_name``. A mismatch raises :class:`NotFoundError`
    so we don't leak existence of peer pins to a probing caller.

    The actual ``pin_events`` write is best-effort (see
    ``pin_event_service.record_event``), so a DB failure here is a
    logged no-op rather than an exception -- the user's click UX must
    stay instant regardless of telemetry health.
    """
    if not pin_id:
        raise ValidationError("pin_id is required")
    existing = _fetch_one(session, pin_id)
    if (
        existing is None
        or existing.get("user_email") != user_email
        or existing.get("endpoint_name") != endpoint_name
    ):
        raise NotFoundError("Pin not found")

    event_id = pin_event_service.record_event(
        session,
        user_email=user_email,
        endpoint_name=endpoint_name,
        pin_id=pin_id,
        event_type="click",
        text_value=existing.get("text"),
        label=existing.get("label"),
        metadata={"position": existing.get("position", 0)},
    )
    return event_id is not None


def _fetch_one(session: Session, pin_id: str) -> dict[str, Any] | None:
    try:
        row = session.exec(
            text(
                """SELECT id, user_email, endpoint_name, text, label, position, created_at
                   FROM pinned_questions
                   WHERE id = CAST(:id AS uuid)"""
            ).bindparams(id=pin_id)
        ).one_or_none()
    except Exception as e:
        logger.warning("pin fetch failed for %s: %s", pin_id, e)
        return None
    if not row:
        return None
    return _row_to_pin(row)
