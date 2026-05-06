"""Thin HTTP router -- every handler delegates to a typed service."""

from __future__ import annotations

import logging
import os

from fastapi import Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, text

logger = logging.getLogger(__name__)

from .core import (
    HeadersDependency,
    LakebaseDependency,
    UserWorkspaceClientDependency,
    create_router,
)
from .core.lakebase import migration_status
from .core.auth import (
    _resolve_user_email,
    _get_user_role,
    require_debug_admin,
    require_role,
)
from .models import (
    AdminSettingOut,
    AdminSettingUpdate,
    AdminSettingsOut,
    AgentAccessOut,
    AgentDetailOut,
    AgentListOut,
    AppConfigOut,
    CatalogEntryOut,
    CatalogEntryUpdate,
    ChartArtifact,
    ChartListOut,
    ChatRequest,
    ConversationDetailOut,
    ConversationListOut,
    DeleteResult,
    DiscoverResult,
    FeatureFlag,
    FeatureFlags,
    GenieSpaceListOut,
    GrantAccessResult,
    HealthLiveOut,
    HealthReadyOut,
    ManualUCEndpointIn,
    PinClickResult,
    PinIn,
    PinListOut,
    PinOut,
    PinPatch,
    RescanMetadataResult,
    ScopeDebugOut,
    SuggestionsOut,
    UCTagConfig,
    UCTagConfigUpdate,
    UserOut,
    UserPrefsOut,
    UserPrefsUpdate,
)
from .services import (
    admin_service,
    catalog_service,
    chart_service,
    chat_service,
    debug_service,
    feature_flags_service,
    pin_service,
    suggestion_service,
    user_prefs_service,
)
from .services.base import NotFoundError

router = create_router()


def _get_optional_session(request: Request) -> Session | None:
    engine = request.app.state.engine
    if engine is None:
        return None
    return Session(bind=engine)


# -- User / Health --


@router.get("/me", response_model=UserOut, operation_id="currentUser")
def me(request: Request) -> UserOut:
    email = _resolve_user_email(request)
    session = _get_optional_session(request)
    try:
        role = _get_user_role(session, email) if session else "user"
    finally:
        if session:
            session.close()

    display_name = email.split("@")[0].replace(".", " ").title() if email else "User"
    return UserOut(email=email, role=role, display_name=display_name)


# -- User preferences (Phase 3 iOS redesign) --
# Tiny surface area -- GET returns the stored prefs (or defaults for new
# users) and PUT upserts them with PATCH-like semantics. Lives next to
# /me because both are strictly per-caller.


@router.get(
    "/user/prefs",
    response_model=UserPrefsOut,
    operation_id="getUserPrefs",
)
def get_user_prefs(
    request: Request,
    session: LakebaseDependency,
) -> UserPrefsOut:
    email = _resolve_user_email(request)
    return user_prefs_service.get_prefs(session, email)


@router.put(
    "/user/prefs",
    response_model=UserPrefsOut,
    operation_id="putUserPrefs",
)
def put_user_prefs(
    body: UserPrefsUpdate,
    request: Request,
    session: LakebaseDependency,
) -> UserPrefsOut:
    email = _resolve_user_email(request)
    return user_prefs_service.put_prefs(session, email, body)


def _resolve_feature_flags(
    session: Session | None, user_email: str
) -> FeatureFlags:
    """Build the :class:`FeatureFlags` payload returned on ``/app/config``.

    Reads the admin master state and the caller's per-user overrides,
    then folds them into the three-state struct
    (``master_on``/``default_on``/``effective_on``) the frontend needs to
    decide whether to *show* a toggle and whether to *render* the
    feature.

    On any failure (no Lakebase, no ``feature_flags`` row) we return the
    safe defaults: the entire feature set ``master_on=False`` so the
    legacy chrome stays in place.
    """
    if session is None:
        return FeatureFlags()
    try:
        admin = feature_flags_service.get_admin_flags(session)
        overrides = feature_flags_service.get_user_overrides(session, user_email)
    except Exception as e:
        logger.warning("feature flag resolution failed for /app/config: %s", e)
        return FeatureFlags()

    def _resolve(key: str) -> FeatureFlag:
        cfg = admin.get(key) or {}
        master_on = bool(cfg.get("enabled", False))
        default_on = bool(cfg.get("default_on", False))
        user_off = overrides.get(key) is False
        effective = master_on and default_on and (not user_off)
        return FeatureFlag(
            master_on=master_on,
            default_on=default_on,
            effective_on=effective,
        )

    return FeatureFlags(
        ai_suggestions=_resolve("ai_suggestions"),
        charts=_resolve("charts"),
        pinned=_resolve("pinned"),
    )


