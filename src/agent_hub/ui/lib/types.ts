export type AgentType =
  | "MAS"
  | "AGENT"
  | "KA"
  | "MODEL"
  | "EXTERNAL"
  | "GENIE_SPACE"
  | "HTTP_CONNECTION"
  | "MCP_ENDPOINT";

// Admin-managed UC tag configuration (Phase 1 of the master roadmap).
// Controls which UC tag key/value pair opts a Unity Catalog function or
// connection into the catalog, and which tag key decides whether it lands
// as an HTTP_CONNECTION or MCP_ENDPOINT.
export interface UCTagConfig {
  agent_tag_key: string;
  agent_tag_value: string;
  agent_kind_tag_key: string;
}

export type SubComponentType =
  | "genie_space"
  | "uc_function"
  | "knowledge_assistant"
  | "external_mcp"
  | "served_model"
  | "vector_search"
  // Legacy alias kept for backwards compatibility with older backend rows.
  | "genie";

export type SubAgentType = SubComponentType;

export interface SubAgent {
  name: string;
  type: SubComponentType;
  description?: string;
  has_access?: boolean;
  owner_email?: string;
  // Backing endpoint / UC path / Genie space id so the UI can render a
  // mono subtitle (e.g. ``ka-ee893c47-endpoint``,
  // ``aan_demo_workspace_catalog.gold.search_thailand_news``).
  endpoint_ref?: string;
}

export interface Agent {
  endpoint_name: string;
  display_name: string;
  description?: string;
  agent_type?: AgentType | string;
  sub_agent_count?: number;
  has_access?: boolean;
  owner_email?: string;
}

export interface AgentDetail {
  endpoint_name: string;
  display_name: string;
  description?: string;
  agent_type?: AgentType | string;
  owner_email?: string;
  has_access?: boolean;
  sub_agents?: SubAgent[];
}

export interface AgentAccess {
  endpoint_name: string;
  has_access: boolean;
  permission_level: string;
  sub_agent_access: Record<string, boolean>;
}

export interface AgentListResponse {
  agents: Agent[];
}

export interface DiscoverResult {
  discovered?: number;
  new?: number;
  updated?: number;
  skipped?: number;
  warnings?: string[];
  agents?: Agent[];
}

// -- Chat --

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  metadata?: Record<string, unknown>;
  created_at: string;
  // Phase 4 (suggestions / charts / pins). The backend embeds these on
  // the conversation reload payload so the UI can lazy-load the chart
  // artifact and re-render suggestion chips without a per-message
  // round-trip on the initial paint.
  //
  // ``chart_id`` is the *primary* (idx=0) artifact's id and kept for
  // back-compat with the single-chart hydrator path. ``chart_count`` is
  // the total number of charts attached -- when it is greater than 1,
  // the UI calls ``GET /messages/{id}/charts`` for the full stacked set.
  chart_id?: string | null;
  chart_count?: number;
  has_suggestions?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  endpoint_name: string;
  display_name?: string;
  last_message_preview?: string | null;
  message_count?: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail {
  id: string;
  title: string;
  endpoint_name: string;
  display_name?: string;
  messages?: Message[];
}

export interface ConversationListResponse {
  conversations: Conversation[];
  total?: number;
}

// Phase 2 (UC HTTP + MCP chat) adds ``tool_call``, ``tool_result``, and
// ``needs_tool_choice`` event types so the UI can render MCP tool-call
// timelines and a picker when the server exposes multiple tools.
//
// Phase 4 (charts + suggestions) adds ``chart`` and ``suggestions``: the
// backend emits one ``chart`` *before* token streaming starts so the
// ECharts card renders above the assistant's textual answer, and one
// ``suggestions`` event right before ``done`` so chips show up under
// the input the moment the stream finishes.
export type SSEEventType =
  | "started"
  | "token"
  | "done"
  | "error"
  | "tool_call"
  | "tool_result"
  | "needs_tool_choice"
  | "chart"
  | "suggestions";

