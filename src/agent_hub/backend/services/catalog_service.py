"""Catalog service -- agent discovery, listing, detail, and access checking."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from databricks.sdk import WorkspaceClient
from sqlmodel import Session, text

from ..core._config import logger
from ..models import (
    AgentAccessOut,
    AgentDetailOut,
    AgentListOut,
    AgentSummary,
    AgentType,
    DiscoverResult,
    GenieSpaceListOut,
    GenieSpaceSummary,
    GrantAccessResult,
    RescanMetadataResult,
    SubAgentInfo,
    SubComponentType,
    TileActionRow,
    UCTagConfig,
)
from .base import ExternalServiceError, NotFoundError


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

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


# Namespacing for Genie Spaces persisted into ``catalog_config``.
#
# We cannot drop a raw space_id into the ``endpoint_name`` column because it
# would collide with any future serving endpoint named the same thing, and
# because the user-facing ``/agents`` list needs to exclude Genie rows (they
# render via a dedicated Genie card grid). Prefixing with ``genie:`` keeps the
# admin table unified while giving both service functions and SQL filters
# (``endpoint_name LIKE 'genie:%'``) a stable discriminator.
_GENIE_ENDPOINT_PREFIX = "genie:"

# UC-tagged catalog entries (Phase 1 of the master roadmap). Admins mark UC
# functions or connections with a role tag (default key ``agent_hub_role``
# value ``agent``) and an optional kind tag (default key ``agent_hub_kind``
# value ``http`` / ``mcp``) to opt them into the catalog. We persist the
# fully-qualified UC name behind one of these prefixes so the chat dispatcher
# can route without re-reading metadata.
_UC_ENDPOINT_PREFIX = "uc:"
_MCP_ENDPOINT_PREFIX = "mcp:"


def _genie_endpoint_name(space_id: str) -> str:
    """Return the ``catalog_config.endpoint_name`` key for a Genie space."""
    return f"{_GENIE_ENDPOINT_PREFIX}{space_id}"


def _uc_endpoint_name(full_name: str) -> str:
    """Return the ``catalog_config.endpoint_name`` key for a UC HTTP agent."""
    return f"{_UC_ENDPOINT_PREFIX}{full_name}"


def _mcp_endpoint_name(full_name: str) -> str:
    """Return the ``catalog_config.endpoint_name`` key for a UC-tagged MCP agent."""
    return f"{_MCP_ENDPOINT_PREFIX}{full_name}"


def _is_uc_endpoint(endpoint_name: str) -> bool:
    return bool(endpoint_name) and endpoint_name.startswith(_UC_ENDPOINT_PREFIX)


def _is_mcp_endpoint(endpoint_name: str) -> bool:
    return bool(endpoint_name) and endpoint_name.startswith(_MCP_ENDPOINT_PREFIX)


def _strip_uc_prefix(endpoint_name: str) -> str:
    """Strip ``uc:`` or ``mcp:`` from the endpoint identifier."""
    if endpoint_name.startswith(_UC_ENDPOINT_PREFIX):
        return endpoint_name[len(_UC_ENDPOINT_PREFIX):]
    if endpoint_name.startswith(_MCP_ENDPOINT_PREFIX):
        return endpoint_name[len(_MCP_ENDPOINT_PREFIX):]
    return endpoint_name


def _owner_has_access(user_email: str | None, owner_email: str | None) -> bool:
    """Return True when the caller is the endpoint's registered owner.

    Conservative fallback used ONLY when the OBO ``serving_endpoints.get``
    probe fails. Matches case-insensitively and ignores surrounding
    whitespace since Databricks emails come through several proxies that
    can normalize casing differently. Empty/None inputs never match.
    """
    if not user_email or not owner_email:
        return False
    return user_email.strip().lower() == owner_email.strip().lower()


def _genie_has_access(user_ws: WorkspaceClient, space_id: str) -> bool | None:
    """Probe ``GET /api/2.0/genie/spaces/{id}`` via the user's OBO client.

    Returns:
      * True  -- the user can read the space (200)
      * False -- explicitly forbidden / not found (403, 404)
      * None  -- inconclusive (network, scope-missing, unexpected error);
                 caller should fall back to the owner check rather than
                 lock the user out.

    We deliberately never throw -- access checks must degrade gracefully
    so a transient API hiccup doesn't render every Genie card as
    "Request Access".
    """
    if not space_id:
        return False
    try:
        user_ws.api_client.do("GET", f"/api/2.0/genie/spaces/{space_id}")
        return True
    except Exception as e:
        err = str(e).lower()
        if "403" in err or "forbidden" in err or "permission" in err:
            return False
        if "404" in err or "not found" in err:
            return False
        logger.warning("Genie access probe inconclusive for %s: %s", space_id, str(e)[:160])
        return None


def _smart_title(s: str) -> str:
    """Title-case words while preserving hex-like tokens (e.g. ``94fa1c3b``).

    ``str.title()`` mangles hex into nonsense like ``94Fa1C3B``. This helper
    capitalizes the first letter of each word unless the word looks like a
    hex/ID token (contains digits, length >= 4, all lowercase) -- those
    get passed through as-is.
    """
    out: list[str] = []
    for w in s.split():
        looks_hex = (
            len(w) >= 4
            and w.islower()
            and any(c.isdigit() for c in w)
            and all(c.isalnum() for c in w)
        )
        out.append(w if looks_hex else w.capitalize())
    return " ".join(out)


_TILE_NAME_KEYS: tuple[str, ...] = ("name", "display_name", "title")
_TILE_DESCRIPTION_KEYS: tuple[str, ...] = ("description", "summary", "subtitle")


def _first_str(source: dict[str, Any] | None, keys: tuple[str, ...]) -> str:
    """Return the first non-empty string from ``source`` across ``keys``.

    Tolerates the list-tiles projection (which favors ``name``) and the
    multi-agent-supervisors detail projection (which sometimes nests
    under ``tile``/``metadata``) by accepting a pre-flattened dict.
    """
    if not source:
        return ""
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # Also accept ``tile["metadata"]["name"]`` style nesting.
    meta = source.get("metadata")
    if isinstance(meta, dict):
        for key in keys:
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _derive_display_name(
    endpoint_name: str,
    tile: dict[str, Any] | None,
    uc_model_name: str | None,
) -> str:
    """Pick the friendliest display name we can given what's available.

    Precedence:

    1. Tile ``name`` / ``display_name`` / ``title`` from either
       ``/api/2.0/tiles`` (list) or
       ``/api/2.0/multi-agent-supervisors/{id}`` (detail). The detail
       endpoint is the canonical source of truth for MAS human names
       and matches the Agent Bricks UI exactly.
    2. The UC model tail: ``main.default.ma_my_agent`` -> ``Ma My Agent``.
       Useful when both tile APIs are unreachable (F3 platform gap) but we
       still introspected the endpoint's served entity.
    3. Prettified endpoint name: ``mas-94fa1c3b-endpoint`` -> ``Mas 94fa1c3b``.
       Ugly but beats showing the raw Agent-Bricks-generated ID.

    The raw ``endpoint_name`` is still persisted in its own column and used
    for all API calls -- this only affects the human-readable label.
    """
    name_from_tile = _first_str(tile, _TILE_NAME_KEYS)
    if name_from_tile:
        return name_from_tile

    if uc_model_name:
        tail = uc_model_name.rsplit(".", 1)[-1]
        if tail:
            pretty = tail.replace("_", " ").replace("-", " ").strip()
            if pretty:
                return _smart_title(pretty)

    name = endpoint_name or ""
    if name.lower().endswith("-endpoint"):
        name = name[: -len("-endpoint")]

    pretty = name.replace("_", " ").replace("-", " ").strip()
    return _smart_title(pretty) if pretty else endpoint_name


def _derive_description(
    tile: dict[str, Any] | None,
    ep: Any | None,
) -> str:
    """Pull the best description we can from (tile detail, tile list, endpoint).

    Prefers rich tile descriptions over the thinner ``ep.description``,
    but falls back to it so user-authored serving endpoints still show
    something sensible. Returns ``""`` when nothing is available.
    """
    desc = _first_str(tile, _TILE_DESCRIPTION_KEYS)
    if desc:
        return desc
    ep_desc = getattr(ep, "description", None)
    if isinstance(ep_desc, str) and ep_desc.strip():
        return ep_desc.strip()
    return ""


def _looks_like_fallback_display_name(
    display_name: str,
    endpoint_name: str,
) -> bool:
    """Detect a stale/derived display_name that should be refreshed.

    Returns True when ``display_name`` matches what ``_derive_display_name``
    would produce *without* any tile data — i.e. the prettified endpoint
    tail. Used by :func:`get_agent_detail` to decide whether it's worth
    paying a detail-API round-trip to upgrade the row.
    """
    if not display_name:
        return True
    derived = _derive_display_name(endpoint_name, None, None)
    return display_name.strip().lower() == derived.strip().lower()


def _default_visible_for(agent_type: AgentType) -> bool:
    """Decide whether a newly-discovered catalog entry shows up to users by default.

    Default-visible: MAS / AGENT / KA / EXTERNAL / GENIE_SPACE — these are all
    first-class agent surfaces users pick from the catalog. OBO still enforces
    per-record access at request time (green dot / 'Accessible' filter), so
    surfacing them here does not leak anything the user wasn't entitled to see.

    Default-hidden: MODEL (plain served models / embeddings are building blocks,
    not agents; admin opts them in per-workspace via ``/admin/catalog``).

    History: GENIE_SPACE was default-hidden prior to 2026-04-17 — which
    silently hid 19/20 spaces in prod even though OBO successfully listed
    them. See ``docs/obo-auth-design.md`` §14 for the investigation trail.
    """
    # HTTP_CONNECTION / MCP_ENDPOINT are also first-class agents — they only
    # make it into the catalog if an admin explicitly tagged the UC object
    # with the configured agent tag, so we don't need a second opt-in gate.
    return agent_type in {
        AgentType.MAS,
        AgentType.AGENT,
        AgentType.KA,
        AgentType.EXTERNAL,
        AgentType.GENIE_SPACE,
        AgentType.HTTP_CONNECTION,
        AgentType.MCP_ENDPOINT,
    }


# Matches phrases like:
#   "Provided OAuth token does not have required scopes: foo.bar"
#   "required scope: foo.bar, baz.qux"
#   "required scopes: foo.bar | other stuff"
# We read everything up to end-of-line / pipe / semicolon, then trim
# trailing sentence punctuation and surrounding quotes. Scope names
# legitimately contain '.' and '-' so we cannot use '.' as a stop char.
_REQUIRED_SCOPE_RE = re.compile(
    r"required\s+scopes?\s*[:=]\s*([^|;\n]+)",
    re.IGNORECASE,
)


def _extract_required_scope(error_message: str) -> str | None:
    """Pull the "required scope" hint out of a Databricks OAuth 403 error.

    Returns ``None`` when the error is not scope-shaped so callers can log
    ``required_scope=unknown`` and move on (F3 in docs/obo-auth-design.md).
    """
    if not error_message:
        return None
    match = _REQUIRED_SCOPE_RE.search(error_message)
    if not match:
        return None
    scope = match.group(1).strip()
    # Strip sentence-terminal punctuation that wraps the scope name.
    scope = scope.rstrip(".,")
    # Peel paired surrounding quotes.
    for pair in ("''", '""', "``"):
        if len(scope) >= 2 and scope[0] == pair[0] and scope[-1] == pair[1]:
            scope = scope[1:-1].strip()
            break
    return scope or None


# --------------------------------------------------------------------------- #
# Resilient serving-endpoint listing (OBO -> SP fallback)
# --------------------------------------------------------------------------- #

def _list_serving_endpoints_resilient(
    ws: WorkspaceClient,
    sp_ws: WorkspaceClient | None,
) -> list[Any]:
    """List serving endpoints with an OBO->SP fallback and structured logging.

    Motivation: in prod we saw ``Listing serving endpoints...`` with no
    follow-up ``Found N`` because the OBO SDK path was silently returning a
    broken/partial result for some token sessions (matches the same class of
    token-scope intermittency that bites Genie API under OBO). Without a
    fallback or explicit timing log, the discover flow looks like a hang.
    """
    last_err: Exception | None = None

    for label, client in (("obo", ws), ("sp", sp_ws)):
        if client is None:
            continue
        t0 = time.monotonic()
        try:
            result = list(client.serving_endpoints.list())
        except Exception as e:
            dt_ms = int((time.monotonic() - t0) * 1000)
            last_err = e
            short = str(e).split("Config:")[0].strip()[:200]
            logger.warning(
                "serving_endpoints.list via %s failed in %dms: %s",
                label, dt_ms, short,
            )
            if client is sp_ws:
                break
            continue

        dt_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "Listed %d serving endpoint(s) via %s in %dms",
            len(result), label, dt_ms,
        )

        if result or client is sp_ws:
            return result

        logger.info(
            "OBO returned 0 endpoints -- retrying via service principal "
            "(likely stale-consent token; F5 in docs/obo-auth-design.md)"
        )

    raise ExternalServiceError(
        f"Failed to list serving endpoints via OBO and SP: {last_err}"
    )


# --------------------------------------------------------------------------- #
# Agent Bricks tiles API
# --------------------------------------------------------------------------- #

def _load_tiles_map(
    ws: WorkspaceClient,
    sp_ws: WorkspaceClient | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch Agent Bricks tiles and index them by ``serving_endpoint_name``.

    Agent Bricks exposes ``GET /api/2.0/tiles`` which returns the same display
    names, descriptions, and MAS/KA classification the Agent Bricks UI shows.
    Only a subset of custom endpoints in a typical workspace are actually
    tile-backed (a few MAS + KA); the rest are plain custom serving endpoints.

    Tries the user's OBO client first. In Databricks Apps the OBO token
    typically doesn't include the scope the tiles API requires
    (``workspace.access`` / ``dashboards.genie`` alone aren't enough on
    prod), so we fall back to the app service-principal client when it's
    available.

    Returns an empty dict on any failure so the caller falls back to the
    task-based classifier gracefully (graceful degradation on 403 / OBO scope
    gaps).
    """
    candidates: list[tuple[str, WorkspaceClient]] = []
    if ws is not None:
        candidates.append(("obo", ws))
    if sp_ws is not None and sp_ws is not ws:
        candidates.append(("sp", sp_ws))

    resp: Any = None
    for label, client in candidates:
        try:
            resp = client.api_client.do("GET", "/api/2.0/tiles")
            logger.info("Tiles API lookup OK via %s", label)
            break
        except Exception as e:
            msg = str(e)
            short = msg.split("Config:")[0].strip()[:200]
            required_scope = _extract_required_scope(msg)
            # Structured warning: the "required_scope=" key-value is machine
            # greppable so we can track what the platform wants over time
            # (F3 in docs/obo-auth-design.md).
            logger.warning(
                "Tiles API lookup via %s failed: %s | required_scope=%s",
                label,
                short,
                required_scope or "unknown",
            )
            continue

    if not isinstance(resp, dict):
        return {}

    tiles = resp.get("tiles") or resp.get("data") or []
    if not isinstance(tiles, list):
        return {}

    out: dict[str, dict[str, Any]] = {}
    for tile in tiles:
        if not isinstance(tile, dict):
            continue
        ep_name = tile.get("serving_endpoint_name") or tile.get("endpoint_name")
        if not ep_name:
            continue
        out[str(ep_name)] = tile
    logger.info("Loaded %d Agent Bricks tiles", len(out))
    return out