# Public frontend config. Read before ThemeProvider hydrates so the
# legacy-UI kill switch can fully skip loading the new Clarity palette.
# We intentionally do not require auth here, but we still try to resolve
# the caller so the per-user opt-out toggles for Phase 4 features can
# render correctly. When auth or Lakebase isn't available we fall back
# to safe defaults rather than 500ing the cold-boot path.
@router.get(
    "/app/config",
    response_model=AppConfigOut,
    operation_id="getAppConfig",
)
def get_app_config(request: Request) -> AppConfigOut:
    legacy_ui = os.environ.get("AGENT_HUB_LEGACY_UI", "").strip() == "1"
    user_email = ""
    try:
        user_email = _resolve_user_email(request)
    except Exception:
        # Anonymous boot path -- treat as "no user override".
        user_email = ""

    flags = FeatureFlags()
    session = _get_optional_session(request)
    try:
        flags = _resolve_feature_flags(session, user_email)
    finally:
        if session is not None:
            session.close()

    return AppConfigOut(legacy_ui=legacy_ui, feature_flags=flags)


@router.get("/health/live", response_model=HealthLiveOut, operation_id="healthLive")
def health_live() -> HealthLiveOut:
    return HealthLiveOut()


@router.get("/health/ready", response_model=HealthReadyOut, operation_id="healthReady")
def health_ready(request: Request) -> HealthReadyOut:
    engine = request.app.state.engine
    db_status = "unavailable"
    if engine is not None:
        try:
            with Session(bind=engine) as session:
                session.exec(text("SELECT 1"))
                db_status = "ok"
        except Exception:
            pass

    ws_status = "ok"
    try:
        ws = request.app.state.workspace_client
        ws.current_user.me()
    except Exception:
        ws_status = "unavailable"

    overall = "ok" if db_status == "ok" and ws_status == "ok" else "degraded"
    return HealthReadyOut(
        status=overall,
        database=db_status,
        workspace=ws_status,
        migration_status=dict(migration_status),
    )


# -- Debug / Diagnostics --


@router.get(
    "/debug/me/scopes",
    response_model=ScopeDebugOut,
    operation_id="debugMyScopes",
    dependencies=[Depends(require_debug_admin)],
)
def debug_my_scopes(
    request: Request,
    headers: HeadersDependency,
) -> ScopeDebugOut:
    """Diff declared OBO scopes vs scopes actually embedded in the forwarded token.

    See ``docs/obo-auth-design.md`` §14 (Debug runbook). Admin-only. Never
    emits the raw token value.
    """
    return debug_service.inspect_scopes(
        headers=headers,
        sp_ws=request.app.state.workspace_client,
    )


# -- Agent Catalog --


@router.get("/agents", response_model=AgentListOut, operation_id="listAgents")
def list_agents(
    request: Request,
    session: LakebaseDependency,
    user_ws: UserWorkspaceClientDependency,
    search: str | None = Query(None, description="Search by name or description"),
    type: str | None = Query(None, alias="type", description="Filter by agent_type"),
) -> AgentListOut:
    user_email = _resolve_user_email(request)
    out = catalog_service.list_agents(session, search=search, type_filter=type)
    # Enrich with per-agent has_access using the user's OBO client. If the
    # OBO probe fails (common with stale-consent / 970-byte tokens we've
    # seen in prod) but the caller owns the agent in catalog_config, grant
    # access anyway -- the owner should never be locked out of their own
    # endpoint. See docs/obo-auth-design.md §14.
    for agent in out.agents:
        try:
            user_ws.serving_endpoints.get(agent.endpoint_name)
            agent.has_access = True
        except Exception as e:
            if catalog_service._owner_has_access(user_email, agent.owner_email):
                agent.has_access = True
                logger.info(
                    "OBO get failed for %s but user %s is owner -- granting access (%s)",
                    agent.endpoint_name, user_email, str(e)[:120],
                )
            else:
                agent.has_access = False
    return out


