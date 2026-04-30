"""Feature flags resolver -- two-tier admin master + user opt-out.

Three switchable features ship under a single ``admin_settings.feature_flags``
JSON row plus a ``user_prefs.feature_overrides`` JSONB column:

- ``ai_suggestions``  -- context-aware question chips above the input
- ``charts``          -- ECharts auto-rendered from Genie SQL results
- ``pinned``          -- per-user per-agent saved questions

The resolver is the single source of truth used by the router (to gate UI
visibility) and by ``chat_service`` (to gate emit-side behavior). Every
feature is a two-tier toggle:

  effective = admin.enabled        # master kill switch
              AND (user_overrides.get(feature) is not False)
              AND admin.default_on  # admin's default for new users

Admins can set ``enabled=false`` to fully disable a feature without
touching code. ``default_on`` flips the default for users who have not
explicitly opted out. Per-user overrides are honored only while the master
switch is on -- once admin disables a feature, the per-user toggle is
greyed out in the UI.

The default JSON shape is documented inline in :data:`DEFAULT_FLAGS` so an
empty / missing row never breaks the resolver. We re-emit the defaults
on every read so a malformed JSON row reverts to safe behavior instead of
500ing the API.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from sqlmodel import Session, text

from ..core._config import logger

FeatureKey = Literal["ai_suggestions", "charts", "pinned"]
ALLOWED_FEATURE_KEYS: set[str] = {"ai_suggestions", "charts", "pinned"}

# Default suggestion model when an agent-type-specific override isn't set.
# Matches memory_service.DEFAULT_LTM_MODEL so deployments only need one
# Foundation Model API endpoint provisioned to use both features.
DEFAULT_SUGGESTION_MODEL = "databricks-meta-llama-3-3-70b-instruct"

# Conservative cap for chart payloads. ECharts will happily render >10k
# points but the network round-trip + browser layout cost climbs sharply
# past 5k. Admins can bump this in admin_settings if they have wide
# dashboards.
DEFAULT_CHART_MAX_ROWS = 5000

# Cap pins per agent so a runaway user doesn't blow up the rail.
DEFAULT_PIN_MAX_PER_AGENT = 30

# Shipping default: master kill ``enabled=false`` so a fresh deploy does
# not surface the new UI until an admin flips the switch. Matches the
# ``SCGP_DISABLE_UC_MCP_CHAT`` rollout pattern.
DEFAULT_FLAGS: dict[str, Any] = {
    "ai_suggestions": {
        "enabled": False,
        "default_on": True,
        "models": {"default": DEFAULT_SUGGESTION_MODEL},
    },
    "charts": {
        "enabled": False,
        "default_on": True,
        "max_rows": DEFAULT_CHART_MAX_ROWS,
    },
    "pinned": {
        "enabled": False,
        "default_on": True,
        "max_per_agent": DEFAULT_PIN_MAX_PER_AGENT,
    },
}


def _admin_flags(session: Session) -> dict[str, Any]:
    """Read ``admin_settings.feature_flags`` with safe fallback to defaults."""
    try:
        row = session.exec(
            text("SELECT value FROM admin_settings WHERE key = 'feature_flags'")
        ).one_or_none()
    except Exception as e:
        logger.warning("feature_flags read failed: %s", e)
        return DEFAULT_FLAGS

    if not row or row[0] is None:
        return DEFAULT_FLAGS

    raw = row[0]
    parsed: Any = None
    if isinstance(raw, dict):
        parsed = raw
    else:
        try:
            parsed = json.loads(str(raw))
        except (json.JSONDecodeError, ValueError):
            logger.warning("feature_flags stored value is not valid JSON; using defaults")
            return DEFAULT_FLAGS

    if not isinstance(parsed, dict):
        return DEFAULT_FLAGS

    # Merge with defaults so a partial JSON (e.g. only ``charts`` set) keeps
    # the other features at their defaults instead of disappearing.
    merged = {k: dict(v) for k, v in DEFAULT_FLAGS.items()}
    for k in ALLOWED_FEATURE_KEYS:
        v = parsed.get(k)
        if isinstance(v, dict):
            merged[k] = {**merged[k], **{kk: vv for kk, vv in v.items() if vv is not None}}
    return merged


def _user_overrides(session: Session, user_email: str) -> dict[str, Any]:
    """Read ``user_prefs.feature_overrides`` for ``user_email``.

    Returns an empty dict when the column is null, missing, or the user
    has no row -- callers treat absence as "honor admin default".
    """
    if not user_email:
        return {}
    try:
        row = session.exec(
            text(
                "SELECT feature_overrides FROM user_prefs WHERE user_email = :email"
            ).bindparams(email=user_email)
        ).one_or_none()
    except Exception as e:
        logger.warning("user feature_overrides read failed: %s", e)
        return {}

    if not row or row[0] is None:
        return {}
    raw = row[0]
    if isinstance(raw, dict):
        return {k: bool(v) for k, v in raw.items() if isinstance(k, str)}
    try:
        parsed = json.loads(str(raw))
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {k: bool(v) for k, v in parsed.items() if isinstance(k, str)}


def get_admin_flags(session: Session) -> dict[str, Any]:
    """Public read of the merged admin feature flags."""
    return _admin_flags(session)


def get_user_overrides(session: Session, user_email: str) -> dict[str, bool]:
    """Public read of a user's feature opt-outs (only ``False`` entries are meaningful)."""
    return _user_overrides(session, user_email)


