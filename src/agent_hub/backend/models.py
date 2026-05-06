"""Pydantic request/response models for the Agent Hub API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class AgentType(str, Enum):
    """Top-level classification for a catalog entry.

    For serving-endpoint-backed entries, the type is derived primarily from the
    Agent Bricks tile metadata (``/api/2.0/tiles``) when available, falling back
    to ``ServingEndpointDetailed.task`` + ``config``. ``GENIE_SPACE`` is used
    for Genie Spaces surfaced from ``/api/2.0/genie/spaces`` as first-class
    catalog entries (no serving endpoint). ``HTTP_CONNECTION`` and
    ``MCP_ENDPOINT`` are opt-in UC-tagged catalog entries (see Phase 1 of
    the master roadmap): admins tag UC functions / connections with a
    configurable role tag and an optional kind tag, and we persist them
    under the ``uc:<full_name>`` / ``mcp:<full_name>`` endpoint-name prefixes.

    Databricks UI labels (used by the frontend):

    - ``MAS``              -> "Supervisor Agent"
    - ``AGENT``            -> "Custom Agent Endpoint"
    - ``KA``               -> "Knowledge Assistant"
    - ``MODEL``            -> "Model"
    - ``EXTERNAL``         -> "External Model"
    - ``GENIE_SPACE``      -> "Genie Space"
    - ``HTTP_CONNECTION``  -> "HTTP Connection"
    - ``MCP_ENDPOINT``     -> "MCP Endpoint"
    """

    MAS = "MAS"                          # Agent Bricks Supervisor (tile_type=MAS)
    AGENT = "AGENT"                      # Custom Agent Endpoint (agent/v1/* task, no tile)
    KA = "KA"                            # Agent Bricks Knowledge Assistant (tile_type=KA)
    MODEL = "MODEL"                      # Plain served model (LLM / embeddings)
    EXTERNAL = "EXTERNAL"                # External model (OpenAI, Anthropic, etc. via gateway)
    GENIE_SPACE = "GENIE_SPACE"          # Genie Space (not a serving endpoint)
    HTTP_CONNECTION = "HTTP_CONNECTION"  # UC-tagged function / HTTP connection (uc:<full_name>)
    MCP_ENDPOINT = "MCP_ENDPOINT"        # UC-tagged MCP server (mcp:<full_name>)


class SubComponentType(str, Enum):
    """Type of a sub-component attached to an agent."""

    GENIE_SPACE = "genie_space"
    UC_FUNCTION = "uc_function"
    KNOWLEDGE_ASSISTANT = "knowledge_assistant"
    EXTERNAL_MCP = "external_mcp"
    SERVED_MODEL = "served_model"
    VECTOR_SEARCH = "vector_search"


# Backwards-compatible alias so existing code that imports ``SubAgentType`` keeps
# working while we migrate.
SubAgentType = SubComponentType


# -- User / Auth --

class UserOut(BaseModel):
    email: str
    role: str
    display_name: str = ""


# -- Health --

class HealthLiveOut(BaseModel):
    status: str = "ok"


class HealthReadyOut(BaseModel):
    status: str = "ok"
    database: str = "ok"
    workspace: str = "ok"
    migration_status: dict[str, Any] = Field(default_factory=dict)


# -- Agents --

class AgentSummary(BaseModel):
    endpoint_name: str
    display_name: str
    description: str = ""
    agent_type: str = "MAS"
    sub_agent_count: int = 0
    has_access: bool = False
    owner_email: str = ""


class AgentListOut(BaseModel):
    agents: list[AgentSummary]


class SubAgentInfo(BaseModel):
    name: str
    type: SubAgentType
    description: str = ""
    has_access: bool = False
    owner_email: str = ""
    # Underlying endpoint / UC path / Genie space id backing this sub-agent.
    # Populated when the Agent Bricks multi-agent-supervisors detail API is
    # reachable; rendered as a mono subtitle in the UI so users can tell
    # which KA endpoint / UC function / Genie space a friendly name maps to.
    endpoint_ref: str = ""


class AgentDetailOut(BaseModel):
    endpoint_name: str
    display_name: str
    description: str = ""
    agent_type: str = "MAS"
    owner_email: str = ""
    has_access: bool = False
    sub_agents: list[SubAgentInfo] = Field(default_factory=list)


class AgentAccessOut(BaseModel):
    endpoint_name: str
    has_access: bool
    permission_level: str = ""
    sub_agent_access: dict[str, bool] = Field(default_factory=dict)


class DiscoverResult(BaseModel):
    discovered: int = 0
    new: int = 0
    updated: int = 0
    skipped: int = 0
    warnings: list[str] = Field(default_factory=list)
    agents: list[AgentSummary] = Field(default_factory=list)


# -- Genie Spaces --

class GenieSpaceSummary(BaseModel):
    space_id: str
    title: str
    description: str = ""
    warehouse_id: str = ""
    has_access: bool = True


class GenieSpaceListOut(BaseModel):
    spaces: list[GenieSpaceSummary] = Field(default_factory=list)


# -- Chat --

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    # Phase 2 (UC HTTP + MCP chat): when an MCP endpoint exposes multiple
    # tools and we couldn't pick one by convention, the backend emits a
    # ``needs_tool_choice`` SSE event. The UI then resubmits the turn
    # with ``tool_choice`` set to the user's selection; the backend
    # persists the choice for the remainder of the conversation.
    tool_choice: Optional[str] = None


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    # Phase 4 (suggestions / charts / pins): conversation reload uses these
    # hints to lazy-load the chart artifact and re-render suggestion chips
    # without a per-message round-trip on the initial paint. Both fields
    # are Optional so existing serializers continue to work for messages
    # written before the feature was enabled.
    chart_id: Optional[str] = Field(
        default=None,
        description="ID of the *first* chart_artifacts row attached to this "
                    "assistant message, when one was generated. Null for "
                    "non-Genie messages or Genie messages without a chartable "
                    "result. Kept for back-compat with clients that only "
                    "request one chart via ``GET /messages/{id}/chart``.",
    )
    chart_count: int = Field(
        default=0,
        description="Number of chart_artifacts rows attached to this message. "
                    "When greater than 1, the UI renders a stacked set; when "
                    "equal to 0, no chart fetch is issued on reload. Populated "
                    "by a COUNT against chart_artifacts during serialization.",
    )
    has_suggestions: bool = Field(
        default=False,
        description="True when suggestions_cache contains a row keyed on this "
                    "message id. Lets the frontend show the chip rail on reload "
                    "without fetching every message's cache eagerly.",
    )


class ConversationSummary(BaseModel):
    id: str
    title: str
    endpoint_name: str
    display_name: str = ""
    last_message_preview: Optional[str] = None
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class ConversationListOut(BaseModel):
    conversations: list[ConversationSummary]
    total: int = 0


class ConversationDetailOut(BaseModel):
    id: str
    title: str
    endpoint_name: str
    display_name: str = ""
    messages: list[MessageOut] = Field(default_factory=list)


class DeleteResult(BaseModel):
    deleted: bool = True
    id: str = ""


# -- Admin --

class AdminSettingsOut(BaseModel):
    settings: dict[str, Any]


class AdminSettingUpdate(BaseModel):
    value: Any


class AdminSettingOut(BaseModel):
    key: str
    value: Any
    updated_at: Optional[datetime] = None


class CatalogEntryOut(BaseModel):
    endpoint_name: str
    display_name: str = ""
    visible: bool = True
    agent_type: str = "MAS"
    sub_agent_count: int = 0
    updated_at: Optional[datetime] = None


class CatalogEntryUpdate(BaseModel):
    visible: Optional[bool] = None
    display_name: Optional[str] = None
    description: Optional[str] = None


# -- Admin: MAS/KA tile access + metadata scan --
#
# ``/admin/catalog/grant-access`` adds the app service principal to each
# MAS/KA tile's Agent Bricks ACL with ``CAN_MANAGE``, which is the only
# permission level that unblocks the ``multi-agent-supervisors`` detail
# endpoint used for metadata enrichment. ``/admin/catalog/rescan-metadata``
# then reads the detail endpoint via the SP and persists the real
# ``display_name`` / ``description`` / ``sub_agents`` into catalog_config.
# See docs/rollback-obo-gaps-2026-04-17.md §11.2 for the platform-level
# rationale (OBO lacks ``all-apis`` scope; SP needs per-tile ACL).

TileActionStatus = Literal[
    "granted",
    "already_granted",
    "unauthorized",
    "failed",
    "refreshed",
    "unchanged",
    "skipped",
]


class TileActionRow(BaseModel):
    endpoint_name: str
    tile_id: Optional[str] = None
    status: TileActionStatus
    message: str = ""


class GrantAccessResult(BaseModel):
    granted: int = 0
    already_granted: int = 0
    unauthorized: int = 0
    failed: int = 0
    skipped: int = 0
    rows: list[TileActionRow] = Field(default_factory=list)


class RescanMetadataResult(BaseModel):
    refreshed: int = 0
    unchanged: int = 0
    failed: int = 0
    skipped: int = 0
    rows: list[TileActionRow] = Field(default_factory=list)


# -- UC Tag Config (Phase 1: HTTP + MCP tagged agents) --
#
# Admins configure which Unity Catalog tag key/value pair marks a UC function
# or UC connection as an agent worth surfacing in the catalog. We store this
# in ``admin_settings`` under the ``uc_tag_config`` key (JSON) to stay
# consistent with the existing KV admin-settings pattern. A kind tag key is
# also configurable so admins can drive the ``HTTP_CONNECTION`` vs
# ``MCP_ENDPOINT`` distinction from within Unity Catalog instead of the app.

class UCTagConfig(BaseModel):
    agent_tag_key: str = Field(
        default="agent_hub_role",
        description="UC tag key that marks an object as a discoverable agent.",
    )
    agent_tag_value: str = Field(
        default="agent",
        description="Expected value of the agent-tag key. Case-insensitive match.",
    )
    agent_kind_tag_key: str = Field(
        default="agent_hub_kind",
        description="UC tag key whose value selects the chat invocation path. "
                    "Known values: 'http' -> HTTP_CONNECTION; 'mcp' -> MCP_ENDPOINT. "
                    "Unknown / missing -> HTTP_CONNECTION (safer default).",
    )


class UCTagConfigUpdate(BaseModel):
    agent_tag_key: Optional[str] = None
    agent_tag_value: Optional[str] = None
    agent_kind_tag_key: Optional[str] = None


# -- Manual UC endpoint registration (Option C fallback for Phase 1) --
#
# Unity Catalog workspaces that don't yet expose ``system.information_schema
# .function_tags`` / ``connection_tags`` can't use the tag-discovery path,
# so we give admins a manual UI to register an ``HTTP_CONNECTION`` /
# ``MCP_ENDPOINT`` directly into ``catalog_config``. The backend writes
# the same ``uc:<full_name>`` / ``mcp:<full_name>`` endpoint_name key and
# the same metadata shape as discovery, but flags the row with
# ``metadata_json.manual = true`` so we know not to overwrite or delete
# it during a subsequent tag-based rescan.

ManualUCObjectType = Literal["function", "connection"]
ManualUCKind = Literal["http", "mcp"]


class ManualUCEndpointIn(BaseModel):
    """Request payload for ``POST /admin/uc-endpoints``."""

    uc_full_name: str = Field(
        description="Fully-qualified Unity Catalog name. Functions use 3 "
                    "segments (catalog.schema.function); connections use 2 "
                    "(catalog.connection).",
    )
    object_type: ManualUCObjectType = Field(
        description="Whether uc_full_name points to a UC function or a UC "
                    "connection. Controls segment validation and the "
                    "invoke_shape metadata.",
    )
    kind: ManualUCKind = Field(
        description="Invocation path: 'http' (SQL / HTTP callout) or 'mcp' "
                    "(MCP endpoint). Controls the endpoint_name prefix.",
    )
    display_name: Optional[str] = Field(
        default=None,
        description="Human-friendly label. Defaults to a titlecased leaf name.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Free-form description shown on the catalog tile.",
    )


# -- User preferences (Phase 3 iOS redesign) --
# The UI calls GET /user/prefs on first paint and PUT /user/prefs whenever
# the user flips the theme toggle. We intentionally keep this tiny so
# future prefs (accent color, density, etc.) can be added without an API
# shape change.


ThemeMode = Literal["system", "light", "dark"]


class UserFeatureOverrides(BaseModel):
    """Per-user opt-outs for the three switchable features (Phase 4).

    Each field defaults to ``None`` meaning "honor admin default". A
    value of ``False`` is the explicit opt-out; ``True`` is honored only
    when the admin master is also on. Stored as a JSONB blob in
    ``user_prefs.feature_overrides`` -- see
    :mod:`backend.services.feature_flags_service` for resolution rules.
    """

    ai_suggestions: Optional[bool] = Field(
        default=None,
        description="Show or hide the suggestion chip rail above the input.",
    )
    charts: Optional[bool] = Field(
        default=None,
        description="Render ECharts cards above Genie SQL answers.",
    )
    pinned: Optional[bool] = Field(
        default=None,
        description="Surface the pin drawer + per-message pin action.",
    )


class UserPrefsOut(BaseModel):
    theme: ThemeMode = Field(
        default="system",
        description="Active theme preference. 'system' follows the OS, 'light' "
                    "forces the iOS light theme, 'dark' forces the warm-neutral "
                    "iOS dark theme.",
    )
    feature_overrides: UserFeatureOverrides = Field(
        default_factory=UserFeatureOverrides,
        description="Per-user opt-outs that gate the three Phase 4 features "
                    "(suggestions, charts, pins). The UI only exposes a toggle "
                    "for a feature when the admin master is currently on.",
    )
    updated_at: Optional[datetime] = None


class UserPrefsUpdate(BaseModel):
    theme: Optional[ThemeMode] = Field(
        default=None,
        description="Leave null to keep the existing preference. Only the "
                    "provided fields are updated (PATCH-like PUT).",
    )
    feature_overrides: Optional[UserFeatureOverrides] = Field(
        default=None,
        description="Optional per-user opt-outs for the Phase 4 features. "
                    "Omitted fields are left unchanged.",
    )


# -- Public app config (Phase 3 rollback) --
# ``GET /app/config`` returns flags the frontend needs *before* a user is
# authenticated (e.g. whether to hide the theme toggle). It is intentionally
# the only unauthenticated, cache-safe endpoint outside of /health/*.


class FeatureFlag(BaseModel):
    """One feature's resolved state for the current user.

    ``master_on`` reflects ``admin_settings.feature_flags.<key>.enabled``;
    ``effective_on`` is the result of the two-tier resolution (admin AND
    user-not-opted-out AND admin.default_on). The frontend uses
    ``master_on`` to decide whether to *show* the user's opt-out toggle
    at all, and ``effective_on`` to decide whether to *render* the
    feature for this user.
    """

    master_on: bool = Field(
        default=False,
        description="Admin master switch. When false, the per-user toggle is "
                    "hidden and the feature is not rendered regardless of "
                    "any user override.",
    )
    default_on: bool = Field(
        default=True,
        description="Admin default for users with no explicit override.",
    )
    effective_on: bool = Field(
        default=False,
        description="Final resolved value after applying admin master + "
                    "default + user override.",
    )


class FeatureFlags(BaseModel):
    """Resolved feature-flag state surfaced on ``/app/config``.

    The shape mirrors ``feature_flags_service.DEFAULT_FLAGS`` and is
    intentionally additive over :class:`AppConfigOut` so existing
    callers that ignore this field keep working unchanged.
    """

    ai_suggestions: FeatureFlag = Field(default_factory=FeatureFlag)
    charts: FeatureFlag = Field(default_factory=FeatureFlag)
    pinned: FeatureFlag = Field(default_factory=FeatureFlag)


class AppConfigOut(BaseModel):
    legacy_ui: bool = Field(
        default=False,
        description="When true, ThemeProvider locks the UI to the legacy "
                    "dark palette and hides the theme toggle. Triggered by "
                    "setting the AGENT_HUB_LEGACY_UI=1 environment variable on "
                    "the server (Phase 3 rollback lever).",
    )
    feature_flags: FeatureFlags = Field(
        default_factory=FeatureFlags,
        description="Resolved feature-flag state for the calling user. "
                    "Hides UI for features that are master-off and reflects "
                    "user opt-outs for features that are master-on.",
    )


# -- Phase 4: Suggestions, Charts, Pins --
#
# These models cover the new endpoints (``/pins``, ``/messages/{id}/chart``,
# ``/messages/{id}/suggestions``) and the SSE event payloads emitted
# during streaming. We keep them in models.py rather than a per-feature
# module so the codegen step has a single import surface.


ChartKind = Literal["bar", "line", "pie", "scatter", "table"]


class ChartArtifact(BaseModel):
    """A chart rendered from a Genie SQL result.

    Persisted in ``chart_artifacts`` (see ``backend.core.lakebase``) and
    re-hydrated on conversation reload via
    ``GET /messages/{message_id}/chart``. The frontend hands ``option``
    straight to ``<ReactECharts option={...} />``; the raw ``columns`` +
    ``rows`` are kept alongside so the user can switch to a tabular view
    without a second SQL round-trip.
    """

    chart_id: str
    message_id: str
    conversation_id: str
    chart_kind: ChartKind
    title: str = ""
    columns: list[dict[str, str]] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    option: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = Field(
        default=False,
        description="True when row count was capped by either Genie's "
                    "warehouse-side truncation or our admin row cap.",
    )
    idx: int = Field(
        default=0,
        description="0-based render order within the assistant message. "
                    "Genie can return multiple ``query`` attachments per "
                    "turn; the UI stacks them in ascending idx order.",
    )
    created_at: Optional[datetime] = None


class ChartListOut(BaseModel):
    """Response for ``GET /messages/{message_id}/charts``.

    Returns every chart attached to the message in stable render order
    (``idx ASC, created_at ASC``). Empty list is a valid 200 -- the
    message has no charts. 404 is only returned when the message itself
    doesn't exist or the caller doesn't own the conversation.
    """

    message_id: str
    charts: list[ChartArtifact] = Field(default_factory=list)


SuggestionSource = Literal["genie_native", "llm", "fallback"]


class Suggestion(BaseModel):
    """One suggestion chip surfaced above the chat input."""

    text: str
    label: Optional[str] = Field(
        default=None,
        description="Optional short label rendered inside the chip. "
                    "Defaults to a truncated version of ``text`` on the "
                    "frontend when missing.",
    )


class SuggestionsOut(BaseModel):
    """Response for ``GET /messages/{id}/suggestions``."""

    message_id: str
    source: SuggestionSource = "fallback"
    suggestions: list[str] = Field(default_factory=list)


class PinIn(BaseModel):
    """Request body for ``POST /pins/{endpoint_name}``."""

    text: str = Field(
        ...,
        description="The question text to pin. Whitespace is collapsed and "
                    "the unique key (user, endpoint, text) protects against "
                    "duplicate pins -- a duplicate POST returns 409.",
    )
    label: Optional[str] = Field(
        default=None,
        description="Optional short label shown in the pin chip. Falls back "
                    "to a truncated version of ``text``.",
    )
    position: Optional[int] = Field(
        default=None,
        description="Optional explicit ordering. Lower values render first. "
                    "Omit on first create -- the server appends to the end.",
    )


class PinPatch(BaseModel):
    """Request body for ``PATCH /pins/{endpoint_name}/{id}``."""

    label: Optional[str] = Field(
        default=None,
        description="Set to a non-empty string to update; null to clear; "
                    "omit to leave unchanged.",
    )
    position: Optional[int] = Field(
        default=None,
        description="New sort key. Omit to leave unchanged.",
    )


class PinOut(BaseModel):
    id: str
    user_email: str
    endpoint_name: str
    text: str
    label: Optional[str] = None
    position: int = 0
    created_at: Optional[datetime] = None


class PinListOut(BaseModel):
    pins: list[PinOut] = Field(default_factory=list)


class PinClickResult(BaseModel):
    """Response for ``POST /pins/{endpoint_name}/{pin_id}/click``.

    ``ok`` is always true on 200 -- the caller owns the pin and the
    server accepted the request. ``recorded`` reflects whether the
    telemetry row actually landed in ``pin_events``; a ``false`` here
    just means the DB write failed and we swallowed it so the UI click
    remained instant. The UI doesn't branch on this today; it's for
    log-correlation.
    """
    ok: bool = True
    recorded: bool = Field(
        default=False,
        description="True when a ``pin_events`` row was successfully written.",
    )


# -- Debug / Diagnostics --
# Surfaces what scopes are actually embedded in the forwarded OBO token vs
# what the app manifest declares. Admin-only. Never returns the raw token.

class ScopeDebugOut(BaseModel):
    ok: bool = Field(
        description="True when every declared scope is present in the forwarded token."
    )
    token_kind: Literal["jwt", "opaque", "missing"] = Field(
        description="'jwt' if we could decode the token payload; 'opaque' if it "
                    "was present but not a JWT; 'missing' if no token header arrived."
    )
    declared: list[str] = Field(
        default_factory=list,
        description="Scopes declared in the app manifest (source: ws.apps.get -> "
                    "effective_user_api_scopes, fallback user_api_scopes).",
    )
    in_token: Optional[list[str]] = Field(
        default=None,
        description="Scopes extracted from the forwarded token's `scope`/`scp` "
                    "claim. Null when token_kind != 'jwt'.",
    )
    missing_from_token: list[str] = Field(
        default_factory=list,
        description="Declared ∖ in_token. These are the scopes Databricks claims to "
                    "have granted but the forwarded token doesn't carry.",
    )
    extra_in_token: list[str] = Field(
        default_factory=list,
        description="in_token ∖ declared. Scopes the user consented to beyond what "
                    "the current manifest declares (usually leftover from older "
                    "consent versions; see F5).",
    )
    user_email: str = ""
    app_name: str = ""
    notes: list[str] = Field(
        default_factory=list,
        description="Diagnostic notes (e.g. 'app metadata lookup failed', "
                    "'token was opaque').",
    )