@router.get("/agents/{endpoint_name}", response_model=AgentDetailOut, operation_id="getAgent")
def get_agent(
    endpoint_name: str,
    request: Request,
    session: LakebaseDependency,
    user_ws: UserWorkspaceClientDependency,
) -> AgentDetailOut:
    # Use the OBO client for per-user checks; the SP client for UC
    # model-version introspection (needs broader catalog scope).
    sp_ws = request.app.state.workspace_client
    user_email = _resolve_user_email(request)
    return catalog_service.get_agent_detail(
        endpoint_name, user_ws, session, sp_ws, user_email=user_email,
    )


@router.get("/agents/{endpoint_name}/access", response_model=AgentAccessOut, operation_id="checkAgentAccess")
def check_agent_access(
    endpoint_name: str,
    request: Request,
    session: LakebaseDependency,
    user_ws: UserWorkspaceClientDependency,
) -> AgentAccessOut:
    user_email = _resolve_user_email(request)
    return catalog_service.check_access(endpoint_name, user_ws, session, user_email=user_email)


@router.post(
    "/agents/discover",
    response_model=DiscoverResult,
    operation_id="discoverAgents",
    dependencies=[Depends(require_role("admin"))],
)
def discover_agents(
    request: Request,
    session: LakebaseDependency,
    user_ws: UserWorkspaceClientDependency,
) -> DiscoverResult:
    # OBO for listing/GET on serving endpoints; SP for UC model-version
    # introspection (catalog.models scope not granted to OBO by default).
    sp_ws = request.app.state.workspace_client
    return catalog_service.discover_from_workspace(user_ws, session, sp_ws)


@router.get(
    "/catalog/genie-spaces",
    response_model=GenieSpaceListOut,
    operation_id="listGenieSpaces",
)
def list_genie_spaces(
    request: Request,
    user_ws: UserWorkspaceClientDependency,
) -> GenieSpaceListOut:
    """Read-through list of Genie Spaces the caller can see via OBO.

    Falls back to the app service principal when OBO lacks ``dashboards.genie``
    scope so admins can still populate the Genie tab. When Lakebase is
    reachable we also persist-on-read into ``catalog_config`` so the Hide
    toggle on /admin/catalog takes effect immediately.
    """
    sp_ws = request.app.state.workspace_client
    session = _get_optional_session(request)
    try:
        return catalog_service.list_genie_spaces(user_ws, sp_ws, session=session)
    finally:
        if session:
            session.close()


@router.post(
    "/admin/catalog/reclassify",
    response_model=DiscoverResult,
    operation_id="reclassifyCatalog",
    dependencies=[Depends(require_role("admin"))],
)
def reclassify_catalog(
    request: Request,
    session: LakebaseDependency,
    user_ws: UserWorkspaceClientDependency,
) -> DiscoverResult:
    """Re-run classification + sub-component introspection for existing rows.

    Idempotent. Use once after upgrading the classifier logic to backfill
    ``agent_type`` and ``metadata_json.sub_agents`` on previously-discovered
    endpoints. Runs with the admin's OBO client for endpoint GETs and the
    app SP for UC model-version introspection.
    """
    sp_ws = request.app.state.workspace_client
    return catalog_service.reclassify_existing(user_ws, session, sp_ws)