def is_enabled(session: Session, user_email: str, key: FeatureKey) -> bool:
    """Two-tier resolution: master kill ANDed with per-user opt-out.

    - admin.enabled = false -> always False (master kill)
    - admin.enabled = true and user explicitly set override = false -> False
    - admin.enabled = true and no user override and admin.default_on = true -> True
    - admin.enabled = true and admin.default_on = false -> False (admin opted
      everyone out by default, even though the feature is technically allowed)
    """
    admin = _admin_flags(session).get(key) or {}
    if not bool(admin.get("enabled", False)):
        return False
    overrides = _user_overrides(session, user_email)
    if overrides.get(key) is False:
        return False
    return bool(admin.get("default_on", False))


def suggestion_model_for(session: Session, agent_type: str) -> str:
    """Resolve the suggestion model for an ``agent_type``.

    Admins can pin per-agent-type models under
    ``feature_flags.ai_suggestions.models.<AGENT_TYPE>`` (e.g. ``MAS``,
    ``GENIE_SPACE``, ``MCP_ENDPOINT``). The ``default`` slot is the
    fallback when no agent-type override is set.
    """
    flags = _admin_flags(session)
    suggestions = flags.get("ai_suggestions") or {}
    models = suggestions.get("models") or {}
    if not isinstance(models, dict):
        return DEFAULT_SUGGESTION_MODEL

    if agent_type:
        # Try an exact match first, then upper-case (admins can save either).
        for candidate in (agent_type, agent_type.upper(), agent_type.lower()):
            v = models.get(candidate)
            if isinstance(v, str) and v.strip():
                return v.strip()
    default = models.get("default")
    if isinstance(default, str) and default.strip():
        return default.strip()
    return DEFAULT_SUGGESTION_MODEL


def chart_max_rows(session: Session) -> int:
    """Cap row count for an ECharts payload (after Genie returns)."""
    flags = _admin_flags(session)
    charts = flags.get("charts") or {}
    raw = charts.get("max_rows", DEFAULT_CHART_MAX_ROWS)
    try:
        n = int(raw)
        return n if n > 0 else DEFAULT_CHART_MAX_ROWS
    except (TypeError, ValueError):
        return DEFAULT_CHART_MAX_ROWS


def pin_max_per_agent(session: Session) -> int:
    """Cap pinned questions per ``(user, endpoint)`` pair."""
    flags = _admin_flags(session)
    pinned = flags.get("pinned") or {}
    raw = pinned.get("max_per_agent", DEFAULT_PIN_MAX_PER_AGENT)
    try:
        n = int(raw)
        return n if n > 0 else DEFAULT_PIN_MAX_PER_AGENT
    except (TypeError, ValueError):
        return DEFAULT_PIN_MAX_PER_AGENT


def validate_feature_flags_blob(encoded: str) -> dict[str, Any]:
    """Parse + validate an admin-supplied ``feature_flags`` JSON blob.

    Raises :class:`ValueError` with a precise message so the admin sees
    why their PUT was rejected. Returns the parsed dict so the caller
    can re-encode in canonical form.
    """
    try:
        parsed = json.loads(encoded)
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"feature_flags must be a valid JSON object: {e}")
    if not isinstance(parsed, dict):
        raise ValueError("feature_flags must be a JSON object at the top level")

    for key in parsed.keys():
        if key not in ALLOWED_FEATURE_KEYS:
            raise ValueError(
                f"Unknown feature_flags key '{key}'. "
                f"Allowed: {sorted(ALLOWED_FEATURE_KEYS)}"
            )
    for key in ALLOWED_FEATURE_KEYS:
        v = parsed.get(key)
        if v is None:
            continue
        if not isinstance(v, dict):
            raise ValueError(f"feature_flags.{key} must be an object")
        for required_bool in ("enabled", "default_on"):
            if required_bool in v and not isinstance(v[required_bool], bool):
                raise ValueError(
                    f"feature_flags.{key}.{required_bool} must be a boolean"
                )
        if key == "charts" and "max_rows" in v:
            try:
                n = int(v["max_rows"])
                if n <= 0:
                    raise ValueError(
                        "feature_flags.charts.max_rows must be a positive integer"
                    )
            except (TypeError, ValueError):
                raise ValueError(
                    "feature_flags.charts.max_rows must be an integer"
                )
        if key == "pinned" and "max_per_agent" in v:
            try:
                n = int(v["max_per_agent"])
                if n <= 0:
                    raise ValueError(
                        "feature_flags.pinned.max_per_agent must be a positive integer"
                    )
            except (TypeError, ValueError):
                raise ValueError(
                    "feature_flags.pinned.max_per_agent must be an integer"
                )
        if key == "ai_suggestions" and "models" in v:
            models = v["models"]
            if not isinstance(models, dict):
                raise ValueError(
                    "feature_flags.ai_suggestions.models must be an object"
                )
            for mk, mv in models.items():
                if not isinstance(mk, str):
                    raise ValueError(
                        "feature_flags.ai_suggestions.models keys must be strings"
                    )
                if mv is not None and not isinstance(mv, str):
                    raise ValueError(
                        f"feature_flags.ai_suggestions.models.{mk} must be a string"
                    )
    return parsed
