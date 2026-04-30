"""Admin service -- read/write admin_settings and catalog_config."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from sqlmodel import Session, text

from ..core._config import logger
from ..models import (
    AdminSettingOut,
    AdminSettingsOut,
    AgentType,
    CatalogEntryOut,
    ManualUCEndpointIn,
    UCTagConfig,
    UCTagConfigUpdate,
)
from . import feature_flags_service
from .base import ConflictError, NotFoundError, ValidationError

ALLOWED_MEMORY_MODES = {"off", "short_term", "long_term", "both"}
FEATURE_FLAGS_KEY = "feature_flags"

# Key in ``admin_settings`` where the UC tag-config JSON is persisted.
# Defined here (not in chat/catalog services) because this is the write path.
UC_TAG_CONFIG_KEY = "uc_tag_config"

# Endpoint-name prefixes. Mirror the constants in catalog_service so we stay
# compatible with the discovery path -- we don't import from there because
# that module is heavy and pulls the Databricks SDK.
_UC_ENDPOINT_PREFIX = "uc:"
_MCP_ENDPOINT_PREFIX = "mcp:"

# UC identifiers allow letters, digits, and underscores. Pre-compiled to catch
# invalid full names (``my;catalog``, ``"sql-inject"``) before they reach the
# database. We deliberately DON'T allow backticks / dots within a segment --
# admins who need reserved-word names can add backtick support later.
_UC_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def get_all_settings(session: Session) -> AdminSettingsOut:
    """Return all admin_settings as a flat key->value map.

    Values are returned as their parsed form (string for now; JSON-decoded if possible).
    """
    rows = session.exec(
        text("SELECT key, value, updated_at FROM admin_settings ORDER BY key ASC")
    ).all()

    settings: dict[str, Any] = {}
    for r in rows:
        settings[str(r[0])] = _decode_value(r[1])
    return AdminSettingsOut(settings=settings)


def update_setting(
    session: Session, key: str, value: Any, user_email: str
) -> AdminSettingOut:
    """Upsert a single admin setting. Validates known keys."""
    key = (key or "").strip()
    if not key:
        raise ValidationError("Setting key is required")

    encoded = _encode_value(value)
    _validate_known_setting(key, encoded)

    try:
        session.exec(
            text(
                """INSERT INTO admin_settings (key, value, updated_at, updated_by)
                VALUES (:key, :val, NOW(), :who)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = NOW(),
                    updated_by = EXCLUDED.updated_by"""
            ).bindparams(key=key, val=encoded, who=user_email)
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    row = session.exec(
        text(
            "SELECT key, value, updated_at FROM admin_settings WHERE key = :key"
        ).bindparams(key=key)
    ).one_or_none()

    if not row:
        raise NotFoundError(f"Setting '{key}' not found after update")

    logger.info("Admin setting %s updated by %s -> %r", key, user_email, encoded)
    return AdminSettingOut(
        key=str(row[0]),
        value=_decode_value(row[1]),
        updated_at=row[2] if isinstance(row[2], datetime) else None,
    )


def list_catalog_entries(session: Session) -> list[CatalogEntryOut]:
    """Return every catalog row, including hidden ones, for admin management."""
    rows = session.exec(
        text(
            """SELECT endpoint_name, display_name, agent_type, visible, owner_email,
                metadata_json, updated_at
            FROM catalog_config
            ORDER BY display_name ASC NULLS LAST, endpoint_name ASC"""
        )
    ).all()

    entries: list[CatalogEntryOut] = []
    for r in rows:
        meta = _parse_metadata(r[5])
        entries.append(
            CatalogEntryOut(
                endpoint_name=str(r[0]),
                display_name=str(r[1] or r[0]),
                visible=bool(r[3]) if r[3] is not None else True,
                agent_type=str(r[2] or "MAS"),
                sub_agent_count=len(meta.get("sub_agents", []) or []),
                updated_at=r[6] if isinstance(r[6], datetime) else None,
            )
        )
    return entries


def update_catalog_entry(
    session: Session,
    endpoint_name: str,
    updates: dict[str, Any],
    user_email: str,
) -> CatalogEntryOut:
    """Patch visible/display_name/description on a catalog entry."""
    endpoint_name = (endpoint_name or "").strip()
    if not endpoint_name:
        raise ValidationError("endpoint_name is required")

    existing = session.exec(
        text(
            "SELECT endpoint_name FROM catalog_config WHERE endpoint_name = :name"
        ).bindparams(name=endpoint_name)
    ).one_or_none()
    if not existing:
        raise NotFoundError(f"Catalog entry '{endpoint_name}' not found")

    set_clauses: list[str] = []
    params: dict[str, Any] = {"name": endpoint_name}

    if "visible" in updates and updates["visible"] is not None:
        set_clauses.append("visible = :visible")
        params["visible"] = bool(updates["visible"])
    if "display_name" in updates and updates["display_name"] is not None:
        dn = str(updates["display_name"]).strip()
        if dn:
            set_clauses.append("display_name = :display_name")
            params["display_name"] = dn
    if "description" in updates and updates["description"] is not None:
        set_clauses.append("description = :description")
        params["description"] = str(updates["description"])

    if not set_clauses:
        return _entry_for(session, endpoint_name)

    set_clauses.append("updated_at = NOW()")
    sql = (
        "UPDATE catalog_config SET "
        + ", ".join(set_clauses)
        + " WHERE endpoint_name = :name"
    )

    try:
        session.exec(text(sql).bindparams(**params))
        session.commit()
    except Exception:
        session.rollback()
        raise

    logger.info(
        "Catalog entry %s patched by %s: %s",
        endpoint_name,
        user_email,
        list(updates.keys()),
    )
    return _entry_for(session, endpoint_name)


def _entry_for(session: Session, endpoint_name: str) -> CatalogEntryOut:
    row = session.exec(
        text(
            """SELECT endpoint_name, display_name, agent_type, visible, owner_email,
                metadata_json, updated_at
            FROM catalog_config WHERE endpoint_name = :name"""
        ).bindparams(name=endpoint_name)
    ).one_or_none()
    if not row:
        raise NotFoundError(f"Catalog entry '{endpoint_name}' not found")
    meta = _parse_metadata(row[5])
    return CatalogEntryOut(
        endpoint_name=str(row[0]),
        display_name=str(row[1] or row[0]),
        visible=bool(row[3]) if row[3] is not None else True,
        agent_type=str(row[2] or "MAS"),
        sub_agent_count=len(meta.get("sub_agents", []) or []),
        updated_at=row[6] if isinstance(row[6], datetime) else None,
    )


def get_uc_tag_config(session: Session) -> UCTagConfig:
    """Return the UC tag-config persisted in ``admin_settings``.

    Missing / malformed rows fall back to ``UCTagConfig`` defaults, so the
    discovery path always has something to work with even on a freshly
    provisioned database.
    """
    row = session.exec(
        text(
            "SELECT value FROM admin_settings WHERE key = :key"
        ).bindparams(key=UC_TAG_CONFIG_KEY)
    ).one_or_none()

    if not row or row[0] is None:
        return UCTagConfig()

    raw = row[0]
    parsed: dict[str, Any] = {}
    if isinstance(raw, dict):
        parsed = raw
    else:
        try:
            parsed = json.loads(str(raw))
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "uc_tag_config stored value is not valid JSON; falling back to defaults"
            )
            return UCTagConfig()

    return UCTagConfig(
        agent_tag_key=str(parsed.get("agent_tag_key") or UCTagConfig().agent_tag_key),
        agent_tag_value=str(parsed.get("agent_tag_value") or UCTagConfig().agent_tag_value),
        agent_kind_tag_key=str(
            parsed.get("agent_kind_tag_key") or UCTagConfig().agent_kind_tag_key
        ),
    )


def update_uc_tag_config(
    session: Session, updates: UCTagConfigUpdate, user_email: str
) -> UCTagConfig:
    """Patch the UC tag-config. Missing fields retain current values."""
    current = get_uc_tag_config(session)
    patched = UCTagConfig(
        agent_tag_key=(updates.agent_tag_key or current.agent_tag_key).strip()
                      or current.agent_tag_key,
        agent_tag_value=(updates.agent_tag_value or current.agent_tag_value).strip()
                        or current.agent_tag_value,
        agent_kind_tag_key=(updates.agent_kind_tag_key or current.agent_kind_tag_key).strip()
                           or current.agent_kind_tag_key,
    )

    encoded = json.dumps(patched.model_dump())
    try:
        session.exec(
            text(
                """INSERT INTO admin_settings (key, value, updated_at, updated_by)
                VALUES (:key, :val, NOW(), :who)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = NOW(),
                    updated_by = EXCLUDED.updated_by"""
            ).bindparams(key=UC_TAG_CONFIG_KEY, val=encoded, who=user_email)
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    logger.info("UC tag-config updated by %s -> %r", user_email, encoded)
    return patched


# -- Manual UC endpoint registration (Option C fallback) ---------------------
#
# When ``system.information_schema.function_tags`` / ``connection_tags`` are
# unavailable (e.g. UC v1 workspaces, pre-GA regions) the tag-discovery
# path can't surface UC-tagged agents. These helpers let an admin register
# one manually from the UI. We reuse the ``catalog_config`` table and the
# same ``uc:<full>`` / ``mcp:<full>`` endpoint_name contract so the chat
# dispatcher treats manual and discovered rows identically.


def _smart_title_leaf(name: str) -> str:
    """Titlecase a UC leaf name for the default display_name."""
    return " ".join(
        w.capitalize() if w else w for w in name.replace("_", " ").split()
    ) or name


def _validate_full_name(full_name: str, object_type: str) -> list[str]:
    """Split & validate a UC full name.

    Returns the cleaned segments or raises :class:`ValidationError`. The
    tuple returned is always either 2 (connection) or 3 (function) entries
    long depending on ``object_type``.
    """
    if not full_name or not full_name.strip():
        raise ValidationError("uc_full_name is required")
    parts = [p.strip() for p in full_name.strip().split(".")]
    if any(not p for p in parts):
        raise ValidationError("uc_full_name segments must be non-empty")
    expected = 3 if object_type == "function" else 2
    if len(parts) != expected:
        kind_name = "function" if object_type == "function" else "connection"
        raise ValidationError(
            f"{kind_name} uc_full_name must have {expected} dot-separated "
            f"segments (got {len(parts)})"
        )
    for p in parts:
        if not _UC_IDENT_RE.match(p):
            raise ValidationError(
                f"uc_full_name segment '{p}' is not a valid UC identifier "
                "(letters, digits, underscores; must start with letter or _)"
            )
    return parts


def _manual_endpoint_name(object_type: str, kind: str, full_name: str) -> str:
    # Mirror catalog_service._uc_endpoint_name / _mcp_endpoint_name. MCP kind
    # always rides the ``mcp:`` prefix regardless of function vs connection,
    # because the chat dispatcher keys off that prefix to pick the invoke
    # path.
    if kind == "mcp":
        return f"{_MCP_ENDPOINT_PREFIX}{full_name}"
    return f"{_UC_ENDPOINT_PREFIX}{full_name}"


def _invoke_shape_for(object_type: str, kind: str) -> str:
    # Must stay in sync with catalog_service._discover_uc_tagged -- the chat
    # dispatcher reads metadata.invoke_shape and branches on these exact
    # strings. Changing one without the other silently breaks chat routing.
    if object_type == "function":
        return "mcp" if kind == "mcp" else "uc_function_sql"
    # connection
    return "mcp_connection" if kind == "mcp" else "uc_connection_http"


def _metadata_kind_label(object_type: str, kind: str) -> str:
    # Matches the ``kind`` field written by discovery (``http_uc_function``,
    # ``mcp_uc_connection``, etc.). We mirror it so diagnostics / log lines
    # look the same whether a row came from discovery or a manual submit.
    prefix = "mcp" if kind == "mcp" else "http"
    suffix = "function" if object_type == "function" else "connection"
    return f"{prefix}_uc_{suffix}"


def register_uc_endpoint(
    session: Session,
    payload: ManualUCEndpointIn,
    user_email: str,
) -> CatalogEntryOut:
    """Insert a manually-registered UC endpoint into ``catalog_config``.

    Raises :class:`ValidationError` on malformed input and
    :class:`ConflictError` if the same ``endpoint_name`` already exists
    (either from a previous manual submit or from tag discovery).
    """
    object_type = payload.object_type
    kind = payload.kind
    parts = _validate_full_name(payload.uc_full_name, object_type)
    full_name = ".".join(parts)

    leaf = parts[-1]
    display = (payload.display_name or "").strip() or _smart_title_leaf(leaf)
    description = (payload.description or "").strip()

    endpoint_name = _manual_endpoint_name(object_type, kind, full_name)
    agent_type = (
        AgentType.MCP_ENDPOINT if kind == "mcp" else AgentType.HTTP_CONNECTION
    )

    metadata: dict[str, Any] = {
        "kind": _metadata_kind_label(object_type, kind),
        "uc_full_name": full_name,
        "invoke_shape": _invoke_shape_for(object_type, kind),
        "kind_tag_value": kind,
        "manual": True,
        "registered_by": user_email,
    }

    existing = session.exec(
        text(
            "SELECT endpoint_name FROM catalog_config WHERE endpoint_name = :n"
        ).bindparams(n=endpoint_name)
    ).one_or_none()
    if existing:
        raise ConflictError(
            f"Endpoint '{endpoint_name}' already exists. Delete the existing "
            "entry before re-registering."
        )

    try:
        session.exec(
            text(
                """INSERT INTO catalog_config
                    (endpoint_name, display_name, description, agent_type,
                     visible, owner_email, metadata_json)
                VALUES (:name, :display, :desc, :agent_type, TRUE,
                        :owner, CAST(:meta AS jsonb))"""
            ).bindparams(
                name=endpoint_name,
                display=display,
                desc=description,
                agent_type=agent_type.value,
                owner=user_email,
                meta=json.dumps(metadata),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    logger.info(
        "Manual UC endpoint registered by %s: %s (kind=%s, object_type=%s)",
        user_email,
        endpoint_name,
        kind,
        object_type,
    )
    return _entry_for(session, endpoint_name)


def list_manual_uc_endpoints(session: Session) -> list[CatalogEntryOut]:
    """Return only the catalog rows that were created via manual registration.

    Filters on ``metadata_json->>'manual' = 'true'`` so the admin card shows
    just the rows an admin can safely delete (discovery-owned rows are
    managed via the discover / rescan path and would be re-inserted on the
    next discovery run anyway).
    """
    rows = session.exec(
        text(
            """SELECT endpoint_name, display_name, agent_type, visible,
                owner_email, metadata_json, updated_at
            FROM catalog_config
            WHERE (metadata_json->>'manual')::boolean IS TRUE
            ORDER BY display_name ASC NULLS LAST, endpoint_name ASC"""
        )
    ).all()

    entries: list[CatalogEntryOut] = []
    for r in rows:
        meta = _parse_metadata(r[5])
        entries.append(
            CatalogEntryOut(
                endpoint_name=str(r[0]),
                display_name=str(r[1] or r[0]),
                visible=bool(r[3]) if r[3] is not None else True,
                agent_type=str(r[2] or "HTTP_CONNECTION"),
                sub_agent_count=len(meta.get("sub_agents", []) or []),
                updated_at=r[6] if isinstance(r[6], datetime) else None,
            )
        )
    return entries


def unregister_uc_endpoint(
    session: Session,
    endpoint_name: str,
    user_email: str,
) -> None:
    """Delete a manually-registered UC endpoint.

    Refuses to delete rows that aren't flagged ``manual`` -- discovery-owned
    rows must be removed by re-running discovery after untagging the UC
    object. Raises :class:`NotFoundError` when the row doesn't exist or
    isn't manual.
    """
    endpoint_name = (endpoint_name or "").strip()
    if not endpoint_name:
        raise ValidationError("endpoint_name is required")

    row = session.exec(
        text(
            "SELECT metadata_json FROM catalog_config "
            "WHERE endpoint_name = :n"
        ).bindparams(n=endpoint_name)
    ).one_or_none()
    if not row:
        raise NotFoundError(f"Endpoint '{endpoint_name}' not found")

    meta = _parse_metadata(row[0])
    if not bool(meta.get("manual")):
        raise ValidationError(
            f"Endpoint '{endpoint_name}' is not manually registered; "
            "use the discovery path (untag + rescan) to remove it."
        )

    try:
        session.exec(
            text(
                "DELETE FROM catalog_config WHERE endpoint_name = :n"
            ).bindparams(n=endpoint_name)
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    logger.info(
        "Manual UC endpoint unregistered by %s: %s", user_email, endpoint_name
    )


def _validate_known_setting(key: str, encoded: str) -> None:
    if key == "memory_mode":
        if encoded not in ALLOWED_MEMORY_MODES:
            raise ValidationError(
                f"Invalid memory_mode '{encoded}'. Allowed: {sorted(ALLOWED_MEMORY_MODES)}"
            )
    elif key == FEATURE_FLAGS_KEY:
        # Reject malformed JSON / unknown feature keys before they hit the
        # resolver. The resolver is forgiving (falls back to defaults) but we
        # don't want admins typo-ing themselves into a silent disable.
        try:
            feature_flags_service.validate_feature_flags_blob(encoded)
        except ValueError as e:
            raise ValidationError(str(e))


def _encode_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value)


def _decode_value(raw: Any) -> Any:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return ""
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if s.startswith("{") or s.startswith("["):
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return s
    return s


def _parse_metadata(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}