@router.post(
    "/admin/catalog/grant-access",
    response_model=GrantAccessResult,
    operation_id="grantCatalogAccess",
    dependencies=[Depends(require_role("admin"))],
)
def grant_catalog_access(
    request: Request,
    session: LakebaseDependency,
    user_ws: UserWorkspaceClientDependency,
) -> GrantAccessResult:
    """Add the app SP to every MAS/KA tile ACL the admin manages (CAN_MANAGE).

    Runs under the admin's OBO -- the PATCH to
    ``/api/2.0/permissions/knowledge-assistants/{tile_id}`` requires the
    caller to already have ``CAN_MANAGE`` on the tile (workspace admin
    or tile owner). Tiles the admin doesn't manage land in the
    ``unauthorized`` bucket so the UI can show a targeted owner-handoff
    message instead of failing the whole call.

    Idempotent: GETs the ACL first and only PATCHes when the SP isn't
    already a manager, so repeated clicks are a no-op. After this
    succeeds, click "Rescan metadata" to refresh display names and
    sub-agent graphs via the SP.
    """
    sp_ws = request.app.state.workspace_client
    return catalog_service.grant_sp_access_on_tiles(user_ws, sp_ws, session)


@router.post(
    "/admin/catalog/rescan-metadata",
    response_model=RescanMetadataResult,
    operation_id="rescanCatalogMetadata",
    dependencies=[Depends(require_role("admin"))],
)
def rescan_catalog_metadata(
    request: Request,
    session: LakebaseDependency,
    user_ws: UserWorkspaceClientDependency,
) -> RescanMetadataResult:
    """Refresh display name / description / sub-agents for every MAS/KA tile.

    Uses two clients. The admin's OBO handles the serving-endpoint
    lookup (``serving_endpoints.get``) and tile-id resolution -- those
    calls only need ``serving.serving-endpoints`` scope which Apps
    already expose, and the admin has View on any tile they manage.
    The app SP handles ``/api/2.0/multi-agent-supervisors/{tile_id}``
    because that endpoint requires ``all-apis`` which Databricks Apps
    OBO cannot carry. The SP must already be in each tile's ACL with
    CAN_MANAGE -- run "Grant catalog access" first for tiles that
    return ``failed``.

    Bypasses the 60s ``_TILE_DETAIL_CACHE`` so every click sees the
    authoritative Agent Bricks state.
    """
    sp_ws = request.app.state.workspace_client
    return catalog_service.rescan_mas_ka_metadata(user_ws, sp_ws, session)


# -- Chat --


@router.post("/chat/{endpoint_name}", operation_id="chatStream")
def chat(
    endpoint_name: str,
    body: ChatRequest,
    request: Request,
    session: LakebaseDependency,
    user_ws: UserWorkspaceClientDependency,
) -> StreamingResponse:
    user_email = _resolve_user_email(request)
    ws = user_ws
    engine = request.app.state.engine
    sp_ws = request.app.state.workspace_client

    generator = chat_service.stream_chat(
        endpoint_name=endpoint_name,
        conversation_id=body.conversation_id,
        user_message=body.message,
        user_email=user_email,
        ws=ws,
        session=session,
        engine=engine,
        sp_ws=sp_ws,
        tool_choice=body.tool_choice,
    )

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# -- Conversations --


@router.get("/conversations", response_model=ConversationListOut, operation_id="listConversations")
def list_conversations(
    request: Request,
    session: LakebaseDependency,
) -> ConversationListOut:
    user_email = _resolve_user_email(request)
    return chat_service.list_conversations(user_email, session)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut, operation_id="getConversation")
def get_conversation(
    conversation_id: str,
    request: Request,
    session: LakebaseDependency,
) -> ConversationDetailOut:
    user_email = _resolve_user_email(request)
    return chat_service.get_conversation(conversation_id, user_email, session)


@router.delete("/conversations/{conversation_id}", response_model=DeleteResult, operation_id="deleteConversation")
def delete_conversation_route(
    conversation_id: str,
    request: Request,
    session: LakebaseDependency,
) -> DeleteResult:
    user_email = _resolve_user_email(request)
    return chat_service.delete_conversation(conversation_id, user_email, session)


# -- Admin --


@router.get(
    "/admin/settings",
    response_model=AdminSettingsOut,
    operation_id="getAdminSettings",
)
def get_admin_settings(session: LakebaseDependency) -> AdminSettingsOut:
    return admin_service.get_all_settings(session)


