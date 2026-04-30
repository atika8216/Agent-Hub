"""Per-user UI preferences (theme, future expansion).

Phase 3 of the agent-hub roadmap introduces a dual-theme (light/dark/system)
toggle. We persist the choice per user so it follows them across devices,
and keep the surface area intentionally tiny -- one GET, one PUT. Anything
richer (density, accent color, localization) should bolt onto
:class:`UserPrefsUpdate` without changing the route shape.

The table is defined in :mod:`backend.core.lakebase` (``user_prefs``); all
queries here assume it exists. If Lakebase is unreachable the caller
should fall back to a session-scoped default rather than 500.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from sqlmodel import Session, text

from ..core._config import logger
from ..models import UserFeatureOverrides, UserPrefsOut, UserPrefsUpdate

ThemeMode = Literal["system", "light", "dark"]
_ALLOWED_THEMES: set[str] = {"system", "light", "dark"}
_DEFAULT_THEME: ThemeMode = "system"

# Allow-list of override keys so a stray PUT can't sneak unrelated keys
# into the JSONB blob. Mirrors
# :data:`feature_flags_service.ALLOWED_FEATURE_KEYS` -- keep them in sync.
_ALLOWED_OVERRIDE_KEYS: set[str] = {"ai_suggestions", "charts", "pinned"}


def _decode_overrides(raw: Any) -> UserFeatureOverrides:
    """Coerce a JSONB cell into a typed :class:`UserFeatureOverrides`.

    Returns the empty defaults when the value is null, malformed, or not
    a JSON object so a corrupt row cannot 500 the prefs read.
    """
    if raw is None:
        return UserFeatureOverrides()
    parsed: Any = raw if isinstance(raw, dict) else None
    if parsed is None:
        try:
            parsed = json.loads(str(raw))
        except (json.JSONDecodeError, ValueError):
            return UserFeatureOverrides()
    if not isinstance(parsed, dict):
        return UserFeatureOverrides()
    cleaned: dict[str, bool] = {}
    for k, v in parsed.items():
        if k in _ALLOWED_OVERRIDE_KEYS and isinstance(v, bool):
            cleaned[k] = v
    return UserFeatureOverrides(**cleaned)


def _default_prefs() -> UserPrefsOut:
    """Fallback when the user has never saved a preference."""
    return UserPrefsOut(
        theme=_DEFAULT_THEME,
        feature_overrides=UserFeatureOverrides(),
        updated_at=None,
    )


def get_prefs(session: Session, user_email: str) -> UserPrefsOut:
    """Return the stored prefs for ``user_email``, or the defaults.

    Missing rows are *not* an error -- brand-new users just see the default
    ``system`` theme until they flip the toggle.
    """
    if not user_email:
        return _default_prefs()

    row = session.exec(
        text(
            "SELECT theme, feature_overrides, updated_at "
            "FROM user_prefs WHERE user_email = :email"
        ).bindparams(email=user_email)
    ).first()
    if row is None:
        return _default_prefs()

    theme_raw = str(row[0]) if row[0] is not None else _DEFAULT_THEME
    theme: ThemeMode = theme_raw if theme_raw in _ALLOWED_THEMES else _DEFAULT_THEME  # type: ignore[assignment]
    overrides = _decode_overrides(row[1])
    updated_at = row[2] if isinstance(row[2], datetime) else None
    return UserPrefsOut(
        theme=theme,
        feature_overrides=overrides,
        updated_at=updated_at,
    )


def put_prefs(
    session: Session,
    user_email: str,
    update: UserPrefsUpdate,
) -> UserPrefsOut:
    """Upsert the caller's prefs. Unknown theme values collapse to ``system``.

    We intentionally do PATCH semantics inside a PUT: fields left as ``None``
    on the request keep their existing value. That keeps the client simple --
    it only has to send the field that changed.
    """
    if not user_email:
        # Anonymous requests shouldn't hit this route (the router guards it)
        # but returning defaults is safer than writing a row with an empty key.
        return _default_prefs()

    current = get_prefs(session, user_email)
    new_theme: ThemeMode = current.theme
    if update.theme is not None:
        if update.theme in _ALLOWED_THEMES:
            new_theme = update.theme  # type: ignore[assignment]
        else:
            # Belt-and-suspenders: pydantic's Literal should've already caught
            # this, but if a client sends something weird we fall back to
            # ``system`` rather than 422ing on a purely cosmetic setting.
            logger.warning(
                "user_prefs.put_prefs.invalid_theme user=%s value=%s",
                user_email,
                update.theme,
            )
            new_theme = _DEFAULT_THEME

    # Merge feature_overrides with PATCH semantics: only the fields the
    # caller explicitly set are updated. Pydantic's exclude_unset is the
    # cleanest way to detect "client passed null" vs "client omitted".
    new_overrides = current.feature_overrides
    if update.feature_overrides is not None:
        sent = update.feature_overrides.model_dump(exclude_unset=True)
        merged = {
            k: getattr(current.feature_overrides, k)
            for k in _ALLOWED_OVERRIDE_KEYS
        }
        for k, v in sent.items():
            if k in _ALLOWED_OVERRIDE_KEYS:
                merged[k] = v if v is None else bool(v)
        new_overrides = UserFeatureOverrides(**merged)

    overrides_dict = {
        k: v for k, v in new_overrides.model_dump().items() if v is not None
    }
    now = datetime.now(timezone.utc)
    session.exec(
        text(
            """
            INSERT INTO user_prefs (user_email, theme, feature_overrides, updated_at)
            VALUES (:email, :theme, CAST(:overrides AS jsonb), :updated_at)
            ON CONFLICT (user_email) DO UPDATE
            SET theme = EXCLUDED.theme,
                feature_overrides = EXCLUDED.feature_overrides,
                updated_at = EXCLUDED.updated_at
            """
        ).bindparams(
            email=user_email,
            theme=new_theme,
            overrides=json.dumps(overrides_dict),
            updated_at=now,
        )
    )
    session.commit()
    return UserPrefsOut(
        theme=new_theme,
        feature_overrides=new_overrides,
        updated_at=now,
    )