export interface McpToolDescriptor {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

export interface SSEEvent {
  type?: SSEEventType;
  token?: string;
  done?: boolean;
  conversation_id?: string;
  error?: string;
  // MCP tool-call events
  name?: string;
  input?: Record<string, unknown>;
  is_error?: boolean;
  // needs_tool_choice events
  tools?: McpToolDescriptor[];
  // Phase 4: chart + suggestions stream payloads. The backend keys both
  // off ``message_id`` because the assistant message is persisted up
  // front (placeholder content) so the chart card / chips can attach
  // to it the instant they arrive, even mid-stream.
  message_id?: string;
  chart_id?: string;
  chart_kind?: ChartKind;
  title?: string;
  option?: Record<string, unknown>;
  truncated?: boolean;
  // Multi-chart stream index (0-based) and total chart count for the
  // current assistant turn. Set on every ``chart`` event so the UI can
  // slot artifacts into a stable order even when they arrive out of
  // sync (rare but possible on slow Genie attachments).
  index?: number;
  total?: number;
  suggestions?: string[];
  source?: SuggestionSource;
}

// -- Phase 4: Charts, Suggestions, Pins, Feature flags --

export type ChartKind = "bar" | "line" | "pie" | "scatter" | "table";

export interface ChartArtifact {
  chart_id: string;
  message_id: string;
  conversation_id: string;
  chart_kind: ChartKind;
  title: string;
  // Pre-built ECharts option dict. We treat it as opaque on the FE -- the
  // backend already wired up tooltip / dataZoom / legend / toolbox / brush
  // so the wrapper just hands it to ``<ReactECharts option={...}/>``.
  option: Record<string, unknown>;
  columns: string[];
  rows: Array<Array<string | number | boolean | null>>;
  truncated: boolean;
  // 0-based render order within the owning assistant message. Genie can
  // return multiple ``query`` attachments per turn; the UI sorts by
  // ``idx`` ascending when stacking cards.
  idx?: number;
  created_at?: string;
}

export type SuggestionSource = "genie_native" | "llm" | "fallback";

export interface SuggestionsPayload {
  message_id: string;
  source: SuggestionSource;
  suggestions: string[];
}

export interface PinIn {
  text: string;
  label?: string | null;
  position?: number;
}

export interface PinPatch {
  label?: string | null;
  position?: number;
}

export interface PinOut {
  id: string;
  user_email: string;
  endpoint_name: string;
  text: string;
  label?: string | null;
  position: number;
  created_at?: string;
}

export interface PinListOut {
  pins: PinOut[];
}

// Three-state per-feature flag returned on /app/config. ``master_on`` is
// the admin kill-switch (any UI for the feature stays hidden when this
// is false), ``default_on`` is the admin's default for new users, and
// ``effective_on`` is what the UI should actually render against -- it
// already folds in the per-user override.
export interface FeatureFlag {
  master_on: boolean;
  default_on: boolean;
  effective_on: boolean;
}

export interface FeatureFlags {
  ai_suggestions: FeatureFlag;
  charts: FeatureFlag;
  pinned: FeatureFlag;
}

export interface AppConfigOut {
  legacy_ui: boolean;
  feature_flags: FeatureFlags;
}

export type ThemeMode = "system" | "light" | "dark";

export interface UserFeatureOverrides {
  ai_suggestions?: boolean | null;
  charts?: boolean | null;
  pinned?: boolean | null;
}

export interface UserPrefsOut {
  theme: ThemeMode;
  feature_overrides: UserFeatureOverrides;
  updated_at?: string | null;
}

export interface UserPrefsUpdate {
  theme?: ThemeMode;
  feature_overrides?: UserFeatureOverrides;
}

// Timeline event rendered in the chat transcript alongside regular
// messages. Keeps tool invocations visible after the stream completes
// so the user can scroll back and see which tool answered.
export type ChatTimelineEvent =
  | {
      id: string;
      kind: "tool_call";
      name: string;
      input: Record<string, unknown>;
      created_at: string;
    }
  | {
      id: string;
      kind: "tool_result";
      name: string;
      is_error: boolean;
      created_at: string;
    };

export interface DeleteResult {
  deleted: boolean;
  id: string;
}

// -- Genie Spaces --

export interface GenieSpace {
  space_id: string;
  title: string;
  description?: string;
  warehouse_id?: string;
  has_access?: boolean;
}

export interface GenieSpaceListResponse {
  spaces: GenieSpace[];
}