@router.put(
    "/admin/settings/{key}",
    response_model=AdminSettingOut,
    operation_id="updateAdminSetting",
    dependencies=[Depends(require_role("admin"))],
)
def update_admin_setting(
    key: str,
    body: AdminSettingUpdate,
    request: Request,
    session: LakebaseDependency,
) -> AdminSettingOut:
    user_email = _resolve_user_email(request)
    return admin_service.update_setting(session, key, body.value, user_email)


@router.get(
    "/admin/catalog",
    response_model=list[CatalogEntryOut],
    operation_id="listAdminCatalog",
    dependencies=[Depends(require_role("admin"))],
)
def list_admin_catalog(session: LakebaseDependency) -> list[CatalogEntryOut]:
    return admin_service.list_catalog_entries(session)


@router.put(
    "/admin/catalog/{endpoint_name}",
    response_model=CatalogEntryOut,
    operation_id="updateAdminCatalogEntry",
    dependencies=[Depends(require_role("admin"))],
)
def update_admin_catalog_entry(
    endpoint_name: str,
    body: CatalogEntryUpdate,
    request: Request,
    session: LakebaseDependency,
) -> CatalogEntryOut:
    user_email = _resolve_user_email(request)
    updates = body.model_dump(exclude_unset=True)
    return admin_service.update_catalog_entry(session, endpoint_name, updates, user_email)


# -- UC Tag Config (Phase 1: HTTP + MCP tagged agents) --
#
# Reads are available to any authenticated user so the UI can render the
# current tag scheme on the discover button tooltip / empty-state help,
# but writes are admin-only.


@router.get(
    "/admin/tag-config",
    response_model=UCTagConfig,
    operation_id="getUCTagConfig",
)
def get_uc_tag_config(session: LakebaseDependency) -> UCTagConfig:
    return admin_service.get_uc_tag_config(session)


@router.put(
    "/admin/tag-config",
    response_model=UCTagConfig,
    operation_id="updateUCTagConfig",
    dependencies=[Depends(require_role("admin"))],
)
def update_uc_tag_config(
    body: UCTagConfigUpdate,
    request: Request,
    session: LakebaseDependency,
) -> UCTagConfig:
    user_email = _resolve_user_email(request)
    return admin_service.update_uc_tag_config(session, body, user_email)


# -- Manual UC endpoint registration --
#
# Fallback path for workspaces where ``system.information_schema.function_tags
# / connection_tags`` aren't available (UC v1, pre-GA regions) or where
# admins simply haven't tagged their UC objects yet. Writes the same row
# shape as tag-discovery into ``catalog_config`` and flags it with
# ``metadata_json.manual = true`` so it survives (and is visible to) the
# admin UI. Read/write routes are admin-only; the row shows up to every
# user as a regular catalog tile once visible.


@router.get(
    "/admin/uc-endpoints",
    response_model=list[CatalogEntryOut],
    operation_id="listManualUCEndpoints",
    dependencies=[Depends(require_role("admin"))],
)
def list_manual_uc_endpoints(
    session: LakebaseDependency,
) -> list[CatalogEntryOut]:
    return admin_service.list_manual_uc_endpoints(session)


@router.post(
    "/admin/uc-endpoints",
    response_model=CatalogEntryOut,
    operation_id="registerManualUCEndpoint",
    dependencies=[Depends(require_role("admin"))],
    status_code=201,
)
def register_manual_uc_endpoint(
    body: ManualUCEndpointIn,
    request: Request,
    session: LakebaseDependency,
) -> CatalogEntryOut:
    user_email = _resolve_user_email(request)
    return admin_service.register_uc_endpoint(session, body, user_email)


@router.delete(
    "/admin/uc-endpoints/{endpoint_name:path}",
    response_model=DeleteResult,
    operation_id="unregisterManualUCEndpoint",
    dependencies=[Depends(require_role("admin"))],
)
def unregister_manual_uc_endpoint(
    endpoint_name: str,
    request: Request,
    session: LakebaseDependency,
) -> DeleteResult:
    user_email = _resolve_user_email(request)
    admin_service.unregister_uc_endpoint(session, endpoint_name, user_email)
    return DeleteResult(deleted=True, id=endpoint_name)


