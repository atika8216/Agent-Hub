"""Pin event service -- append-only telemetry for pin interactions.

Records ``create`` / ``update`` / ``delete`` / ``click`` events to the
``pin_events`` table so the dev team can answer "which pinned questions
actually get used" without building a dedicated analytics UI.

Design notes:

- **Best-effort writes.** Every call is wrapped in a blanket try/except
  that logs and swallows. A failed telemetry insert MUST NOT break the
  user's pin action -- the value here is signal quality over coverage.
- **Own transaction.** We commit (or rollback) inside ``record_event``
  so a failure here never pollutes the caller's transaction and a
  success persists even if the caller later rolls back their own work.
- **Snapshotted text/label.** Pin rows can be deleted; the event row
  keeps a copy so queries like "top pinned questions in the last 30d"
  still work when the underlying pin is gone.

Dev-team query patterns (Lakebase-only, no admin UI ships):

.. code-block:: sql

    -- Top 10 pinned questions per agent (last 30d, by clicks)
    SELECT endpoint_name, text, COUNT(*) AS clicks
    FROM pin_events
    WHERE event_type = 'click' AND created_at > now() - interval '30 days'
    GROUP BY endpoint_name, text
    ORDER BY clicks DESC
    LIMIT 10;

    -- Per-user pin activity (last 7d)
    SELECT user_email, event_type, COUNT(*)
    FROM pin_events
    WHERE created_at > now() - interval '7 days'
    GROUP BY 1, 2
    ORDER BY 1;

    -- Pin-to-click ratio per agent
    SELECT endpoint_name,
           SUM(CASE WHEN event_type = 'create' THEN 1 ELSE 0 END) AS creates,
           SUM(CASE WHEN event_type = 'click'  THEN 1 ELSE 0 END) AS clicks
    FROM pin_events
    WHERE created_at > now() - interval '30 days'
    GROUP BY endpoint_name
    ORDER BY clicks DESC;
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from sqlmodel import Session, text

from ..core._config import logger

PinEventType = Literal["create", "update", "delete", "click"]


def record_event(
    session: Session,
    *,
    user_email: str,
    endpoint_name: str,
    pin_id: str | None,
    event_type: PinEventType,
    text_value: str | None = None,
    label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Insert a single ``pin_events`` row. Returns the new id or ``None``.

    Never raises -- a DB / network failure is logged and swallowed so the
    caller's pin action is unaffected. The write commits on its own
    transaction via ``session.commit()`` so even if the caller
    subsequently rolls back, the event row remains.

    ``pin_id`` is nullable because delete events legitimately reference a
    pin that has already been removed. For all other event types the
    caller should pass a valid UUID.
    """
    if not user_email or not endpoint_name or not event_type:
        return None

    event_id = str(uuid.uuid4())
    meta_json = json.dumps(metadata or {}, default=str)

    try:
        session.exec(
            text(
                """INSERT INTO pin_events
                       (id, user_email, endpoint_name, pin_id, event_type,
                        text, label, metadata_json)
                   VALUES (CAST(:id AS uuid),
                           :email, :ep,
                           CASE WHEN :pin_id IS NULL
                                THEN NULL
                                ELSE CAST(:pin_id AS uuid)
                           END,
                           :etype, :txt, :label, CAST(:meta AS jsonb))"""
            ).bindparams(
                id=event_id,
                email=user_email,
                ep=endpoint_name,
                pin_id=pin_id,
                etype=event_type,
                txt=text_value,
                label=label,
                meta=meta_json,
            )
        )
        session.commit()
        return event_id
    except Exception as e:
        logger.warning(
            "pin_events insert failed (user=%s endpoint=%s event=%s): %s",
            user_email,
            endpoint_name,
            event_type,
            e,
        )
        try:
            session.rollback()
        except Exception:
            pass
        return None