# Module-level TTL cache keyed by ``endpoint_name`` — prevents the detail
# view from hammering ``/api/2.0/multi-agent-supervisors/{id}`` when a user
# clicks through a card multiple times within a minute.
_TILE_DETAIL_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_TILE_DETAIL_TTL_SEC = 60.0


# Module-level TTL cache of ``endpoint_name -> tile_id`` resolutions
# from ``GET /api/2.0/serving-endpoints/{name}``. Used as a backfill
# when a catalog row was imported via plain serving-endpoints discovery
# (and the tiles list API was scope-denied at the time, so no
# ``tile_id`` landed in ``metadata_json``). Short TTL because a single
# detail view only needs one hit.
_TILE_ID_RESOLVE_CACHE: dict[str, tuple[float, str | None]] = {}
_TILE_ID_RESOLVE_TTL_SEC = 300.0


def _resolve_tile_id_from_endpoint(
    ws: WorkspaceClient,
    sp_ws: WorkspaceClient | None,
    endpoint_name: str,
) -> str | None:
    """Return the Agent Bricks ``tile_id`` for a serving endpoint, or None.

    Reads ``tile_endpoint_metadata.tile_id`` from
    ``GET /api/2.0/serving-endpoints/{name}``. That field is populated
    for every MAS / KA endpoint and gives us the UUID we need to call
    the detail endpoint at ``/api/2.0/multi-agent-supervisors/{tile_id}``
    -- without requiring the ``all-apis`` scope that the tiles list
    endpoint demands.

    OBO-first, SP fallback. Silent-fail to ``None`` so callers degrade
    gracefully. Cached per endpoint for ``_TILE_ID_RESOLVE_TTL_SEC``.
    """
    if not endpoint_name:
        return None

    now = time.monotonic()
    cached = _TILE_ID_RESOLVE_CACHE.get(endpoint_name)
    if cached is not None and (now - cached[0]) < _TILE_ID_RESOLVE_TTL_SEC:
        return cached[1]

    candidates: list[tuple[str, WorkspaceClient]] = []
    if ws is not None:
        candidates.append(("obo", ws))
    if sp_ws is not None and sp_ws is not ws:
        candidates.append(("sp", sp_ws))

    resp: Any = None
    for label, client in candidates:
        try:
            resp = client.api_client.do(
                "GET", f"/api/2.0/serving-endpoints/{endpoint_name}"
            )
            break
        except Exception as e:
            msg = str(e)
            short = msg.split("Config:")[0].strip()[:200]
            required_scope = _extract_required_scope(msg)
            logger.info(
                "serving-endpoints get via %s failed for %s: %s | required_scope=%s",
                label, endpoint_name, short, required_scope or "unknown",
            )
            continue

    if not isinstance(resp, dict):
        _TILE_ID_RESOLVE_CACHE[endpoint_name] = (now, None)
        return None

    meta = resp.get("tile_endpoint_metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    tile_id = meta.get("tile_id") or meta.get("id")
    tile_id_str = str(tile_id).strip() if tile_id else None

    if tile_id_str:
        logger.info(
            "Resolved tile_id for %s -> %s via serving-endpoints",
            endpoint_name, tile_id_str,
        )
    _TILE_ID_RESOLVE_CACHE[endpoint_name] = (now, tile_id_str)
    return tile_id_str


def _tile_api_paths_for(
    endpoint_name: str,
    tile_type_hint: str | None,
) -> list[str]:
    """Return the ``/api/2.0`` detail paths to try, in preferred order.

    Agent Bricks exposes MAS tiles at ``/api/2.0/multi-agent-supervisors``
    and Knowledge Assistant tiles at ``/api/2.0/knowledge-assistants``.
    The two shapes are disjoint: calling the MAS URL on a KA tile returns
    ``Tile type config is not of type MasConfig``, and vice versa. So we
    pick the preferred URL from the strongest signal we have (tile_type
    or endpoint-name prefix) and fall back to the other on shape errors.
    """
    mas = "multi-agent-supervisors"
    ka = "knowledge-assistants"

    hint = (tile_type_hint or "").strip().upper()
    prefer_ka = hint == "KA" or endpoint_name.startswith("ka-")
    prefer_mas = hint == "MAS" or endpoint_name.startswith("mas-")

    if prefer_ka and not prefer_mas:
        return [ka, mas]
    if prefer_mas and not prefer_ka:
        return [mas, ka]
    return [mas, ka]


def _load_tile_detail(
    ws: WorkspaceClient,
    sp_ws: WorkspaceClient | None,
    *,
    tile_id: str | None,
    endpoint_name: str,
    force: bool = False,
    tile_type_hint: str | None = None,
) -> dict[str, Any] | None:
    """Fetch the rich Agent Bricks detail for one MAS or KA tile.

    The Databricks UI at ``/ml/bricks/sa/configure/{tile_id}`` reads from
    ``GET /api/2.0/multi-agent-supervisors/{tile_id}`` for supervisor
    tiles and ``GET /api/2.0/knowledge-assistants/{tile_id}`` for KA
    tiles. Both REST endpoints authenticate with a normal OAuth bearer
    token and return the tile metadata (human-readable name +
    description). MAS responses also include a *structured* sub-agent
    graph -- the KA endpoint, Genie space id, and UC function path for
    each child. KA responses include ``knowledge_sources`` instead; we
    do not turn those into sub-agents because KAs render as a single
    leaf in the catalog UI.

    We use this for two reasons:

    1. Better display names/descriptions for MAS/KA rows whose
       ``/api/2.0/tiles`` projection returns only ``serving_endpoint_name``
       style keys instead of the human ``name``/``title``.
    2. Populate ``metadata_json.sub_agents`` with the real child graph
       rather than regex-parsing the ``instructions`` text.

    Returns a normalized dict with ``name``, ``description``,
    ``instructions``, ``tile_id``, ``endpoint_name``, ``tile_type`` and
    ``sub_agents`` (list of child dicts; empty for KA), or ``None`` on
    any failure — callers fall back to the less-rich tiles-list + regex
    path.

    Falls back OBO -> SP, same pattern as ``_load_tiles_map``. The
    preferred URL is picked by ``_tile_api_paths_for`` (tile-type hint
    or ``ka-``/``mas-`` endpoint prefix); we try the other shape on
    ``"is not of type"`` errors so a mis-classified row still resolves.
    Silent-fail on 403/404 because we also call this on non-tile
    endpoints just to refresh display metadata.
    """
    if not tile_id:
        return None

    now = time.monotonic()
    cached = _TILE_DETAIL_CACHE.get(endpoint_name)
    if not force and cached is not None and (now - cached[0]) < _TILE_DETAIL_TTL_SEC:
        return cached[1]

    candidates: list[tuple[str, WorkspaceClient]] = []
    if ws is not None:
        candidates.append(("obo", ws))
    if sp_ws is not None and sp_ws is not ws:
        candidates.append(("sp", sp_ws))

    paths = _tile_api_paths_for(endpoint_name, tile_type_hint)
    resp: Any = None
    hit_path: str | None = None
    for path_segment in paths:
        for label, client in candidates:
            try:
                resp = client.api_client.do(
                    "GET", f"/api/2.0/{path_segment}/{tile_id}"
                )
                hit_path = path_segment
                logger.info(
                    "%s detail via %s for %s (tile=%s)%s",
                    path_segment, label, endpoint_name, tile_id,
                    " [force]" if force else "",
                )
                break
            except Exception as e:
                msg = str(e)
                short = msg.split("Config:")[0].strip()[:200]
                required_scope = _extract_required_scope(msg)
                logger.info(
                    "%s detail via %s failed for %s: %s | required_scope=%s",
                    path_segment, label, endpoint_name, short,
                    required_scope or "unknown",
                )
                # If the error is "not of type" (MAS API called on a KA
                # tile or vice versa), stop retrying this path and let
                # the outer loop try the other shape.
                if "is not of type" in msg:
                    resp = None
                    break
                continue
        if isinstance(resp, dict):
            break

    if not isinstance(resp, dict):
        _TILE_DETAIL_CACHE[endpoint_name] = (now, None)
        return None

    # Normalize across both shapes. MAS: ``multi_agent_supervisor``
    # wraps ``tile`` + ``agents``. KA: ``knowledge_assistant`` wraps
    # ``tile`` + ``knowledge_sources``. We surface ``tile`` uniformly
    # and keep ``agents`` only for MAS (KA is a leaf, no sub-agents).
    wrapper = (
        resp.get("multi_agent_supervisor")
        or resp.get("knowledge_assistant")
        or resp
    )
    if not isinstance(wrapper, dict):
        _TILE_DETAIL_CACHE[endpoint_name] = (now, None)
        return None

    tile = wrapper.get("tile") if isinstance(wrapper.get("tile"), dict) else {}
    agents = wrapper.get("agents") if isinstance(wrapper.get("agents"), list) else []

    name = (
        tile.get("name")
        or tile.get("display_name")
        or tile.get("title")
        or wrapper.get("name")
        or ""
    )
    description = (
        tile.get("description")
        or wrapper.get("description")
        or ""
    )
    instructions = tile.get("instructions") or wrapper.get("instructions") or ""

    normalized: dict[str, Any] = {
        "tile_id": tile_id,
        "endpoint_name": endpoint_name,
        "name": str(name) if name else "",
        "description": str(description) if description else "",
        "instructions": str(instructions) if instructions else "",
        "sub_agents": list(agents),
        "tile_type": tile.get("tile_type") or "",
    }
    logger.info(
        "%s detail parsed for %s: name=%r, sub_agents=%d",
        hit_path or "tile", endpoint_name,
        normalized["name"], len(normalized["sub_agents"]),
    )

    _TILE_DETAIL_CACHE[endpoint_name] = (now, normalized)
    return normalized


def _invalidate_tile_detail_cache(endpoint_name: str | None = None) -> None:
    """Drop cached MAS detail entries (single endpoint or all).

    Exposed for admin-triggered rediscovery so a forced refresh isn't
    masked by the 60s TTL.
    """
    if endpoint_name is None:
        _TILE_DETAIL_CACHE.clear()
        return
    _TILE_DETAIL_CACHE.pop(endpoint_name, None)


_SUB_AGENT_TYPE_MAP: dict[str, str] = {
    "knowledge-assistant": SubComponentType.KNOWLEDGE_ASSISTANT.value,
    "knowledge_assistant": SubComponentType.KNOWLEDGE_ASSISTANT.value,
    "ka": SubComponentType.KNOWLEDGE_ASSISTANT.value,
    "genie-space": SubComponentType.GENIE_SPACE.value,
    "genie_space": SubComponentType.GENIE_SPACE.value,
    "genie": SubComponentType.GENIE_SPACE.value,
    "unity-catalog-function": SubComponentType.UC_FUNCTION.value,
    "unity_catalog_function": SubComponentType.UC_FUNCTION.value,
    "uc_function": SubComponentType.UC_FUNCTION.value,
    "uc-function": SubComponentType.UC_FUNCTION.value,
}


def _sub_agents_from_detail(detail: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Convert a ``_load_tile_detail`` response into persistable sub_agents.

    Each entry becomes a dict matching what :class:`SubAgentInfo` expects,
    plus an ``endpoint_ref`` carrying the KA endpoint name / Genie space
    id / UC fully-qualified name. Returns ``[]`` if the detail payload is
    missing or empty so the caller can fall back to the regex parser.
    """
    if not detail:
        return []
    raw = detail.get("sub_agents")
    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue

        friendly = str(entry.get("name") or "").strip()
        description = str(entry.get("description") or "").strip()
        type_hint = str(entry.get("agent_type") or "").strip().lower()
        mapped = _SUB_AGENT_TYPE_MAP.get(type_hint)

        endpoint_ref = ""
        if mapped == SubComponentType.KNOWLEDGE_ASSISTANT.value:
            se = entry.get("serving_endpoint")
            if isinstance(se, dict):
                endpoint_ref = str(se.get("name") or "").strip()
        elif mapped == SubComponentType.GENIE_SPACE.value:
            gs = entry.get("genie_space")
            if isinstance(gs, dict):
                endpoint_ref = str(gs.get("id") or "").strip()
        elif mapped == SubComponentType.UC_FUNCTION.value:
            uc = entry.get("unity_catalog_function")
            if isinstance(uc, dict):
                path = uc.get("uc_path")
                if isinstance(path, dict):
                    parts = [
                        str(path.get("catalog") or "").strip(),
                        str(path.get("schema") or "").strip(),
                        str(path.get("name") or "").strip(),
                    ]
                    if all(parts):
                        endpoint_ref = ".".join(parts)

        if not friendly and endpoint_ref:
            friendly = endpoint_ref

        # Fall back to SERVED_MODEL for unknown types so the UI still
        # shows the row (rather than silently dropping it).
        sub_type = mapped or SubComponentType.SERVED_MODEL.value

        out.append(
            {
                "name": friendly or "Sub-agent",
                "type": sub_type,
                "description": description,
                "endpoint_ref": endpoint_ref,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Agent-type classification
# --------------------------------------------------------------------------- #

_AGENT_TASKS = {"agent/v1/chat", "agent/v1/responses"}
_MODEL_TASKS = {"llm/v1/chat", "llm/v1/completions", "llm/v1/embeddings", "embeddings"}

# Agent Bricks names its generated endpoints using a fixed convention that
# the Databricks UI uses as a secondary signal too. Keeping these tight (must
# start with ``mas-`` / ``ka-`` AND end with ``-endpoint``) avoids
# misclassifying community agents whose names happen to contain ``mas`` or
# ``supervisor``.
_AGENT_BRICKS_MAS_PREFIX = "mas-"
_AGENT_BRICKS_KA_PREFIX = "ka-"
_AGENT_BRICKS_SUFFIX = "-endpoint"


def _looks_like_agent_bricks_mas(name: str) -> bool:
    lower = name.lower()
    return lower.startswith(_AGENT_BRICKS_MAS_PREFIX) and lower.endswith(_AGENT_BRICKS_SUFFIX)


def _looks_like_agent_bricks_ka(name: str) -> bool:
    lower = name.lower()
    return lower.startswith(_AGENT_BRICKS_KA_PREFIX) and lower.endswith(_AGENT_BRICKS_SUFFIX)


def _classify_agent_type(ep: Any, tile: dict[str, Any] | None = None) -> AgentType:
    """Classify an endpoint, preferring the Agent Bricks tile when present.

    Precedence:

    1. ``tile.tile_type`` from ``/api/2.0/tiles`` (MAS / KA) -- source of truth.
    2. External-model endpoints -> ``EXTERNAL``.
    3. Agent Bricks naming convention (``mas-<id>-endpoint`` /
       ``ka-<id>-endpoint``). These are auto-generated by Agent Bricks and
       are unambiguous even when the tiles API is unreachable (the Databricks
       UI shows them as Supervisor Agent / Knowledge Assistant too).
    4. ``task`` field: ``agent/v1/*`` -> ``AGENT`` (Custom Agent Endpoint),
       ``llm/v1/*`` / ``embeddings`` -> ``MODEL``.
    5. Last-resort: if the endpoint has any served entity/model, default to
       ``MODEL``.
    """
    name = getattr(ep, "name", "") or ""
    task = (getattr(ep, "task", None) or "").lower()
    config = getattr(ep, "config", None)

    served_entities = list(getattr(config, "served_entities", None) or []) if config else []
    served_models = list(getattr(config, "served_models", None) or []) if config else []

    # 1. Tile-driven classification (source of truth for MAS / KA).
    if tile:
        tile_type = str(tile.get("tile_type") or "").upper()
        if tile_type == "MAS":
            return AgentType.MAS
        if tile_type == "KA":
            return AgentType.KA

    # 2. External-model endpoints expose served_entity.external_model.
    for se in served_entities:
        if getattr(se, "external_model", None):
            return AgentType.EXTERNAL

    # 3. Agent Bricks naming convention -- works even when the tiles API is
    # inaccessible (e.g. OBO token lacks the Agent Bricks scope on prod).
    if _looks_like_agent_bricks_mas(name):
        return AgentType.MAS
    if _looks_like_agent_bricks_ka(name):
        return AgentType.KA

    # 4. Task-based classification for non-Agent-Bricks endpoints.
    if task in _AGENT_TASKS:
        return AgentType.AGENT

    if task in _MODEL_TASKS:
        return AgentType.MODEL

    # 5. Fallback.
    if served_entities or served_models:
        return AgentType.MODEL

    return AgentType.MODEL


# --------------------------------------------------------------------------- #
# MAS-instructions parsing
# --------------------------------------------------------------------------- #

_MAS_SUBAGENT_PATTERNS: list[tuple[re.Pattern[str], SubComponentType]] = [
    (re.compile(r"([A-Za-z0-9_\-\.]+)\s*\(\s*Genie\s*Space\s*\)", re.IGNORECASE), SubComponentType.GENIE_SPACE),
    (re.compile(r"([A-Za-z0-9_\-\.]+)\s*\(\s*Knowledge\s*Assistant\s*\)", re.IGNORECASE), SubComponentType.KNOWLEDGE_ASSISTANT),
    (re.compile(r"([A-Za-z0-9_\-\.]+)\s*\(\s*UC\s*Function\s*\)", re.IGNORECASE), SubComponentType.UC_FUNCTION),
    (re.compile(r"([A-Za-z0-9_\-\.]+)\s*\(\s*Vector\s*Search\s*\)", re.IGNORECASE), SubComponentType.VECTOR_SEARCH),
    (re.compile(r"([A-Za-z0-9_\-\.]+)\s*\(\s*External\s*MCP\s*\)", re.IGNORECASE), SubComponentType.EXTERNAL_MCP),
    (re.compile(r"([A-Za-z0-9_\-\.]+)\s*\(\s*MCP\s*\)", re.IGNORECASE), SubComponentType.EXTERNAL_MCP),
]


def _parse_mas_instructions_for_sub_agents(instructions: str) -> list[dict[str, Any]]:
    """Best-effort regex pass over MAS tile ``instructions`` text.

    MAS tiles declare their sub-agents in free-form text like::

        analytics_agent (Genie Space) - responsible for ...
        thailand_news_agent (UC Function) - fetches ...
        ka_set_index_th (Knowledge Assistant) - retrieves ...

    We extract ``name (Type)`` occurrences and return a deduped list of
    ``{name, type}`` dicts. Used as a fallback when MLflow model-version
    dependencies come back empty for a MAS endpoint.
    """
    if not instructions:
        return []

    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for pattern, sub_type in _MAS_SUBAGENT_PATTERNS:
        for m in pattern.finditer(instructions):
            name = (m.group(1) or "").strip()
            if not name:
                continue
            key = (name, sub_type.value)
            if key in seen:
                continue
            seen.add(key)
            found.append({"name": name, "type": sub_type.value})
    if found:
        logger.info("Parsed %d sub-agent(s) from MAS instructions", len(found))
    return found


# --------------------------------------------------------------------------- #
# Sub-component introspection
# --------------------------------------------------------------------------- #

def _classify_entity_name(entity_name: str) -> SubComponentType:
    """Heuristic classification of a served entity by its UC name."""
    lower = entity_name.lower()
    if lower.startswith("system.ai.") or ".ai." in lower:
        return SubComponentType.UC_FUNCTION
    if "genie" in lower:
        return SubComponentType.GENIE_SPACE
    if "knowledge" in lower or lower.startswith("ka-") or lower.startswith("ka_"):
        return SubComponentType.KNOWLEDGE_ASSISTANT
    if "vector" in lower or "vs_" in lower or lower.endswith("_index"):
        return SubComponentType.VECTOR_SEARCH
    if "mcp" in lower:
        return SubComponentType.EXTERNAL_MCP
    return SubComponentType.SERVED_MODEL


def _mlflow_resources_to_components(resources: Any) -> list[dict[str, Any]]:
    """Extract sub-components from MLflow model metadata.

    Supports two shapes:

    1. Databricks ``model_version_dependencies.dependencies`` on UC model
       versions, where each entry is one of::

           {"function":        {"function_full_name": "cat.schema.fn"}}
           {"table":           {"table_full_name":    "cat.schema.tbl"}}
           {"serving_endpoint":{"name": "my-endpoint"}}
           {"vector_search_index": {"index_name": "cat.schema.idx"}}
           {"genie_space":     {"space_id": "..."}}

    2. Legacy MLflow ``resources`` metadata (dict or list of
       ``{type, name}``), e.g. ``DatabricksGenieSpace``,
       ``DatabricksFunction``, ``DatabricksVectorSearchIndex``,
       ``DatabricksServingEndpoint``.

    Tables are intentionally dropped — they're upstream data dependencies,
    not tool-level sub-components the user invokes.
    """
    if not resources:
        return []

    items: list[dict[str, Any]] = []

    if isinstance(resources, dict) and "dependencies" in resources:
        resources = resources["dependencies"]

    if isinstance(resources, dict):
        for key, vals in resources.items():
            if isinstance(vals, list):
                for v in vals:
                    items.append({"type": str(key), "name": _coerce_resource_name(v)})
            else:
                items.append({"type": str(key), "name": _coerce_resource_name(vals)})
    elif isinstance(resources, list):
        for entry in resources:
            if not isinstance(entry, dict):
                continue

            if "type" in entry or ("name" in entry and not any(
                k in entry for k in ("function", "table", "serving_endpoint", "vector_search_index", "genie_space")
            )):
                items.append({
                    "type": str(entry.get("type", "")),
                    "name": _coerce_resource_name(entry.get("name") or entry),
                })
                continue

            for k, v in entry.items():
                items.append({"type": str(k), "name": _coerce_resource_name(v)})

    out: list[dict[str, Any]] = []
    for it in items:
        t = (it.get("type") or "").lower()
        name = it.get("name") or ""
        if not name:
            continue

        if t == "table" or "table" in t:
            continue

        sub_type: SubComponentType
        if "genie" in t:
            sub_type = SubComponentType.GENIE_SPACE
        elif "function" in t:
            sub_type = SubComponentType.UC_FUNCTION
        elif "vector_search" in t or "vectorsearch" in t or "vector-search" in t:
            sub_type = SubComponentType.VECTOR_SEARCH
        elif "serving_endpoint" in t or "servingendpoint" in t or "serving-endpoint" in t:
            sub_type = SubComponentType.SERVED_MODEL
        elif "mcp" in t:
            sub_type = SubComponentType.EXTERNAL_MCP
        elif "knowledge" in t or t == "ka":
            sub_type = SubComponentType.KNOWLEDGE_ASSISTANT
        else:
            sub_type = _classify_entity_name(name)

        out.append({"name": name, "type": sub_type.value})
    return out


def _coerce_resource_name(val: Any) -> str:
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        for key in (
            "function_full_name",
            "table_full_name",
            "index_name",
            "full_name",
            "endpoint_name",
            "space_id",
            "function_name",
            "name",
        ):
            v = val.get(key)
            if v:
                return str(v)
    return str(val) if val else ""


def _components_from_endpoint_config(ep: Any) -> list[dict[str, Any]]:
    """Fallback: derive components from the live endpoint config only.

    Used when MLflow resource metadata isn't available. Each served_entity /
    served_model becomes a SubComponent typed via name heuristics.
    """
    config = getattr(ep, "config", None)
    if not config:
        return []

    items: list[dict[str, Any]] = []
    for se in list(getattr(config, "served_entities", None) or []):
        if getattr(se, "external_model", None):
            em = se.external_model
            name = getattr(em, "name", None) or getattr(se, "name", "") or ""
            items.append({
                "name": name or "external_model",
                "type": SubComponentType.EXTERNAL_MCP.value,
            })
            continue

        entity_name = (
            getattr(se, "entity_name", None)
            or getattr(se, "name", None)
            or ""
        )
        if not entity_name:
            continue
        items.append({
            "name": entity_name,
            "type": _classify_entity_name(entity_name).value,
        })

    if not items:
        for sm in list(getattr(config, "served_models", None) or []):
            model_name = getattr(sm, "model_name", None) or getattr(sm, "name", "")
            if model_name:
                items.append({
                    "name": model_name,
                    "type": _classify_entity_name(model_name).value,
                })

    return items


def _resolve_sub_components(
    ep: Any,
    ws: WorkspaceClient,
    sp_ws: WorkspaceClient | None = None,
    tile: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Best-effort: read MLflow model resources; fall back to served_entities.

    ``ws`` is typically the caller's OBO client (used for access-context
    operations). Unity Catalog model-version introspection requires broader
    scopes than the app's OBO grant, so when ``sp_ws`` (the app service
    principal client) is provided we prefer it for ``model_versions.get`` and
    silently fall back to ``ws`` otherwise.

    When ``detail`` is a ``_load_tile_detail`` response, its structured
    ``sub_agents`` graph (real KA / Genie / UC children) takes precedence
    over any MLflow / endpoint / regex inference — it is the same graph
    the Agent Bricks UI renders.

    When ``tile`` is an Agent Bricks MAS tile *without* a detail payload,
    we still parse its ``instructions`` field for ``name (Type)`` sub-agent
    mentions as a last resort -- useful for MAS agents where the detail
    API 403s for the current token.

    Always returns a deduped list of ``{name, type}`` dicts suitable for
    persistence in ``metadata_json.sub_agents``.
    """
    # Prefer the structured detail payload — it is the same source the
    # Agent Bricks UI renders and is strictly richer than either MLflow
    # resource inference or the regex fallback.
    from_detail = _sub_agents_from_detail(detail) if detail else []
    if from_detail:
        return from_detail

    components: list[dict[str, Any]] = []

    config = getattr(ep, "config", None)
    served_entities = list(getattr(config, "served_entities", None) or []) if config else []

    # Try the user's OBO client first (the user is usually the model
    # owner so EXECUTE succeeds), fall back to the app SP if OBO lacks the
    # relevant catalog scope (e.g. user_api_scopes only has
    # serving.serving-endpoints).
    candidate_clients: list[WorkspaceClient] = [c for c in (ws, sp_ws) if c is not None]

    for se in served_entities:
        model_name = getattr(se, "entity_name", None)
        model_version = getattr(se, "entity_version", None)
        if not model_name or not model_version:
            continue
        mv_dict: dict[str, Any] = {}
        errors: list[str] = []
        for i, candidate in enumerate(candidate_clients):
            client_label = "obo" if i == 0 else "sp"
            auth_type = getattr(getattr(candidate, "config", None), "auth_type", "?")
            try:
                mv = candidate.model_versions.get(
                    full_name=model_name, version=str(model_version)
                )
                mv_dict = mv.as_dict() if hasattr(mv, "as_dict") else {}
                logger.info(
                    "MLflow introspection OK via %s (auth=%s) for %s v%s",
                    client_label, auth_type, model_name, model_version,
                )
                break
            except Exception as e:
                short = str(e).split("Config:")[0].strip()
                errors.append(f"{client_label}(auth={auth_type}): {short[:200]}")
                continue
        if not mv_dict:
            logger.warning(
                "MLflow introspection failed for %s v%s: %s",
                model_name, model_version, " | ".join(errors),
            )
            continue
        try:

            # Primary (UC): tool-level dependencies live on
            # ``model_version_dependencies.dependencies``.
            mvd = mv_dict.get("model_version_dependencies") or {}
            if mvd:
                components.extend(_mlflow_resources_to_components(mvd))

            # Fallback (legacy MLflow): some registrations drop a
            # ``resources`` block directly on the model version.
            resources = mv_dict.get("resources") or {}
            if resources:
                components.extend(_mlflow_resources_to_components(resources))

            # Fallback (older / run-level): serialized JSON under tags like
            # ``mlflow.databricks.resources``.
            tags = mv_dict.get("tags") or []
            if isinstance(tags, list):
                for tag in tags:
                    key = tag.get("key") if isinstance(tag, dict) else None
                    val = tag.get("value") if isinstance(tag, dict) else None
                    if key and "resource" in key.lower() and isinstance(val, str):
                        try:
                            parsed = json.loads(val)
                            components.extend(_mlflow_resources_to_components(parsed))
                        except (json.JSONDecodeError, TypeError):
                            continue
        except Exception as e:
            logger.warning(
                "MLflow resource parsing failed for %s v%s: %s",
                model_name, model_version, e,
            )

    if not components:
        components = _components_from_endpoint_config(ep)

    # MAS-instructions fallback: if we still have nothing (or only the self
    # served-model), parse sub-agents out of the tile's free-text
    # instructions so MAS tiles always surface their Genie / UC Function
    # children even when MLflow deps are unavailable.
    if tile and str(tile.get("tile_type") or "").upper() == "MAS":
        instructions = str(tile.get("instructions") or "")
        parsed = _parse_mas_instructions_for_sub_agents(instructions)
        if parsed:
            components.extend(parsed)

    # Filter out "self" — the agent's own backing UC model shouldn't appear
    # as one of its sub-components.
    self_names: set[str] = set()
    for se in served_entities:
        n = getattr(se, "entity_name", None)
        if n:
            self_names.add(str(n))

    # Dedupe by (name, type) and drop self references.
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for c in components:
        name = c.get("name") or ""
        ctype = c.get("type") or ""
        if name in self_names and ctype == SubComponentType.SERVED_MODEL.value:
            continue
        key = (name, ctype)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped


def _coerce_sub_component_type(raw: Any) -> SubComponentType:
    """Parse a sub-agent ``type`` from metadata / API payloads.

    Rows may store either the enum *value* (``knowledge_assistant``), the
    enum *name* (``KNOWLEDGE_ASSISTANT``), or legacy hyphenated hints. A
    failed parse must not silently downgrade to ``SERVED_MODEL`` before
    access checks — that mis-classification pointed OBO probes at the
    friendly ``name`` instead of ``endpoint_ref`` and painted KA/Genie
    rows as inaccessible.
    """
    if isinstance(raw, SubComponentType):
        return raw
    s = str(raw or "").strip()
    if not s:
        return SubComponentType.SERVED_MODEL
    try:
        return SubComponentType(s)
    except ValueError:
        pass
    # Tile / legacy strings may use spaces or hyphens ("Knowledge Assistant").
    normalized = s.lower().replace("-", "_").replace(" ", "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    try:
        return SubComponentType(normalized)
    except ValueError:
        pass
    key = s.upper().replace("-", "_")
    try:
        return SubComponentType[key]
    except KeyError:
        pass
    return SubComponentType.SERVED_MODEL


def _component_has_access(ws: WorkspaceClient, comp: dict[str, Any]) -> bool:
    """Best-effort OBO access check for a sub-component.

    Unity Catalog APIs (functions, vector-search) require the underlying
    ``unity-catalog`` OAuth scope which is not currently exposable via
    Databricks Apps ``user_api_scopes``. For those component types we treat
    missing-scope errors as 'unknown -> True' (optimistic), and only
    surface False on explicit 403/permission denial.

    Knowledge Assistants and Genie Spaces reuse the same optimistic patterns
    as top-level ``get_agent_detail`` / ``check_access`` (scope gaps and
    inconclusive probes must not paint sub-rows as "Request access").
    External MCP has no OBO probe today and is treated as accessible for
    catalog display; invocation-time checks run elsewhere.
    """
    name = comp.get("name") or ""
    ctype = _coerce_sub_component_type(comp.get("type")).value
    endpoint_ref = str(comp.get("endpoint_ref") or "").strip()
    space_id_field = str(comp.get("space_id") or "").strip()

    if ctype == SubComponentType.EXTERNAL_MCP.value:
        logger.debug(
            "Component access for EXTERNAL_MCP %s: no OBO probe, optimistic True",
            name or endpoint_ref or "(unnamed)",
        )
        return True

    if ctype == SubComponentType.KNOWLEDGE_ASSISTANT.value:
        if not endpoint_ref:
            logger.debug(
                "Component access for KNOWLEDGE_ASSISTANT %s: no endpoint_ref, optimistic True",
                name or "(unnamed)",
            )
            return True
        try:
            ws.serving_endpoints.get(endpoint_ref)
            return True
        except Exception as e:
            msg = str(e).lower()
            if "required scopes" in msg or "invalid scope" in msg:
                logger.debug(
                    "Component access unknown due to OBO scope limitation for KA %s (%s)",
                    name, endpoint_ref,
                )
                return True
            if "permission" in msg or "forbidden" in msg or "403" in msg:
                return False
            if "not found" in msg or "404" in msg:
                return False
            logger.debug(
                "Component access check inconclusive for KA %s (%s): %s",
                name, endpoint_ref, e,
            )
            return True

    if ctype == SubComponentType.GENIE_SPACE.value:
        space_id = endpoint_ref or space_id_field
        if not space_id:
            logger.debug(
                "Component access for GENIE_SPACE %s: no space id, optimistic True",
                name or "(unnamed)",
            )
            return True
        probe = _genie_has_access(ws, space_id)
        if probe is True:
            return True
        if probe is False:
            return False
        return True

    if not name:
        return False
    try:
        if ctype == SubComponentType.SERVED_MODEL.value:
            ws.serving_endpoints.get(name)
            return True
        if ctype == SubComponentType.UC_FUNCTION.value:
            ws.functions.get(name)
            return True
        if ctype == SubComponentType.VECTOR_SEARCH.value:
            ws.vector_search_indexes.get_index(index_name=name)
            return True
    except Exception as e:
        msg = str(e).lower()
        if "required scopes" in msg or "invalid scope" in msg:
            logger.debug(
                "Component access unknown due to OBO scope limitation for %s (%s)",
                name, ctype,
            )
            return True
        if "permission" in msg or "forbidden" in msg or "403" in msg:
            return False
        logger.debug("Component access check failed for %s (%s): %s", name, ctype, e)
    return False


# --------------------------------------------------------------------------- #
# Public service functions
# --------------------------------------------------------------------------- #

def discover_from_workspace(
    ws: WorkspaceClient,
    session: Session,
    sp_ws: WorkspaceClient | None = None,
) -> DiscoverResult:
    """Scan workspace serving endpoints and upsert into catalog_config.

    Excludes system endpoints prefixed with 'databricks-'.
    Uses batch upsert for performance on large workspaces.

    ``ws`` is the caller's (admin's) OBO client used for endpoint listing /
    GETs. ``sp_ws`` is the app service principal client used for Unity
    Catalog model-version introspection, since the app OBO scope doesn't
    typically include ``catalog.models``.
    """
    warnings: list[str] = []
    new_agents: list[AgentSummary] = []

    logger.info("Listing serving endpoints...")
    all_endpoints = _list_serving_endpoints_resilient(ws, sp_ws)

    custom_endpoints = [
        ep for ep in all_endpoints if ep.name and not ep.name.startswith("databricks-")
    ]
    discovered = len(custom_endpoints)
    logger.info("Found %d custom endpoints (filtered from %d total)", discovered, len(all_endpoints))

    # Load Agent Bricks tiles once -- the source of truth for MAS / KA
    # classification and for display names matching Databricks UI. Pass
    # ``sp_ws`` so we fall back to the app service principal if the caller's
    # OBO token is missing the tiles-API scope.
    tiles_map = _load_tiles_map(ws, sp_ws)

    existing_names: set[str] = set()
    try:
        rows = session.exec(text("SELECT endpoint_name FROM catalog_config")).all()
        # sqlmodel returns Row objects; index 0 is the first selected column.
        existing_names = {str(r[0]) for r in rows}
    except Exception:
        pass

    created = 0
    updated = 0
    skipped = 0

    for ep_summary in custom_endpoints:
        endpoint_name = ep_summary.name
        if not endpoint_name:
            skipped += 1
            continue

        try:
            # Fetch the detailed endpoint so we can read task + full config.
            try:
                ep = ws.serving_endpoints.get(endpoint_name)
            except Exception as e:
                logger.debug("Detail fetch failed for %s, falling back to list result: %s", endpoint_name, e)
                ep = ep_summary

            tile = tiles_map.get(endpoint_name)
            # For MAS tiles, fetch the structured detail so we can populate
            # the real sub-agent graph (and upgrade display_name/description
            # past the list-projection). Safe no-op for non-MAS rows.
            detail: dict[str, Any] | None = None
            tile_id_for_detail = None
            if tile:
                tile_id_for_detail = tile.get("tile_id") or tile.get("id")
                tile_type_str = str(tile.get("tile_type") or "").upper()
                if tile_type_str in ("MAS", "KA"):
                    detail = _load_tile_detail(
                        ws, sp_ws,
                        tile_id=str(tile_id_for_detail) if tile_id_for_detail else None,
                        endpoint_name=endpoint_name,
                        tile_type_hint=tile_type_str,
                    )

            agent_type = _classify_agent_type(ep, tile)
            sub_components = _resolve_sub_components(
                ep, ws, sp_ws, tile=tile, detail=detail,
            )

            uc_model_name: str | None = None
            config = getattr(ep, "config", None)
            if config:
                ses = list(getattr(config, "served_entities", None) or [])
                sms = list(getattr(config, "served_models", None) or [])
                if ses:
                    uc_model_name = getattr(ses[0], "entity_name", None)
                elif sms:
                    uc_model_name = getattr(sms[0], "model_name", None)

            # Prefer the detail payload (richer, UI-canonical) for
            # display_name / description; fall back to list-tile.
            display_name = _derive_display_name(
                endpoint_name, detail or tile, uc_model_name,
            )
            description = _derive_description(detail or tile, ep)

            metadata: dict[str, Any] = {
                "uc_model_name": uc_model_name,
                "sub_agents": sub_components,
                "task": getattr(ep, "task", None) or "",
            }
            if tile:
                metadata["tile_id"] = tile.get("tile_id") or tile.get("id") or ""
                metadata["tile_type"] = tile.get("tile_type") or ""
            metadata = {k: v for k, v in metadata.items() if v is not None}

            owner = getattr(ep, "creator", None) or ""
            visible_default = _default_visible_for(agent_type)

            session.exec(text("SAVEPOINT ep_save"))
            if endpoint_name in existing_names:
                session.exec(
                    text(
                        """UPDATE catalog_config SET
                            display_name = :display,
                            description = COALESCE(NULLIF(:desc, ''), description),
                            agent_type = :agent_type,
                            metadata_json = CAST(:meta AS jsonb),
                            owner_email = COALESCE(NULLIF(:owner, ''), owner_email),
                            updated_at = NOW()
                        WHERE endpoint_name = :name"""
                    ).bindparams(
                        display=display_name,
                        desc=description,
                        agent_type=agent_type.value,
                        meta=json.dumps(metadata),
                        owner=owner,
                        name=endpoint_name,
                    )
                )
                updated += 1
            else:
                session.exec(
                    text(
                        """INSERT INTO catalog_config
                            (endpoint_name, display_name, description, agent_type, visible, owner_email, metadata_json)
                        VALUES (:name, :display, :desc, :agent_type, :visible, :owner, CAST(:meta AS jsonb))
                        ON CONFLICT (endpoint_name) DO UPDATE SET
                            display_name = EXCLUDED.display_name,
                            description = COALESCE(NULLIF(EXCLUDED.description, ''), catalog_config.description),
                            agent_type = EXCLUDED.agent_type,
                            metadata_json = EXCLUDED.metadata_json,
                            owner_email = COALESCE(NULLIF(EXCLUDED.owner_email, ''), catalog_config.owner_email),
                            updated_at = NOW()"""
                    ).bindparams(
                        name=endpoint_name,
                        display=display_name,
                        desc=description,
                        agent_type=agent_type.value,
                        visible=visible_default,
                        owner=owner,
                        meta=json.dumps(metadata),
                    )
                )
                created += 1
                new_agents.append(
                    AgentSummary(
                        endpoint_name=endpoint_name,
                        display_name=display_name,
                        description=description,
                        agent_type=agent_type.value,
                        sub_agent_count=len(sub_components),
                        owner_email=owner,
                    )
                )
                existing_names.add(endpoint_name)
            session.exec(text("RELEASE SAVEPOINT ep_save"))
        except Exception as e:
            try:
                session.exec(text("ROLLBACK TO SAVEPOINT ep_save"))
            except Exception:
                pass
            skipped += 1
            warnings.append(f"Endpoint {endpoint_name!r}: {e}")

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        warnings.append(f"Batch commit failed: {e}")

    # Also persist Genie Spaces so admins can Hide/Show them from
    # /admin/catalog the same way they manage endpoint-backed agents.
    # Uses the same OBO-then-SP fallback already in list_genie_spaces.
    genie_created, genie_updated, genie_skipped, genie_warnings = _upsert_genie_spaces(
        ws, session, sp_ws
    )
    discovered += genie_created + genie_updated
    created += genie_created
    updated += genie_updated
    skipped += genie_skipped
    warnings.extend(genie_warnings)

    # UC-tag driven discovery (Phase 1). Pulls tagged functions / connections
    # via SP + admin warehouse and upserts them under ``uc:`` / ``mcp:``
    # prefixes. Deliberately runs after genie upsert so a failure here never
    # prevents the other discovery paths from completing.
    uc_created = uc_updated = uc_skipped = 0
    uc_warnings: list[str] = []
    try:
        from . import admin_service as _admin  # local import avoids cycle
        tag_config = _admin.get_uc_tag_config(session)
        uc_created, uc_updated, uc_skipped, uc_warnings = _discover_uc_tagged(
            sp_ws, session, tag_config
        )
    except Exception as e:
        uc_warnings.append(f"UC tag discovery failed: {e}")
        logger.exception("UC tag discovery raised")
    discovered += uc_created + uc_updated
    created += uc_created
    updated += uc_updated
    skipped += uc_skipped
    warnings.extend(uc_warnings)

    logger.info(
        "Discovery complete: %d found, %d new, %d updated, %d skipped "
        "(incl. %d new / %d updated Genie spaces, %d new / %d updated UC-tagged)",
        discovered,
        created,
        updated,
        skipped,
        genie_created,
        genie_updated,
        uc_created,
        uc_updated,
    )

    return DiscoverResult(
        discovered=discovered,
        new=created,
        updated=updated,
        skipped=skipped,
        warnings=warnings,
        agents=new_agents,
    )


def _upsert_genie_spaces(
    ws: WorkspaceClient,
    session: Session,
    sp_ws: WorkspaceClient | None,
) -> tuple[int, int, int, list[str]]:
    """Upsert Genie Spaces reachable under the caller's OBO into catalog_config.

    Returns ``(created, updated, skipped, warnings)`` so the surrounding
    ``discover_from_workspace`` can roll them into its counters.

    We use the raw ``/api/2.0/genie/spaces`` response instead of
    ``list_genie_spaces()`` because the latter filters against the admin
    visibility table (``catalog_config.visible``) -- which is exactly what
    we're trying to populate here.
    """
    warnings: list[str] = []
    created = 0
    updated = 0
    skipped = 0

    try:
        spaces = _fetch_genie_spaces_raw(ws, sp_ws)
    except Exception as e:
        warnings.append(f"Genie fetch failed: {e}")
        logger.warning("Genie discovery skipped: %s", e)
        return 0, 0, 0, warnings

    for sp in spaces:
        space_id = str(sp.get("space_id") or sp.get("id") or "").strip()
        title = str(sp.get("title") or sp.get("name") or "").strip()
        if not space_id or not title:
            skipped += 1
            continue

        endpoint_name = _genie_endpoint_name(space_id)
        description = str(sp.get("description") or "")
        warehouse_id = str(sp.get("warehouse_id") or "")
        metadata = {
            "kind": "genie_space",
            "space_id": space_id,
            "warehouse_id": warehouse_id,
        }
        visible_default = _default_visible_for(AgentType.GENIE_SPACE)

        try:
            session.exec(text("SAVEPOINT genie_save"))
            existing = session.exec(
                text(
                    "SELECT 1 FROM catalog_config WHERE endpoint_name = :n"
                ).bindparams(n=endpoint_name)
            ).one_or_none()

            if existing:
                session.exec(
                    text(
                        """UPDATE catalog_config SET
                            display_name = :display,
                            description = COALESCE(NULLIF(:desc, ''), description),
                            agent_type = :agent_type,
                            metadata_json = CAST(:meta AS jsonb),
                            updated_at = NOW()
                        WHERE endpoint_name = :name"""
                    ).bindparams(
                        display=title,
                        desc=description,
                        agent_type=AgentType.GENIE_SPACE.value,
                        meta=json.dumps(metadata),
                        name=endpoint_name,
                    )
                )
                updated += 1
            else:
                session.exec(
                    text(
                        """INSERT INTO catalog_config
                            (endpoint_name, display_name, description, agent_type, visible, owner_email, metadata_json)
                        VALUES (:name, :display, :desc, :agent_type, :visible, '', CAST(:meta AS jsonb))
                        ON CONFLICT (endpoint_name) DO NOTHING"""
                    ).bindparams(
                        name=endpoint_name,
                        display=title,
                        desc=description,
                        agent_type=AgentType.GENIE_SPACE.value,
                        visible=visible_default,
                        meta=json.dumps(metadata),
                    )
                )
                created += 1
            session.exec(text("RELEASE SAVEPOINT genie_save"))
        except Exception as e:
            try:
                session.exec(text("ROLLBACK TO SAVEPOINT genie_save"))
            except Exception:
                pass
            skipped += 1
            warnings.append(f"Genie space {space_id!r}: {e}")

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        warnings.append(f"Genie batch commit failed: {e}")

    return created, updated, skipped, warnings


# --------------------------------------------------------------------------- #
# UC-tag-driven discovery (Phase 1: HTTP + MCP agents)
# --------------------------------------------------------------------------- #

def _admin_warehouse_id() -> str:
    """Warehouse used to run ``system.information_schema.*_tags`` queries.

    Precedence: ``AGENT_HUB_ADMIN_WAREHOUSE_ID`` (explicit config) ->
    ``DATABRICKS_WAREHOUSE_ID`` (standard SDK env). Returns ``""`` when
    neither is set; the discovery branch then logs a warning and skips
    UC-tag discovery rather than crash.
    """
    return (
        os.environ.get("AGENT_HUB_ADMIN_WAREHOUSE_ID")
        or os.environ.get("DATABRICKS_WAREHOUSE_ID")
        or ""
    ).strip()


def _normalize_sql_ident(value: str) -> str:
    """Quote a single SQL string literal safely (escape single quotes)."""
    return (value or "").replace("'", "''")


def _execute_sp_sql(
    sp_ws: WorkspaceClient,
    statement: str,
    warehouse_id: str,
    *,
    wait_timeout: str = "30s",
) -> list[dict[str, Any]]:
    """Run a SQL statement via the SP client and return rows as dicts.

    Raises if the statement fails. Uses ``INLINE`` disposition with
    ``JSON_ARRAY`` so result sets are returned synchronously when small
    (the tag tables are tiny in practice).
    """
    resp = sp_ws.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout=wait_timeout,
    )

    state = getattr(getattr(resp, "status", None), "state", None)
    if state and str(state).upper() not in {"SUCCEEDED", "FINISHED"}:
        err = getattr(getattr(resp, "status", None), "error", None)
        raise RuntimeError(f"Statement state={state}: {err}")

    manifest = getattr(resp, "manifest", None)
    schema = getattr(manifest, "schema", None)
    cols = getattr(schema, "columns", None) or []
    col_names = [str(getattr(c, "name", "")) for c in cols]

    result = getattr(resp, "result", None)
    data = getattr(result, "data_array", None) or []

    rows: list[dict[str, Any]] = []
    for raw in data:
        if not isinstance(raw, (list, tuple)):
            continue
        rows.append({
            col_names[i] if i < len(col_names) else str(i): raw[i]
            for i in range(len(raw))
        })
    return rows


def _discover_uc_tagged(
    sp_ws: WorkspaceClient | None,
    session: Session,
    tag_config: UCTagConfig,
) -> tuple[int, int, int, list[str]]:
    """Discover UC functions + connections marked with the configured agent tag.

    Queries ``system.information_schema.function_tags`` and
    ``system.information_schema.connection_tags`` via the SP client + admin
    warehouse (the OBO token typically lacks the scope for those views).
    Matches ``tag_name`` = ``agent_tag_key`` AND ``tag_value`` =
    ``agent_tag_value`` (case-insensitive).

    For each match, a second scan over the same table picks up the optional
    kind tag (``agent_kind_tag_key``) so we can classify ``HTTP_CONNECTION``
    vs ``MCP_ENDPOINT``. Missing / unknown kind defaults to
    ``HTTP_CONNECTION`` because it has the safer chat-invocation fallback
    (SQL Statements REST) in Phase 2.

    Upserts rows under ``uc:<full_name>`` / ``mcp:<full_name>`` and records
    ``invoke_shape`` + ``kind_tag_value`` in metadata for Phase 2.

    Returns ``(created, updated, skipped, warnings)``.
    """
    warnings: list[str] = []
    created = 0
    updated = 0
    skipped = 0

    if sp_ws is None:
        warnings.append("UC tag discovery skipped: SP workspace client unavailable")
        logger.warning("UC tag discovery skipped: sp_ws is None")
        return 0, 0, 0, warnings

    warehouse_id = _admin_warehouse_id()
    if not warehouse_id:
        warnings.append(
            "UC tag discovery skipped: set AGENT_HUB_ADMIN_WAREHOUSE_ID "
            "(or DATABRICKS_WAREHOUSE_ID) to enable"
        )
        logger.info("UC tag discovery skipped: no admin warehouse configured")
        return 0, 0, 0, warnings

    if os.environ.get("AGENT_HUB_DISABLE_UC_MCP_DISCOVERY") == "1":
        logger.info("UC tag discovery disabled via AGENT_HUB_DISABLE_UC_MCP_DISCOVERY")
        return 0, 0, 0, warnings

    tag_key = tag_config.agent_tag_key.strip()
    tag_value = tag_config.agent_tag_value.strip()
    kind_key = tag_config.agent_kind_tag_key.strip()

    if not tag_key or not tag_value:
        warnings.append("UC tag discovery skipped: empty agent_tag_key or agent_tag_value")
        return 0, 0, 0, warnings

    tag_key_l = _normalize_sql_ident(tag_key)
    tag_value_l = _normalize_sql_ident(tag_value)
    kind_key_l = _normalize_sql_ident(kind_key) if kind_key else ""

    # -- Functions --
    fn_rows: list[dict[str, Any]] = []
    try:
        fn_rows = _execute_sp_sql(
            sp_ws,
            f"""
            SELECT catalog_name, schema_name, function_name, tag_value
              FROM system.information_schema.function_tags
             WHERE lower(tag_name) = lower('{tag_key_l}')
               AND lower(tag_value) = lower('{tag_value_l}')
            """,
            warehouse_id,
        )
    except Exception as e:
        msg = f"function_tags query failed: {e}"
        warnings.append(msg)
        logger.warning("UC discovery: %s", msg)

    kind_for_fn: dict[str, str] = {}
    if kind_key_l and fn_rows:
        try:
            kind_rows = _execute_sp_sql(
                sp_ws,
                f"""
                SELECT catalog_name, schema_name, function_name, tag_value
                  FROM system.information_schema.function_tags
                 WHERE lower(tag_name) = lower('{kind_key_l}')
                """,
                warehouse_id,
            )
            for r in kind_rows:
                full = f"{r.get('catalog_name')}.{r.get('schema_name')}.{r.get('function_name')}"
                kind_for_fn[full.lower()] = str(r.get("tag_value") or "").lower().strip()
        except Exception as e:
            warnings.append(f"function kind-tag query skipped: {e}")

    # -- Connections --
    conn_rows: list[dict[str, Any]] = []
    try:
        conn_rows = _execute_sp_sql(
            sp_ws,
            f"""
            SELECT catalog_name, connection_name, tag_value
              FROM system.information_schema.connection_tags
             WHERE lower(tag_name) = lower('{tag_key_l}')
               AND lower(tag_value) = lower('{tag_value_l}')
            """,
            warehouse_id,
        )
    except Exception as e:
        msg = f"connection_tags query failed: {e}"
        warnings.append(msg)
        logger.info("UC discovery: %s (connection tagging may not be supported yet)", msg)

    kind_for_conn: dict[str, str] = {}
    if kind_key_l and conn_rows:
        try:
            kind_rows = _execute_sp_sql(
                sp_ws,
                f"""
                SELECT catalog_name, connection_name, tag_value
                  FROM system.information_schema.connection_tags
                 WHERE lower(tag_name) = lower('{kind_key_l}')
                """,
                warehouse_id,
            )
            for r in kind_rows:
                full = f"{r.get('catalog_name')}.{r.get('connection_name')}"
                kind_for_conn[full.lower()] = str(r.get("tag_value") or "").lower().strip()
        except Exception as e:
            warnings.append(f"connection kind-tag query skipped: {e}")

    # Upsert functions (default kind: http unless kind tag says otherwise)
    for r in fn_rows:
        catalog = str(r.get("catalog_name") or "").strip()
        schema = str(r.get("schema_name") or "").strip()
        name = str(r.get("function_name") or "").strip()
        if not (catalog and schema and name):
            skipped += 1
            continue

        full = f"{catalog}.{schema}.{name}"
        kind = kind_for_fn.get(full.lower(), "http")
        is_mcp = kind == "mcp"
        agent_type = AgentType.MCP_ENDPOINT if is_mcp else AgentType.HTTP_CONNECTION
        endpoint_name = (_mcp_endpoint_name(full) if is_mcp else _uc_endpoint_name(full))

        display = _smart_title(name.replace("_", " "))
        description = ""  # left blank; Phase 2 will optionally enrich via DESCRIBE FUNCTION
        metadata = {
            "kind": "mcp_uc_function" if is_mcp else "http_uc_function",
            "uc_full_name": full,
            "invoke_shape": "mcp" if is_mcp else "uc_function_sql",
            "kind_tag_value": kind,
            "agent_tag_key": tag_key,
            "agent_tag_value": tag_value,
        }
        c, u, s = _upsert_uc_row(
            session, endpoint_name, display, description, agent_type, metadata
        )
        created += c
        updated += u
        skipped += s

    # Upsert connections (always mcp for external MCP connections by convention)
    for r in conn_rows:
        catalog = str(r.get("catalog_name") or "").strip()
        name = str(r.get("connection_name") or "").strip()
        if not (catalog and name):
            skipped += 1
            continue

        full = f"{catalog}.{name}"
        kind = kind_for_conn.get(full.lower(), "mcp")  # connections default to MCP
        is_mcp = kind != "http"
        agent_type = AgentType.MCP_ENDPOINT if is_mcp else AgentType.HTTP_CONNECTION
        endpoint_name = (_mcp_endpoint_name(full) if is_mcp else _uc_endpoint_name(full))

        display = _smart_title(name.replace("_", " "))
        metadata = {
            "kind": "mcp_uc_connection" if is_mcp else "http_uc_connection",
            "uc_full_name": full,
            "invoke_shape": "mcp_connection" if is_mcp else "uc_connection_http",
            "kind_tag_value": kind,
            "agent_tag_key": tag_key,
            "agent_tag_value": tag_value,
        }
        c, u, s = _upsert_uc_row(
            session, endpoint_name, display, "", agent_type, metadata
        )
        created += c
        updated += u
        skipped += s

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        warnings.append(f"UC batch commit failed: {e}")

    logger.info(
        "catalog.uc_discovery fn=%d mcp_fn=%d conn=%d created=%d updated=%d "
        "warehouse=%s",
        len(fn_rows),
        sum(1 for r in fn_rows
            if kind_for_fn.get(
                f"{r.get('catalog_name')}.{r.get('schema_name')}.{r.get('function_name')}".lower(),
                "http",
            ) == "mcp"),
        len(conn_rows),
        created,
        updated,
        warehouse_id,
    )
    return created, updated, skipped, warnings


def _upsert_uc_row(
    session: Session,
    endpoint_name: str,
    display_name: str,
    description: str,
    agent_type: AgentType,
    metadata: dict[str, Any],
) -> tuple[int, int, int]:
    """Upsert a ``uc:*`` / ``mcp:*`` row. Returns ``(created, updated, skipped)``."""
    visible_default = _default_visible_for(agent_type)
    try:
        session.exec(text("SAVEPOINT uc_tag_save"))
        existing = session.exec(
            text(
                "SELECT 1 FROM catalog_config WHERE endpoint_name = :n"
            ).bindparams(n=endpoint_name)
        ).one_or_none()

        if existing:
            session.exec(
                text(
                    """UPDATE catalog_config SET
                        display_name = :display,
                        description = COALESCE(NULLIF(:desc, ''), description),
                        agent_type = :agent_type,
                        metadata_json = CAST(:meta AS jsonb),
                        updated_at = NOW()
                    WHERE endpoint_name = :name"""
                ).bindparams(
                    display=display_name,
                    desc=description,
                    agent_type=agent_type.value,
                    meta=json.dumps(metadata),
                    name=endpoint_name,
                )
            )
            session.exec(text("RELEASE SAVEPOINT uc_tag_save"))
            return 0, 1, 0

        session.exec(
            text(
                """INSERT INTO catalog_config
                    (endpoint_name, display_name, description, agent_type, visible, owner_email, metadata_json)
                VALUES (:name, :display, :desc, :agent_type, :visible, '', CAST(:meta AS jsonb))
                ON CONFLICT (endpoint_name) DO NOTHING"""
            ).bindparams(
                name=endpoint_name,
                display=display_name,
                desc=description,
                agent_type=agent_type.value,
                visible=visible_default,
                meta=json.dumps(metadata),
            )
        )
        session.exec(text("RELEASE SAVEPOINT uc_tag_save"))
        return 1, 0, 0
    except Exception as e:
        try:
            session.exec(text("ROLLBACK TO SAVEPOINT uc_tag_save"))
        except Exception:
            pass
        logger.warning("uc_tag upsert failed for %s: %s", endpoint_name, e)
        return 0, 0, 1


def _fetch_genie_spaces_raw(
    ws: WorkspaceClient,
    sp_ws: WorkspaceClient | None = None,
) -> list[dict[str, Any]]:
    """Call ``/api/2.0/genie/spaces`` with OBO->SP fallback, return raw dicts.

    Shared by discovery (which needs the full set to persist) and
    ``list_genie_spaces`` (which additionally filters against admin
    visibility). Returns ``[]`` on any failure so callers can degrade
    gracefully.
    """
    candidates: list[tuple[str, WorkspaceClient]] = []
    if ws is not None:
        candidates.append(("obo", ws))
    if sp_ws is not None and sp_ws is not ws:
        candidates.append(("sp", sp_ws))

    resp: Any = None
    for label, client in candidates:
        try:
            resp = client.api_client.do("GET", "/api/2.0/genie/spaces")
            break
        except Exception as e:
            short = str(e).split("Config:")[0].strip()[:200]
            logger.warning("Genie spaces list via %s failed: %s", label, short)
            continue

    if not isinstance(resp, dict):
        return []
    raw = resp.get("spaces") or resp.get("data") or []
    return raw if isinstance(raw, list) else []


def reclassify_existing(
    ws: WorkspaceClient,
    session: Session,
    sp_ws: WorkspaceClient | None = None,
) -> DiscoverResult:
    """Re-run classification + sub-component introspection for every existing row.

    Unlike ``discover_from_workspace``, this doesn't add new endpoints — it just
    corrects ``agent_type`` and refreshes ``metadata_json.sub_agents`` on rows
    that already exist. Use once after a schema/logic upgrade.
    """
    warnings: list[str] = []
    updated = 0
    skipped = 0

    # Load Agent Bricks tiles once so reclassify uses the same source of
    # truth as discover. Pass ``sp_ws`` for tiles-API scope fallback.
    tiles_map = _load_tiles_map(ws, sp_ws)

    rows = session.exec(text("SELECT endpoint_name FROM catalog_config")).all()
    for r in rows:
        # sqlmodel returns Row objects; index 0 is the first selected column.
        endpoint_name = str(r[0])

        # Skip Genie / UC / MCP rows -- they are first-class agents with no
        # serving endpoint to introspect; reclassification would fail with
        # RESOURCE_NOT_FOUND on each one.
        if (
            endpoint_name.startswith(_GENIE_ENDPOINT_PREFIX)
            or _is_uc_endpoint(endpoint_name)
            or _is_mcp_endpoint(endpoint_name)
        ):
            skipped += 1
            continue

        try:
            ep = ws.serving_endpoints.get(endpoint_name)
            tile = tiles_map.get(endpoint_name)
            detail: dict[str, Any] | None = None
            if tile:
                tile_type_str = str(tile.get("tile_type") or "").upper()
                if tile_type_str in ("MAS", "KA"):
                    tile_id_for_detail = tile.get("tile_id") or tile.get("id")
                    detail = _load_tile_detail(
                        ws, sp_ws,
                        tile_id=str(tile_id_for_detail) if tile_id_for_detail else None,
                        endpoint_name=endpoint_name,
                        tile_type_hint=tile_type_str,
                    )

            agent_type = _classify_agent_type(ep, tile)
            sub_components = _resolve_sub_components(
                ep, ws, sp_ws, tile=tile, detail=detail,
            )

            uc_model_name: str | None = None
            config = getattr(ep, "config", None)
            if config:
                ses = list(getattr(config, "served_entities", None) or [])
                sms = list(getattr(config, "served_models", None) or [])
                if ses:
                    uc_model_name = getattr(ses[0], "entity_name", None)
                elif sms:
                    uc_model_name = getattr(sms[0], "model_name", None)

            display_name = _derive_display_name(
                endpoint_name, detail or tile, uc_model_name,
            )
            description = _derive_description(detail or tile, ep)

            metadata_patch: dict[str, Any] = {
                "sub_agents": sub_components,
                "task": getattr(ep, "task", None) or "",
            }
            if tile:
                metadata_patch["tile_id"] = tile.get("tile_id") or tile.get("id") or ""
                metadata_patch["tile_type"] = tile.get("tile_type") or ""

            session.exec(
                text(
                    """UPDATE catalog_config SET
                        display_name = :display,
                        description = COALESCE(NULLIF(:desc, ''), description),
                        agent_type = :agent_type,
                        metadata_json = COALESCE(metadata_json, '{}'::jsonb) || CAST(:meta AS jsonb),
                        updated_at = NOW()
                    WHERE endpoint_name = :name"""
                ).bindparams(
                    display=display_name,
                    desc=description,
                    agent_type=agent_type.value,
                    meta=json.dumps(metadata_patch),
                    name=endpoint_name,
                )
            )
            updated += 1
        except Exception as e:
            skipped += 1
            warnings.append(f"Reclassify {endpoint_name!r}: {e}")

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        warnings.append(f"Commit failed: {e}")

    return DiscoverResult(
        discovered=len(rows),
        new=0,
        updated=updated,
        skipped=skipped,
        warnings=warnings,
        agents=[],
    )


# --------------------------------------------------------------------------- #
# Admin: grant SP access + rescan MAS/KA tile metadata
# --------------------------------------------------------------------------- #

# Agent Bricks tile ACL surface. This is *not* the serving-endpoint ACL --
# even a workspace admin with CAN_MANAGE on the serving endpoint cannot
# read ``/api/2.0/multi-agent-supervisors/{tile_id}`` unless they (or the
# caller) also have CAN_MANAGE on the tile ACL. CAN_QUERY on either the
# tile or the endpoint returns the same "You do not have read access to
# the agent." error. See docs/rollback-obo-gaps-2026-04-17.md §11.2.
_TILE_ACL_PATH = "/api/2.0/permissions/knowledge-assistants/{tile_id}"

# Permission level we grant the app SP. The Agent Bricks detail endpoint
# rejects CAN_QUERY; CAN_MANAGE is the minimum that unblocks reads.
_SP_TILE_PERMISSION = "CAN_MANAGE"


def _mas_ka_endpoint_predicate(endpoint_name: str, agent_type: str) -> bool:
    """Return True for rows that are Agent Bricks MAS / KA tiles.

    Matches either the endpoint-name convention the platform uses
    (``mas-<uuid>-endpoint`` / ``ka-<uuid>-endpoint``) or the stored
    ``agent_type`` if the row was already classified. Genie / UC / MCP
    rows are excluded because they have no tile ACL.
    """
    if not endpoint_name or endpoint_name.startswith(_GENIE_ENDPOINT_PREFIX):
        return False
    if _is_uc_endpoint(endpoint_name) or _is_mcp_endpoint(endpoint_name):
        return False
    name = endpoint_name.lower()
    if name.startswith("mas-") or name.startswith("ka-"):
        return True
    at = (agent_type or "").upper()
    return at in {AgentType.MAS.value, AgentType.KA.value}


def _iter_mas_ka_rows(session: Session) -> list[tuple[str, str, dict[str, Any]]]:
    """Return ``(endpoint_name, agent_type, metadata)`` for every MAS/KA row.

    Reads directly from ``catalog_config`` so admins can rescan even rows
    whose ``visible`` flag is currently false (e.g. freshly discovered
    tiles the admin has not yet reviewed).
    """
    rows = session.exec(
        text(
            "SELECT endpoint_name, agent_type, metadata_json "
            "FROM catalog_config"
        )
    ).all()
    out: list[tuple[str, str, dict[str, Any]]] = []
    for r in rows:
        endpoint_name = str(r[0])
        agent_type = str(r[1] or "")
        metadata = _parse_metadata(r[2])
        if _mas_ka_endpoint_predicate(endpoint_name, agent_type):
            out.append((endpoint_name, agent_type, metadata))
    return out


def _sp_application_id(sp_ws: WorkspaceClient) -> str | None:
    """Return the app Service Principal's application id (OAuth client id).

    Uses ``sp_ws.config.client_id`` so we don't need the
    ``iam.current-user:read`` scope on the SP token (which the app
    manifest doesn't declare). Falls back to ``current_user.me()`` for
    local / dev paths where ``client_id`` is unset.
    """
    cid = getattr(getattr(sp_ws, "config", None), "client_id", None)
    if cid:
        return str(cid)
    try:
        me = sp_ws.current_user.me()
        app_id = getattr(me, "application_id", None) or getattr(me, "user_name", None)
        return str(app_id) if app_id else None
    except Exception as e:
        logger.warning("Failed to resolve SP application_id: %s", e)
        return None


def _tile_id_for_row(
    ws: WorkspaceClient,
    sp_ws: WorkspaceClient | None,
    endpoint_name: str,
    metadata: dict[str, Any],
) -> str | None:
    """Resolve the Agent Bricks tile id for a catalog row.

    Prefers the cached value in ``metadata_json.tile_id`` (written by
    ``discover_from_workspace`` / ``reclassify_existing``). Falls back
    to ``_resolve_tile_id_from_endpoint`` which reads
    ``tile_endpoint_metadata.tile_id`` from the serving endpoint detail
    -- works without ``all-apis`` scope.
    """
    tid = metadata.get("tile_id")
    if isinstance(tid, str) and tid.strip():
        return tid.strip()
    return _resolve_tile_id_from_endpoint(ws, sp_ws, endpoint_name)


def _tile_acl_contains_sp(
    acl: list[Any] | None,
    sp_app_id: str,
    required_level: str = _SP_TILE_PERMISSION,
) -> bool:
    """Check whether ``acl`` already grants ``sp_app_id`` at ``required_level``.

    The Agent Bricks permissions API returns entries shaped like::

        {
          "service_principal_name": "...",
          "application_id": "...",   # sometimes used instead of SPN
          "all_permissions": [
            {"permission_level": "CAN_MANAGE", "inherited": false, ...}
          ]
        }

    We match on either ``application_id`` or ``service_principal_name``
    because the returned shape depends on how the SP was originally
    added (workspace-level SP vs. account-level SP).
    """
    if not acl:
        return False
    wanted = required_level.upper()
    for entry in acl:
        if not isinstance(entry, dict):
            continue
        candidates = {
            str(entry.get("application_id") or "").strip(),
            str(entry.get("service_principal_name") or "").strip(),
        }
        if sp_app_id not in candidates or not sp_app_id:
            continue
        perms = entry.get("all_permissions")
        if not isinstance(perms, list):
            # Fallback for the older payload shape that uses a flat
            # ``permission_level`` field.
            level = str(entry.get("permission_level") or "").upper()
            if level == wanted:
                return True
            continue
        for p in perms:
            if not isinstance(p, dict):
                continue
            if str(p.get("permission_level") or "").upper() == wanted:
                return True
    return False


def _classify_acl_error(exc: Exception) -> tuple[str, str]:
    """Map an exception raised by a tile-ACL call to ``(status, message)``.

    ``status`` matches the ``TileActionStatus`` literal: ``unauthorized``
    for 403s (admin doesn't manage the tile) **and** for the specific
    ``required scopes: access-management`` response -- Databricks Apps
    does not yet expose that scope to OBO, so the button is functionally
    unauthorized even for a tile owner until the platform catches up.
    Everything else is ``failed``. ``message`` is a short human-readable
    string suitable for the UI toast / table cell.
    """
    msg = str(exc)
    short = msg.split("Config:")[0].strip()[:240]
    required_scope = _extract_required_scope(msg) or ""
    # Agent Bricks' permissions API wants the ``access-management`` OAuth
    # scope which Databricks Apps does not currently expose in
    # ``user_authorization.scopes`` (the bundle CLI rejects it as "not a
    # valid scope"). Surface a distinct message so the admin knows to
    # use the curl fallback instead of assuming they mis-clicked.
    if required_scope.split()[0:1] == ["access-management"]:
        return (
            "unauthorized",
            "OBO missing scope 'access-management' -- Databricks Apps does "
            "not expose it yet. Use the curl fallback in docs/rollback-obo-"
            "gaps-2026-04-17.md §11.2.1.",
        )
    # Databricks SDK surfaces 403 as "PermissionDenied" or an HTTP 403
    # snippet; the tile endpoint also returns a 403 when the caller
    # isn't in the tile ACL as a manager.
    if (
        "PermissionDenied" in msg
        or " 403 " in msg
        or msg.lstrip().startswith("403")
        or "Forbidden" in msg
        or "do not have" in msg.lower()
    ):
        return "unauthorized", short or "Forbidden"
    return "failed", short or "Request failed"


def grant_sp_access_on_tiles(
    user_ws: WorkspaceClient,
    sp_ws: WorkspaceClient,
    session: Session,
) -> GrantAccessResult:
    """Add the app SP to each MAS/KA tile's ACL with ``CAN_MANAGE`` (idempotent).

    Runs under the admin's OBO so the PATCH call flows with the admin's
    credentials -- the person clicking the button must already hold
    ``CAN_MANAGE`` on each tile (workspace admin or tile owner). 403s
    are classified as ``unauthorized`` and surfaced to the UI row-by-row
    so the admin can tell exactly which tiles need an owner handoff.

    We GET the ACL first and only PATCH when the SP isn't already in the
    list at ``CAN_MANAGE`` -- that keeps the operation idempotent and
    avoids spurious audit-log churn on repeated clicks.
    """
    sp_app_id = _sp_application_id(sp_ws)
    if not sp_app_id:
        # Without an SP id we can't grant anything. Surface a single row
        # so the UI toast can explain why nothing happened.
        return GrantAccessResult(
            failed=1,
            rows=[
                TileActionRow(
                    endpoint_name="",
                    status="failed",
                    message="Could not resolve app service principal id.",
                )
            ],
        )

    rows: list[TileActionRow] = []
    counts = {
        "granted": 0,
        "already_granted": 0,
        "unauthorized": 0,
        "failed": 0,
        "skipped": 0,
    }

    for endpoint_name, _agent_type, metadata in _iter_mas_ka_rows(session):
        tile_id = _tile_id_for_row(user_ws, sp_ws, endpoint_name, metadata)
        if not tile_id:
            counts["skipped"] += 1
            rows.append(
                TileActionRow(
                    endpoint_name=endpoint_name,
                    tile_id=None,
                    status="skipped",
                    message="Could not resolve tile_id.",
                )
            )
            continue

        path = _TILE_ACL_PATH.format(tile_id=tile_id)

        # 1) GET current ACL (OBO). 403 here means the admin doesn't
        #    manage the tile, so PATCH would also 403 -- short-circuit.
        try:
            current = user_ws.api_client.do("GET", path)
        except Exception as e:
            status, short = _classify_acl_error(e)
            counts[status] += 1
            rows.append(
                TileActionRow(
                    endpoint_name=endpoint_name,
                    tile_id=tile_id,
                    status=status,  # type: ignore[arg-type]
                    message=short,
                )
            )
            logger.info(
                "Tile ACL GET failed for %s (tile=%s) [%s]: %s",
                endpoint_name, tile_id, status, short,
            )
            continue

        acl = None
        if isinstance(current, dict):
            acl = current.get("access_control_list")
        if _tile_acl_contains_sp(acl, sp_app_id):
            counts["already_granted"] += 1
            rows.append(
                TileActionRow(
                    endpoint_name=endpoint_name,
                    tile_id=tile_id,
                    status="already_granted",
                    message=f"SP already has {_SP_TILE_PERMISSION} on tile.",
                )
            )
            continue

        # 2) PATCH to add the SP at CAN_MANAGE. The Databricks permissions
        #    API treats PATCH as additive for ``access_control_list`` --
        #    existing entries are preserved.
        payload = {
            "access_control_list": [
                {
                    "service_principal_name": sp_app_id,
                    "permission_level": _SP_TILE_PERMISSION,
                }
            ]
        }
        try:
            user_ws.api_client.do("PATCH", path, body=payload)
        except Exception as e:
            status, short = _classify_acl_error(e)
            counts[status] += 1
            rows.append(
                TileActionRow(
                    endpoint_name=endpoint_name,
                    tile_id=tile_id,
                    status=status,  # type: ignore[arg-type]
                    message=short,
                )
            )
            logger.info(
                "Tile ACL PATCH failed for %s (tile=%s) [%s]: %s",
                endpoint_name, tile_id, status, short,
            )
            continue

        counts["granted"] += 1
        rows.append(
            TileActionRow(
                endpoint_name=endpoint_name,
                tile_id=tile_id,
                status="granted",
                message=f"Granted {_SP_TILE_PERMISSION} to app SP.",
            )
        )
        logger.info(
            "Granted %s to SP %s on tile %s (endpoint=%s)",
            _SP_TILE_PERMISSION, sp_app_id, tile_id, endpoint_name,
        )

    return GrantAccessResult(
        granted=counts["granted"],
        already_granted=counts["already_granted"],
        unauthorized=counts["unauthorized"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        rows=rows,
    )


def rescan_mas_ka_metadata(
    user_ws: WorkspaceClient | None,
    sp_ws: WorkspaceClient,
    session: Session,
) -> RescanMetadataResult:
    """Refresh ``display_name`` / ``description`` / ``sub_agents`` from Agent Bricks.

    Uses two clients on purpose:

    * ``user_ws`` (admin OBO) for ``serving_endpoints.get`` and tile-id
      resolution. Grant-catalog-access only writes the tile ACL, it does
      **not** grant the SP ``CAN_VIEW`` on the underlying serving
      endpoint, so the SP would get "User does not have permission
      'View'" for every tile the admin didn't manually share. The admin
      already has View on any tile they own.
    * ``sp_ws`` for ``/api/2.0/multi-agent-supervisors/{tile_id}`` --
      that endpoint requires the ``all-apis`` scope Databricks Apps OBO
      cannot carry. The SP must already be in the tile ACL at
      ``CAN_MANAGE`` (run "Grant catalog access" first).

    Bypasses the 60s ``_TILE_DETAIL_CACHE`` via ``force=True`` so the
    admin sees fresh values on every click. Genie / UC / MCP rows are
    filtered out by ``_iter_mas_ka_rows`` -- only tile-backed MAS/KA
    endpoints have a supervisors detail to fetch.
    """
    rows: list[TileActionRow] = []
    counts = {"refreshed": 0, "unchanged": 0, "failed": 0, "skipped": 0}

    # ``serving_endpoints.get`` runs as the admin OBO when possible;
    # fall back to SP only if the endpoint really is caller-anonymous.
    endpoint_ws = user_ws if user_ws is not None else sp_ws

    for endpoint_name, agent_type_str, metadata in _iter_mas_ka_rows(session):
        tile_id = _tile_id_for_row(endpoint_ws, sp_ws, endpoint_name, metadata)
        if not tile_id:
            counts["skipped"] += 1
            rows.append(
                TileActionRow(
                    endpoint_name=endpoint_name,
                    tile_id=None,
                    status="skipped",
                    message="Could not resolve tile_id.",
                )
            )
            continue

        # Pick the right Agent Bricks detail URL up-front: MAS rows go
        # to ``/api/2.0/multi-agent-supervisors``, KA rows go to
        # ``/api/2.0/knowledge-assistants``. Calling the wrong shape
        # returns ``Tile type config is not of type MasConfig`` which
        # ``_load_tile_detail`` treats as a signal to try the other
        # URL, but passing the hint avoids that round-trip. Fall back
        # to the endpoint-name prefix when the stored agent_type is
        # stale (e.g. imported as a plain ``MODEL`` before KA/MAS
        # classification was wired up).
        tile_type_hint: str | None = None
        if agent_type_str == AgentType.MAS.value:
            tile_type_hint = "MAS"
        elif agent_type_str == AgentType.KA.value:
            tile_type_hint = "KA"
        elif endpoint_name.startswith("mas-"):
            tile_type_hint = "MAS"
        elif endpoint_name.startswith("ka-"):
            tile_type_hint = "KA"

        try:
            detail = _load_tile_detail(
                endpoint_ws, sp_ws,
                tile_id=tile_id,
                endpoint_name=endpoint_name,
                force=True,
                tile_type_hint=tile_type_hint,
            )
        except Exception as e:
            status, short = _classify_acl_error(e)
            counts["failed"] += 1
            rows.append(
                TileActionRow(
                    endpoint_name=endpoint_name,
                    tile_id=tile_id,
                    status="failed",
                    message=short,
                )
            )
            logger.info(
                "rescan_mas_ka_metadata detail load raised for %s [%s]: %s",
                endpoint_name, status, short,
            )
            continue

        if not detail:
            # _load_tile_detail silently returns None on 403/404 -- most
            # commonly because the SP isn't in the tile ACL yet. Surface
            # as "failed" so the admin knows to run Grant access first.
            counts["failed"] += 1
            rows.append(
                TileActionRow(
                    endpoint_name=endpoint_name,
                    tile_id=tile_id,
                    status="failed",
                    message="Tile detail unavailable -- run Grant access first.",
                )
            )
            continue

        ep = None
        last_err: Exception | None = None
        # Try OBO first (admin has View on any tile they manage), then SP
        # as a belt-and-braces fallback. Without this order the call
        # fails for tiles we only granted at the tile-ACL level because
        # the SP still lacks serving-endpoint View.
        for candidate in (user_ws, sp_ws):
            if candidate is None:
                continue
            try:
                ep = candidate.serving_endpoints.get(endpoint_name)
                break
            except Exception as e:
                last_err = e
                continue
        if ep is None:
            msg = str(last_err or "").split("Config:")[0].strip()[:200]
            counts["failed"] += 1
            rows.append(
                TileActionRow(
                    endpoint_name=endpoint_name,
                    tile_id=tile_id,
                    status="failed",
                    message=f"Serving endpoint lookup failed: {msg}",
                )
            )
            continue

        effective_tile_type = (
            detail.get("tile_type") or tile_type_hint or ""
        )
        tile_hint: dict[str, Any] | None = None
        if effective_tile_type:
            tile_hint = {
                "tile_id": tile_id,
                "tile_type": effective_tile_type,
                "endpoint_name": endpoint_name,
            }

        agent_type = _classify_agent_type(ep, tile_hint)
        sub_components = _resolve_sub_components(
            ep, endpoint_ws, sp_ws, tile=tile_hint, detail=detail,
        )

        uc_model_name: str | None = None
        config = getattr(ep, "config", None)
        if config:
            ses = list(getattr(config, "served_entities", None) or [])
            sms = list(getattr(config, "served_models", None) or [])
            if ses:
                uc_model_name = getattr(ses[0], "entity_name", None)
            elif sms:
                uc_model_name = getattr(sms[0], "model_name", None)

        display_name = _derive_display_name(
            endpoint_name, detail or tile_hint, uc_model_name,
        )
        description = _derive_description(detail or tile_hint, ep)

        metadata_patch: dict[str, Any] = {
            "tile_id": tile_id,
            "sub_agents": sub_components,
            "task": getattr(ep, "task", None) or "",
        }
        if effective_tile_type:
            metadata_patch["tile_type"] = effective_tile_type

        # Compare against what's already stored so we can report
        # "unchanged" vs "refreshed" without re-hashing every field.
        prev_row = session.exec(
            text(
                "SELECT display_name, description, agent_type, "
                "COALESCE(metadata_json::text, '{}') AS meta "
                "FROM catalog_config WHERE endpoint_name = :n"
            ).bindparams(n=endpoint_name)
        ).first()
        prev_display = str((prev_row or ("", "", "", "{}"))[0] or "")
        prev_description = str((prev_row or ("", "", "", "{}"))[1] or "")
        prev_agent_type = str((prev_row or ("", "", "", "{}"))[2] or "")
        prev_meta = _parse_metadata((prev_row or ("", "", "", "{}"))[3])
        prev_sub_agents = prev_meta.get("sub_agents")

        try:
            session.exec(
                text(
                    """UPDATE catalog_config SET
                        display_name = :display,
                        description = COALESCE(NULLIF(:desc, ''), description),
                        agent_type = :agent_type,
                        metadata_json = COALESCE(metadata_json, '{}'::jsonb) || CAST(:meta AS jsonb),
                        updated_at = NOW()
                    WHERE endpoint_name = :name"""
                ).bindparams(
                    display=display_name,
                    desc=description,
                    agent_type=agent_type.value,
                    meta=json.dumps(metadata_patch),
                    name=endpoint_name,
                )
            )
        except Exception as e:
            msg = str(e).split("Config:")[0].strip()[:200]
            counts["failed"] += 1
            rows.append(
                TileActionRow(
                    endpoint_name=endpoint_name,
                    tile_id=tile_id,
                    status="failed",
                    message=f"DB update failed: {msg}",
                )
            )
            continue

        changed = (
            prev_display != display_name
            or (description and prev_description != description)
            or prev_agent_type != agent_type.value
            or prev_sub_agents != sub_components
        )
        if changed:
            counts["refreshed"] += 1
            rows.append(
                TileActionRow(
                    endpoint_name=endpoint_name,
                    tile_id=tile_id,
                    status="refreshed",
                    message=f"Refreshed -> {display_name}",
                )
            )
            logger.info(
                "Refreshed MAS/KA metadata for %s (tile=%s): name=%r, sub=%d",
                endpoint_name, tile_id, display_name, len(sub_components),
            )
        else:
            counts["unchanged"] += 1
            rows.append(
                TileActionRow(
                    endpoint_name=endpoint_name,
                    tile_id=tile_id,
                    status="unchanged",
                    message="Metadata already up to date.",
                )
            )

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        msg = str(e).split("Config:")[0].strip()[:200]
        # Convert every just-recorded refreshed/unchanged row into a
        # failure since the rollback undid the writes.
        counts = {"refreshed": 0, "unchanged": 0, "failed": 0, "skipped": 0}
        new_rows: list[TileActionRow] = []
        for r in rows:
            if r.status in ("refreshed", "unchanged"):
                counts["failed"] += 1
                new_rows.append(
                    TileActionRow(
                        endpoint_name=r.endpoint_name,
                        tile_id=r.tile_id,
                        status="failed",
                        message=f"Commit failed: {msg}",
                    )
                )
            else:
                counts[r.status] = counts.get(r.status, 0) + 1  # type: ignore[index]
                new_rows.append(r)
        rows = new_rows

    return RescanMetadataResult(
        refreshed=counts["refreshed"],
        unchanged=counts["unchanged"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        rows=rows,
    )


def list_agents(
    session: Session,
    search: str | None = None,
    type_filter: str | None = None,
) -> AgentListOut:
    """Return visible catalog agents, with optional search and type filter.

    Excludes ``genie:*`` rows because Genie Spaces are rendered by their own
    card grid on the catalog page (see ``catalog.index.tsx``); including
    them here would double-render.
    """
    query = (
        "SELECT endpoint_name, display_name, description, agent_type, owner_email, metadata_json "
        "FROM catalog_config WHERE visible = true "
        f"AND endpoint_name NOT LIKE '{_GENIE_ENDPOINT_PREFIX}%'"
    )
    params: dict[str, Any] = {}

    if search:
        query += " AND (LOWER(display_name) LIKE :search OR LOWER(description) LIKE :search)"
        params["search"] = f"%{search.lower()}%"

    if type_filter:
        query += " AND agent_type = :agent_type"
        params["agent_type"] = type_filter

    query += " ORDER BY display_name ASC"

    rows = session.exec(text(query).bindparams(**params)).all()

    agents = []
    for row in rows:
        meta = _parse_metadata(row[5])
        sub_count = len(meta.get("sub_agents", []))
        agents.append(
            AgentSummary(
                endpoint_name=row[0],
                display_name=row[1] or row[0],
                description=row[2] or "",
                agent_type=row[3] or AgentType.MODEL.value,
                sub_agent_count=sub_count,
                owner_email=row[4] or "",
            )
        )

    return AgentListOut(agents=agents)


def get_agent_detail(
    endpoint_name: str,
    ws: WorkspaceClient,
    session: Session,
    sp_ws: WorkspaceClient | None = None,
    user_email: str | None = None,
) -> AgentDetailOut:
    """Return agent detail with best-effort sub-component introspection.

    Uses the persisted `metadata_json.sub_agents` as the source of truth; if
    empty, re-introspects live (slower path).

    ``user_email`` enables the owner fallback: if the OBO
    ``serving_endpoints.get`` probe fails (which happens intermittently
    under stale-consent tokens and has historically locked owners out of
    their own agents) we still mark ``has_access=True`` when the caller's
    email matches ``catalog_config.owner_email``.
    """
    row = session.exec(
        text(
            "SELECT endpoint_name, display_name, description, agent_type, owner_email, metadata_json "
            "FROM catalog_config WHERE endpoint_name = :name"
        ).bindparams(name=endpoint_name)
    ).one_or_none()

    if not row:
        raise NotFoundError(f"Agent '{endpoint_name}' not found in catalog")

    meta = _parse_metadata(row[5])
    owner_email = row[4] or ""

    # Mutable copies that the MAS refresh path can replace below so the
    # response reflects the freshest data we have.
    display_name_db = row[1] or row[0]
    description_db = row[2] or ""

    is_genie = endpoint_name.startswith(_GENIE_ENDPOINT_PREFIX)
    is_uc = _is_uc_endpoint(endpoint_name)
    is_mcp = _is_mcp_endpoint(endpoint_name)

    # On-demand refresh for MAS / KA rows: if the persisted
    # display_name / description / sub_agents look thin (prettified
    # endpoint tail, blank description, empty child graph), hit the
    # Agent Bricks detail API once, upgrade the row, and fall through
    # with the fresh data. Guarded by a 60s TTL cache in
    # ``_load_tile_detail``.
    tile_id = str(meta.get("tile_id") or "").strip()
    tile_type = str(meta.get("tile_type") or "").upper()
    existing_sub_agents = meta.get("sub_agents") or []

    # Endpoint naming convention for Agent Bricks MAS is
    # ``mas-{short_uuid}-endpoint`` and KA is ``ka-...``. When the row
    # was imported via the plain serving-endpoints discovery path (and
    # the tiles list API was scope-denied at the time), ``tile_id``
    # isn't stamped in ``metadata_json``. For those rows, backfill
    # ``tile_id`` by reading ``tile_endpoint_metadata.tile_id`` from
    # the serving-endpoints detail API, then proceed with the detail
    # refresh path below.
    looks_like_mas = (
        tile_type == "MAS"
        or endpoint_name.startswith("mas-")
    )
    looks_like_ka = (
        tile_type == "KA"
        or endpoint_name.startswith("ka-")
    )
    if (
        not tile_id
        and (looks_like_mas or looks_like_ka)
        and not (is_genie or is_uc or is_mcp)
    ):
        try:
            resolved = _resolve_tile_id_from_endpoint(ws, sp_ws, endpoint_name)
        except Exception as e:
            logger.debug(
                "tile_id backfill failed for %s: %s", endpoint_name, e,
            )
            resolved = None
        if resolved:
            tile_id = resolved
            if not tile_type:
                tile_type = "KA" if looks_like_ka else "MAS"

    if (
        tile_id
        and (tile_type in ("MAS", "KA") or looks_like_mas or looks_like_ka)
        and not (is_genie or is_uc or is_mcp)
        and (
            _looks_like_fallback_display_name(display_name_db, endpoint_name)
            or not description_db
            or not existing_sub_agents
        )
    ):
        try:
            detail = _load_tile_detail(
                ws, sp_ws, tile_id=tile_id, endpoint_name=endpoint_name,
                tile_type_hint=tile_type or ("KA" if looks_like_ka else "MAS"),
            )
        except Exception as e:
            logger.debug("Detail refresh failed for %s: %s", endpoint_name, e)
            detail = None

        if detail:
            fresh_name = _derive_display_name(endpoint_name, detail, None) or display_name_db
            fresh_desc = _derive_description(detail, None) or description_db
            fresh_subs = _sub_agents_from_detail(detail)

            meta["sub_agents"] = fresh_subs or existing_sub_agents
            display_name_db = fresh_name
            description_db = fresh_desc

            try:
                session.exec(
                    text(
                        """UPDATE catalog_config SET
                            display_name = :display,
                            description = COALESCE(NULLIF(:desc, ''), description),
                            metadata_json = COALESCE(metadata_json, '{}'::jsonb) || CAST(:meta AS jsonb),
                            updated_at = NOW()
                        WHERE endpoint_name = :name"""
                    ).bindparams(
                        display=fresh_name,
                        desc=fresh_desc,
                        meta=json.dumps(
                            {
                                "sub_agents": fresh_subs or existing_sub_agents,
                                "tile_id": tile_id,
                                "tile_type": tile_type or "MAS",
                            }
                        ),
                        name=endpoint_name,
                    )
                )
                session.commit()
                logger.info(
                    "Refreshed MAS detail for %s (name=%r, sub_agents=%d)",
                    endpoint_name, fresh_name, len(fresh_subs or existing_sub_agents),
                )
            except Exception as e:
                session.rollback()
                logger.warning(
                    "Failed to persist MAS detail refresh for %s: %s",
                    endpoint_name, e,
                )

    # Compute parent access BEFORE building sub-agent infos so we can
    # pass the transitive-access signal down and avoid expensive (and
    # false-negative-prone) per-sub-component OBO probes when the user
    # already has access to the parent MAS.
    if is_genie:
        space_id = endpoint_name[len(_GENIE_ENDPOINT_PREFIX):]
        probe = _genie_has_access(ws, space_id)
        if probe is True:
            has_access = True
        elif probe is False and _owner_has_access(user_email, owner_email):
            # Owner-of-record always has access even if the API probe
            # returns 403 (which can happen when scope is granted at the
            # account level but not yet reflected for the space).
            logger.info(
                "Genie probe denied for %s but user %s is owner -- granting access",
                endpoint_name, user_email,
            )
            has_access = True
        elif probe is None and _owner_has_access(user_email, owner_email):
            logger.info(
                "Genie probe inconclusive for %s; user %s is owner -- granting access",
                endpoint_name, user_email,
            )
            has_access = True
        else:
            has_access = bool(probe)
    elif is_uc or is_mcp:
        # Phase 1: grant read/detail visibility to any authenticated user.
        # Real EXECUTE / tools.list probe happens in Phase 2 right before
        # the chat call (see stream_chat uc:* / mcp: branch). Owner
        # fallback still applies so UC objects owned by the caller are
        # always visible in the UI.
        has_access = True
    else:
        has_access = False
        try:
            ws.serving_endpoints.get(endpoint_name)
            has_access = True
        except Exception as e:
            if _owner_has_access(user_email, owner_email):
                logger.info(
                    "OBO get failed for %s but user %s is owner -- granting access (%s)",
                    endpoint_name, user_email, str(e)[:120],
                )
                has_access = True
            else:
                has_access = False

    # Genie / UC / MCP entries have no MAS sub-components and would crash
    # _build_sub_agent_infos's serving-endpoints fallback. Skip live
    # introspection entirely for them.
    skip_sub_intro = is_genie or is_uc or is_mcp
    sub_agents: list[SubAgentInfo] = (
        []
        if skip_sub_intro
        else _build_sub_agent_infos(
            endpoint_name, ws, meta, sp_ws, parent_has_access=has_access
        )
    )

    return AgentDetailOut(
        endpoint_name=row[0],
        display_name=display_name_db,
        description=description_db,
        agent_type=row[3] or AgentType.MODEL.value,
        owner_email=owner_email,
        has_access=has_access,
        sub_agents=sub_agents,
    )


def check_access(
    endpoint_name: str,
    user_ws: WorkspaceClient,
    session: Session,
    user_email: str | None = None,
) -> AgentAccessOut:
    """Check user's access to the agent and its sub-components using OBO client."""
    row = session.exec(
        text(
            "SELECT metadata_json, owner_email FROM catalog_config WHERE endpoint_name = :name"
        ).bindparams(name=endpoint_name)
    ).one_or_none()
    if not row:
        raise NotFoundError(f"Agent '{endpoint_name}' not found in catalog")

    owner_email = row[1] or ""
    has_access = False
    permission_level = ""

    if endpoint_name.startswith(_GENIE_ENDPOINT_PREFIX):
        space_id = endpoint_name[len(_GENIE_ENDPOINT_PREFIX):]
        probe = _genie_has_access(user_ws, space_id)
        if probe is True:
            has_access = True
            permission_level = "CAN_USE"
        elif probe is False and _owner_has_access(user_email, owner_email):
            has_access = True
            permission_level = "OWNER"
        elif probe is None and _owner_has_access(user_email, owner_email):
            has_access = True
            permission_level = "OWNER"
        else:
            has_access = bool(probe)
            permission_level = "" if has_access else "NONE"
    elif _is_uc_endpoint(endpoint_name) or _is_mcp_endpoint(endpoint_name):
        # Phase 1 grants catalog-level access optimistically; the real
        # EXECUTE / tools.list probe runs in Phase 2 inside stream_chat
        # just before invocation. Owner fallback is preserved.
        if _owner_has_access(user_email, owner_email):
            has_access = True
            permission_level = "OWNER"
        else:
            has_access = True
            permission_level = "CAN_USE_DEFERRED"
    else:
        try:
            user_ws.serving_endpoints.get(endpoint_name)
            has_access = True
            permission_level = "CAN_QUERY"
        except Exception as e:
            err_str = str(e).lower()
            if _owner_has_access(user_email, owner_email):
                logger.info(
                    "OBO get failed for %s but user %s is owner -- granting access",
                    endpoint_name, user_email,
                )
                has_access = True
                permission_level = "OWNER"
            elif "permission" in err_str or "forbidden" in err_str or "403" in err_str:
                has_access = False
            elif "not found" in err_str or "404" in err_str:
                has_access = False
            else:
                logger.warning("Unexpected error checking access for %s: %s", endpoint_name, e)
                has_access = True
                permission_level = "UNKNOWN"

    meta = _parse_metadata(row[0])
    sub_agent_access: dict[str, bool] = {}
    # Genie / UC / MCP entries don't carry MAS-style sub_agents; skip
    # the serving-endpoint based per-sub probe.
    skip_sub = (
        endpoint_name.startswith(_GENIE_ENDPOINT_PREFIX)
        or _is_uc_endpoint(endpoint_name)
        or _is_mcp_endpoint(endpoint_name)
    )
    if not skip_sub:
        # Transitive access: if the user can reach the parent MAS, they
        # can invoke every sub-component through it. The MAS orchestrator
        # forwards requests to KA / Genie / UC / MCP using its own service
        # principal, so the user's direct ACL on the underlying resource
        # is irrelevant. A False here would paint 'Request' chips on
        # components the user can actually use, so we short-circuit when
        # the parent probe already said yes. We only fall back to the
        # per-component probe when the parent was denied -- that path
        # surfaces partial access for edge cases like an owner without
        # CAN_QUERY on the MAS itself.
        for sa in meta.get("sub_agents", []):
            if not isinstance(sa, dict):
                continue
            sa_name = sa.get("name", "")
            if not sa_name:
                continue
            if has_access:
                sub_agent_access[sa_name] = True
            else:
                sub_agent_access[sa_name] = _component_has_access(user_ws, sa)

    return AgentAccessOut(
        endpoint_name=endpoint_name,
        has_access=has_access,
        permission_level=permission_level,
        sub_agent_access=sub_agent_access,
    )


def _build_sub_agent_infos(
    endpoint_name: str,
    ws: WorkspaceClient,
    cached_meta: dict[str, Any],
    sp_ws: WorkspaceClient | None = None,
    parent_has_access: bool | None = None,
) -> list[SubAgentInfo]:
    """Turn the persisted `sub_agents` metadata into typed SubAgentInfo records.

    When ``parent_has_access`` is ``True`` we grant transitive access to
    every sub-component. MAS orchestrators invoke their children via the
    MAS's own service principal, so the caller's direct ACL on the
    underlying KA / Genie / UC / MCP endpoint is irrelevant for
    invocation. Painting sub-rows as 'Request' based on an OBO probe of
    the child surface produces false negatives -- we saw this with KA
    and Genie components that the user could already chat through the
    MAS. Fall back to the per-component probe only when the parent was
    explicitly denied or unknown.
    """
    cached = cached_meta.get("sub_agents", []) or []

    if not cached:
        # Live-introspect as a fallback (slower, needs OBO scope on serving endpoints).
        try:
            ep = ws.serving_endpoints.get(endpoint_name)
            # Try to pick up a matching Agent Bricks tile too, so MAS
            # instruction-parsing works here as well.
            tiles_map = _load_tiles_map(ws, sp_ws)
            tile = tiles_map.get(endpoint_name)
            detail = None
            if tile:
                tile_type_str = str(tile.get("tile_type") or "").upper()
                if tile_type_str in ("MAS", "KA"):
                    tile_id = tile.get("tile_id") or tile.get("id")
                    detail = _load_tile_detail(
                        ws, sp_ws,
                        tile_id=str(tile_id) if tile_id else None,
                        endpoint_name=endpoint_name,
                        tile_type_hint=tile_type_str,
                    )
            cached = _resolve_sub_components(
                ep, ws, sp_ws, tile=tile, detail=detail,
            )
        except Exception as e:
            logger.debug("Live fallback introspection failed for %s: %s", endpoint_name, e)
            cached = []

    out: list[SubAgentInfo] = []
    for sa in cached:
        if not isinstance(sa, dict):
            continue
        name = sa.get("name")
        if not name:
            continue
        sub_type = _coerce_sub_component_type(sa.get("type"))
        if parent_has_access is True:
            sub_has_access = True
        else:
            sub_has_access = _component_has_access(ws, sa)
        out.append(
            SubAgentInfo(
                name=name,
                type=sub_type,
                description=sa.get("description", ""),
                has_access=sub_has_access,
                owner_email=sa.get("owner_email", "") or "",
                endpoint_ref=str(sa.get("endpoint_ref") or "").strip(),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Genie Spaces -- read-through from /api/2.0/genie/spaces
# --------------------------------------------------------------------------- #

def list_genie_spaces(
    ws: WorkspaceClient,
    sp_ws: WorkspaceClient | None = None,
    session: Session | None = None,
) -> GenieSpaceListOut:
    """List Genie Spaces the caller can see, via the user's OBO client.

    Backed by ``GET /api/2.0/genie/spaces``. Returns an empty list on
    permission / scope failures so the catalog page can render gracefully
    even when the ``dashboards.genie`` scope isn't granted. Falls back to
    the app service principal so admins can still populate the Genie tab
    when a user OBO token is missing the right scope.

    When ``session`` is provided we also:
      1. Upsert any previously-unseen space into ``catalog_config``
         (persist-on-read), so admins don't have to press *Discover*
         before hiding a space on /admin/catalog.
      2. Drop spaces whose catalog_config row is ``visible=false``, so
         the admin-level Hide toggle actually takes effect for end users.
    """
    raw_spaces = _fetch_genie_spaces_raw(ws, sp_ws)

    visibility_map: dict[str, bool] = {}
    if session is not None and raw_spaces:
        try:
            visibility_map = _persist_and_fetch_visibility(session, raw_spaces)
        except Exception as e:
            # Don't fail the user-facing catalog if the persistence layer
            # hiccups; just log and degrade to "show everything OBO returned".
            logger.warning("Genie visibility lookup failed, showing all: %s", e)

    out: list[GenieSpaceSummary] = []
    for sp in raw_spaces:
        if not isinstance(sp, dict):
            continue
        space_id = sp.get("space_id") or sp.get("id") or ""
        title = sp.get("title") or sp.get("name") or ""
        if not space_id or not title:
            continue
        if visibility_map.get(str(space_id)) is False:
            continue
        out.append(
            GenieSpaceSummary(
                space_id=str(space_id),
                title=str(title),
                description=str(sp.get("description") or ""),
                warehouse_id=str(sp.get("warehouse_id") or ""),
                has_access=True,
            )
        )
    logger.info("Listed %d Genie Space(s)", len(out))
    return GenieSpaceListOut(spaces=out)


def _persist_and_fetch_visibility(
    session: Session,
    raw_spaces: list[dict[str, Any]],
) -> dict[str, bool]:
    """Insert any new Genie rows and return {space_id: visible} for known ones.

    Keeps writes inside a single transaction so a partial failure doesn't
    leave orphan rows. We intentionally don't overwrite ``display_name`` on
    persist-on-read -- that's what *Discover* is for; if the API returns a
    renamed title we'll refresh on the next discover run rather than
    stomping any admin-side tweaks mid-read.
    """
    space_ids = [
        str(sp.get("space_id") or sp.get("id") or "").strip()
        for sp in raw_spaces
        if isinstance(sp, dict)
    ]
    space_ids = [s for s in space_ids if s]
    if not space_ids:
        return {}

    endpoint_names = [_genie_endpoint_name(s) for s in space_ids]

    try:
        rows = session.exec(
            text(
                "SELECT endpoint_name, visible FROM catalog_config "
                "WHERE endpoint_name = ANY(:names)"
            ).bindparams(names=endpoint_names)
        ).all()
    except Exception:
        # Older Postgres or mocked sessions may not support ANY -- fall back
        # to a per-id SELECT so the feature still works.
        rows = []
        for en in endpoint_names:
            r = session.exec(
                text(
                    "SELECT endpoint_name, visible FROM catalog_config "
                    "WHERE endpoint_name = :n"
                ).bindparams(n=en)
            ).one_or_none()
            if r:
                rows.append(r)

    existing: dict[str, bool] = {}
    for r in rows:
        en = str(r[0])
        if en.startswith(_GENIE_ENDPOINT_PREFIX):
            existing[en[len(_GENIE_ENDPOINT_PREFIX):]] = bool(r[1]) if r[1] is not None else True

    # Persist-on-read for any space we've never seen before.
    inserted_any = False
    visible_default = _default_visible_for(AgentType.GENIE_SPACE)
    for sp in raw_spaces:
        if not isinstance(sp, dict):
            continue
        space_id = str(sp.get("space_id") or sp.get("id") or "").strip()
        title = str(sp.get("title") or sp.get("name") or "").strip()
        if not space_id or not title or space_id in existing:
            continue
        metadata = {
            "kind": "genie_space",
            "space_id": space_id,
            "warehouse_id": str(sp.get("warehouse_id") or ""),
        }
        try:
            session.exec(
                text(
                    """INSERT INTO catalog_config
                        (endpoint_name, display_name, description, agent_type, visible, owner_email, metadata_json)
                    VALUES (:name, :display, :desc, :agent_type, :visible, '', CAST(:meta AS jsonb))
                    ON CONFLICT (endpoint_name) DO NOTHING"""
                ).bindparams(
                    name=_genie_endpoint_name(space_id),
                    display=title,
                    desc=str(sp.get("description") or ""),
                    agent_type=AgentType.GENIE_SPACE.value,
                    visible=visible_default,
                    meta=json.dumps(metadata),
                )
            )
            existing[space_id] = visible_default
            inserted_any = True
        except Exception as e:
            logger.debug("Genie persist-on-read upsert skipped for %s: %s", space_id, e)

    if inserted_any:
        try:
            session.commit()
        except Exception as e:
            session.rollback()
            logger.warning("Genie persist-on-read commit failed: %s", e)

    return existing