# -- Phase 4: Suggestions, Charts, Pins --
#
# All three routes are guarded by the resolved feature flag for the
# caller. Returning 404 on a master-off flag is intentional: it lets the
# frontend treat "feature off" identically to "no data" without leaking
# capability information back to clients that wouldn't be allowed to
# render the UI anyway.


def _pin_dict_to_out(pin: dict[str, object]) -> PinOut:
    """Map a service-layer pin dict to the API response model."""
    return PinOut(
        id=str(pin.get("id") or ""),
        user_email=str(pin.get("user_email") or ""),
        endpoint_name=str(pin.get("endpoint_name") or ""),
        text=str(pin.get("text") or ""),
        label=(str(pin.get("label")) if pin.get("label") is not None else None),
        position=int(pin.get("position") or 0),  # type: ignore[arg-type]
        created_at=pin.get("created_at"),  # type: ignore[arg-type]
    )


def _require_feature(
    session: Session, user_email: str, key: str
) -> None:
    """Translate "feature disabled" into a 404 NotFoundError.

    Used by every Phase 4 route so the same code path runs whether the
    feature is master-off, the user opted out, or the message simply
    doesn't have an associated artifact -- the frontend already handles
    "404 means nothing to render".
    """
    if not feature_flags_service.is_enabled(session, user_email, key):  # type: ignore[arg-type]
        raise NotFoundError(f"Feature '{key}' is not enabled for this user")


@router.get(
    "/pins/{endpoint_name}",
    response_model=PinListOut,
    operation_id="listPins",
)
def list_pins_route(
    endpoint_name: str,
    request: Request,
    session: LakebaseDependency,
) -> PinListOut:
    """List the caller's pins for a given agent endpoint, in display order."""
    user_email = _resolve_user_email(request)
    _require_feature(session, user_email, "pinned")
    pins = pin_service.list_pins(
        session, user_email=user_email, endpoint_name=endpoint_name
    )
    return PinListOut(pins=[_pin_dict_to_out(p) for p in pins])


@router.post(
    "/pins/{endpoint_name}",
    response_model=PinOut,
    operation_id="createPin",
)
def create_pin_route(
    endpoint_name: str,
    body: PinIn,
    request: Request,
    session: LakebaseDependency,
) -> PinOut:
    """Pin a question for the caller against ``endpoint_name``.

    The service raises :class:`ConflictError` (-> 409) when a pin with
    the same normalized text already exists for this user/agent and
    :class:`ValidationError` (-> 422) when the per-agent quota is
    exhausted.
    """
    user_email = _resolve_user_email(request)
    _require_feature(session, user_email, "pinned")
    pin = pin_service.create_pin(
        session,
        user_email=user_email,
        endpoint_name=endpoint_name,
        text_value=body.text,
        label=body.label,
        position=body.position,
    )
    return _pin_dict_to_out(pin)


@router.patch(
    "/pins/{endpoint_name}/{pin_id}",
    response_model=PinOut,
    operation_id="updatePin",
)
def update_pin_route(
    endpoint_name: str,
    pin_id: str,
    body: PinPatch,
    request: Request,
    session: LakebaseDependency,
) -> PinOut:
    """Update label and/or position on an existing pin (owner-only).

    PATCH semantics: only fields the client explicitly sent are changed.
    We use ``model_fields_set`` to distinguish "client passed null" from
    "client omitted" because the service supports both (null clears the
    label).
    """
    user_email = _resolve_user_email(request)
    _require_feature(session, user_email, "pinned")
    fields_set = body.model_fields_set
    pin = pin_service.update_pin(
        session,
        user_email=user_email,
        endpoint_name=endpoint_name,
        pin_id=pin_id,
        label=body.label,
        position=body.position,
        label_set="label" in fields_set,
        position_set="position" in fields_set,
    )
    return _pin_dict_to_out(pin)


@router.delete(
    "/pins/{endpoint_name}/{pin_id}",
    response_model=DeleteResult,
    operation_id="deletePin",
)
def delete_pin_route(
    endpoint_name: str,
    pin_id: str,
    request: Request,
    session: LakebaseDependency,
) -> DeleteResult:
    """Delete a pin (owner-only). Missing or foreign pins return 404."""
    user_email = _resolve_user_email(request)
    _require_feature(session, user_email, "pinned")
    pin_service.delete_pin(
        session,
        user_email=user_email,
        endpoint_name=endpoint_name,
        pin_id=pin_id,
    )
    return DeleteResult(deleted=True, id=pin_id)


@router.post(
    "/pins/{endpoint_name}/{pin_id}/click",
    response_model=PinClickResult,
    operation_id="recordPinClick",
)
def record_pin_click_route(
    endpoint_name: str,
    pin_id: str,
    request: Request,
    session: LakebaseDependency,
) -> PinClickResult:
    """Record a ``click`` event when the user re-submits a pinned question.

    Ownership is enforced by :func:`pin_service.record_click` -- a
    mismatch between the calling user and the pin's owner returns 404
    (same shape as a non-existent pin, so a probing peer cannot tell
    the difference). The telemetry write itself is best-effort; a DB
    failure returns ``recorded=false`` without raising.

    Feature-gated under ``pinned`` (the whole pin subsystem shares one
    flag), so if the user has opted out of pins this endpoint is 404.
    """
    user_email = _resolve_user_email(request)
    _require_feature(session, user_email, "pinned")
    recorded = pin_service.record_click(
        session,
        user_email=user_email,
        endpoint_name=endpoint_name,
        pin_id=pin_id,
    )
    return PinClickResult(ok=True, recorded=recorded)


@router.get(
    "/messages/{message_id}/chart",
    response_model=ChartArtifact,
    operation_id="getMessageChart",
)
def get_message_chart(
    message_id: str,
    request: Request,
    session: LakebaseDependency,
) -> ChartArtifact:
    """Rehydrate the ECharts artifact attached to a Genie assistant message.

    Used on conversation reload: ``MessageOut.chart_id`` tells the UI a
    chart exists; the UI then calls this endpoint lazily as the message
    scrolls into view to keep the initial paint cheap.

    Authorization is enforced by joining through ``messages`` to
    ``conversations`` and matching ``conversations.user_email`` against
    the caller -- a peer cannot read another user's chart even with a
    leaked message id.
    """
    user_email = _resolve_user_email(request)
    _require_feature(session, user_email, "charts")

    artifact = chart_service.get_artifact(session, message_id)
    if artifact is None:
        raise NotFoundError(f"No chart found for message '{message_id}'")

    owner_row = session.exec(
        text(
            """SELECT c.user_email
                 FROM messages m
                 JOIN conversations c ON c.id = m.conversation_id
                WHERE m.id = CAST(:mid AS uuid)"""
        ).bindparams(mid=message_id)
    ).one_or_none()
    owner_email = str(owner_row[0]) if owner_row and owner_row[0] is not None else ""
    if owner_email and owner_email != user_email:
        # Same shape as a missing artifact -- never confirm existence to
        # a non-owner. Matches the access-control posture on
        # /conversations/{id}.
        raise NotFoundError(f"No chart found for message '{message_id}'")

    return ChartArtifact(
        chart_id=str(artifact.get("id") or ""),
        message_id=str(artifact.get("message_id") or ""),
        conversation_id=str(artifact.get("conversation_id") or ""),
        chart_kind=str(artifact.get("chart_kind") or "table"),  # type: ignore[arg-type]
        title=str(artifact.get("title") or ""),
        columns=list(artifact.get("columns") or []),  # type: ignore[arg-type]
        rows=list(artifact.get("rows") or []),  # type: ignore[arg-type]
        option=dict(artifact.get("option") or {}),  # type: ignore[arg-type]
        truncated=bool(artifact.get("truncated") or False),
        idx=int(artifact.get("idx") or 0),
        created_at=artifact.get("created_at"),  # type: ignore[arg-type]
    )


@router.get(
    "/messages/{message_id}/charts",
    response_model=ChartListOut,
    operation_id="listMessageCharts",
)
def list_message_charts(
    message_id: str,
    request: Request,
    session: LakebaseDependency,
) -> ChartListOut:
    """Rehydrate every ECharts artifact attached to an assistant message.

    Genie can return multiple ``query`` attachments per turn (primary
    query + follow-up drill-downs), and each one becomes a separate row
    in ``chart_artifacts``. The single-artifact ``GET .../chart`` route
    only returns the first; this endpoint returns the full stack in
    render order so the UI can show a 1-of-N rail.

    Returns an empty ``charts`` list (200) when the message exists and
    the caller owns it but no charts were produced -- matches how
    text-only Genie answers render. Returns 404 when the message does
    not exist *or* the caller doesn't own it; the shape is identical
    so a probing peer cannot distinguish the two cases (mirrors the
    posture on ``GET /conversations/{id}``).
    """
    user_email = _resolve_user_email(request)
    _require_feature(session, user_email, "charts")

    owner_row = session.exec(
        text(
            """SELECT c.user_email
                 FROM messages m
                 JOIN conversations c ON c.id = m.conversation_id
                WHERE m.id = CAST(:mid AS uuid)"""
        ).bindparams(mid=message_id)
    ).one_or_none()
    if owner_row is None:
        raise NotFoundError(f"No charts found for message '{message_id}'")
    owner_email = str(owner_row[0]) if owner_row[0] is not None else ""
    if owner_email and owner_email != user_email:
        raise NotFoundError(f"No charts found for message '{message_id}'")

    artifacts = chart_service.list_artifacts(session, message_id)
    charts = [
        ChartArtifact(
            chart_id=str(a.get("id") or ""),
            message_id=str(a.get("message_id") or ""),
            conversation_id=str(a.get("conversation_id") or ""),
            chart_kind=str(a.get("chart_kind") or "table"),  # type: ignore[arg-type]
            title=str(a.get("title") or ""),
            columns=list(a.get("columns") or []),  # type: ignore[arg-type]
            rows=list(a.get("rows") or []),  # type: ignore[arg-type]
            option=dict(a.get("option") or {}),  # type: ignore[arg-type]
            truncated=bool(a.get("truncated") or False),
            idx=int(a.get("idx") or 0),
            created_at=a.get("created_at"),  # type: ignore[arg-type]
        )
        for a in artifacts
    ]
    return ChartListOut(message_id=str(message_id), charts=charts)


@router.get(
    "/messages/{message_id}/suggestions",
    response_model=SuggestionsOut,
    operation_id="getMessageSuggestions",
)
def get_message_suggestions(
    message_id: str,
    request: Request,
    session: LakebaseDependency,
    refresh: bool = Query(
        default=False,
        description="When true, ignore the cache and re-emit the recorded "
                    "suggestions (no LLM call). Reserved for future "
                    "regenerate-on-demand flows; today this is a no-op so "
                    "the endpoint stays idempotent for the conversation "
                    "rehydrate path.",
    ),
) -> SuggestionsOut:
    """Rehydrate the cached suggestion chips for an assistant message.

    Like the chart endpoint, this is the lazy reload path -- the
    streaming flow already emitted the chips inline via SSE. Returns
    an empty list (not 404) when the cache row exists but the model
    produced nothing, so the frontend can distinguish "feature on but
    no suggestions" from "feature off".
    """
    user_email = _resolve_user_email(request)
    _require_feature(session, user_email, "ai_suggestions")
    _ = refresh  # accepted for forward-compat; today the cache is authoritative.

    cached = suggestion_service.get_cached_with_source(session, message_id)
    if cached is None:
        return SuggestionsOut(message_id=message_id, source="fallback", suggestions=[])
    suggestions, source = cached
    safe_source: str = source if source in ("genie_native", "llm", "fallback") else "fallback"
    return SuggestionsOut(
        message_id=message_id,
        source=safe_source,  # type: ignore[arg-type]
        suggestions=suggestions,
    )
