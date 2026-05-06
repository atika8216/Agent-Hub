import { useQuery, useSuspenseQuery, useMutation } from "@tanstack/react-query";
import type { UseQueryOptions, UseSuspenseQueryOptions, UseMutationOptions } from "@tanstack/react-query";
export class ApiError extends Error {
    status: number;
    statusText: string;
    body: unknown;
    constructor(status: number, statusText: string, body: unknown){
        super(`HTTP ${status}: ${statusText}`);
        this.name = "ApiError";
        this.status = status;
        this.statusText = statusText;
        this.body = body;
    }
}
export interface AdminSettingOut {
    key: string;
    updated_at?: string | null;
    value: unknown;
}
export interface AdminSettingUpdate {
    value: unknown;
}
export interface AdminSettingsOut {
    settings: Record<string, unknown>;
}
export interface AgentAccessOut {
    endpoint_name: string;
    has_access: boolean;
    permission_level?: string;
    sub_agent_access?: Record<string, boolean>;
}
export interface AgentDetailOut {
    agent_type?: string;
    description?: string;
    display_name: string;
    endpoint_name: string;
    has_access?: boolean;
    owner_email?: string;
    sub_agents?: SubAgentInfo[];
}
export interface AgentListOut {
    agents: AgentSummary[];
}
export interface AgentSummary {
    agent_type?: string;
    description?: string;
    display_name: string;
    endpoint_name: string;
    has_access?: boolean;
    owner_email?: string;
    sub_agent_count?: number;
}
export interface CatalogEntryOut {
    agent_type?: string;
    display_name?: string;
    endpoint_name: string;
    sub_agent_count?: number;
    updated_at?: string | null;
    visible?: boolean;
}
export interface CatalogEntryUpdate {
    description?: string | null;
    display_name?: string | null;
    visible?: boolean | null;
}
export interface ChatRequest {
    conversation_id?: string | null;
    message: string;
}
export interface ConversationDetailOut {
    display_name?: string;
    endpoint_name: string;
    id: string;
    messages?: MessageOut[];
    title: string;
}
export interface ConversationListOut {
    conversations: ConversationSummary[];
    total?: number;
}
export interface ConversationSummary {
    created_at: string;
    display_name?: string;
    endpoint_name: string;
    id: string;
    last_message_preview?: string | null;
    message_count?: number;
    title: string;
    updated_at: string;
}
export interface DeleteResult {
    deleted?: boolean;
    id?: string;
}
export interface DiscoverResult {
    agents?: AgentSummary[];
    discovered?: number;
    new?: number;
    skipped?: number;
    updated?: number;
    warnings?: string[];
}
export interface GenieSpaceListOut {
    spaces?: GenieSpaceSummary[];
}
export interface GenieSpaceSummary {
    description?: string;
    has_access?: boolean;
    space_id: string;
    title: string;
    warehouse_id?: string;
}
export interface HTTPValidationError {
    detail?: ValidationError[];
}
export interface HealthLiveOut {
    status?: string;
}
export interface HealthReadyOut {
    database?: string;
    migration_status?: Record<string, unknown>;
    status?: string;
    workspace?: string;
}
export interface MessageOut {
    content: string;
    created_at: string;
    id: string;
    metadata?: Record<string, unknown>;
    role: string;
    // Phase 4: surfaced on the conversation reload payload so the UI
    // can lazy-load the chart card / suggestion chips per-message.
    chart_id?: string | null;
    has_suggestions?: boolean;
}
export interface ScopeDebugOut {
    app_name?: string;
    declared?: string[];
    extra_in_token?: string[];
    in_token?: string[] | null;
    missing_from_token?: string[];
    notes?: string[];
    ok: boolean;
    token_kind: "jwt" | "opaque" | "missing";
    user_email?: string;
}
export interface SubAgentInfo {
    description?: string;
    has_access?: boolean;
    name: string;
    owner_email?: string;
    type: SubComponentType;
}
export const SubComponentType = {
    genie_space: "genie_space",
    uc_function: "uc_function",
    knowledge_assistant: "knowledge_assistant",
    external_mcp: "external_mcp",
    served_model: "served_model",
    vector_search: "vector_search"
} as const;
export type SubComponentType = typeof SubComponentType[keyof typeof SubComponentType];
export interface UserOut {
    display_name?: string;
    email: string;
    role: string;
}
export interface ValidationError {
    ctx?: Record<string, unknown>;
    input?: unknown;
    loc: (string | number)[];
    msg: string;
    type: string;
}
export const listAdminCatalog = async (options?: RequestInit): Promise<{
    data: CatalogEntryOut[];
}> =>{
    const res = await fetch("/api/v1/admin/catalog", {
        ...options,
        method: "GET"
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export const listAdminCatalogKey = ()=>{
    return [
        "/api/v1/admin/catalog"
    ] as const;
};
export function useListAdminCatalog<TData = {
    data: CatalogEntryOut[];
}>(options?: {
    query?: Omit<UseQueryOptions<{
        data: CatalogEntryOut[];
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: listAdminCatalogKey(),
        queryFn: ()=>listAdminCatalog(),
        ...options?.query
    });
}
export function useListAdminCatalogSuspense<TData = {
    data: CatalogEntryOut[];
}>(options?: {
    query?: Omit<UseSuspenseQueryOptions<{
        data: CatalogEntryOut[];
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useSuspenseQuery({
        queryKey: listAdminCatalogKey(),
        queryFn: ()=>listAdminCatalog(),
        ...options?.query
    });
}
export interface ReclassifyCatalogParams {
    "X-Forwarded-Host"?: string | null;
    "X-Forwarded-Preferred-Username"?: string | null;
    "X-Forwarded-User"?: string | null;
    "X-Forwarded-Email"?: string | null;
    "X-Request-Id"?: string | null;
    "X-Forwarded-Access-Token"?: string | null;
}
export const reclassifyCatalog = async (params?: ReclassifyCatalogParams, options?: RequestInit): Promise<{
    data: DiscoverResult;
}> =>{
    const res = await fetch("/api/v1/admin/catalog/reclassify", {
        ...options,
        method: "POST",
        headers: {
            ...(params?.["X-Forwarded-Host"] != null && {
                "X-Forwarded-Host": params["X-Forwarded-Host"]
            }),
            ...(params?.["X-Forwarded-Preferred-Username"] != null && {
                "X-Forwarded-Preferred-Username": params["X-Forwarded-Preferred-Username"]
            }),
            ...(params?.["X-Forwarded-User"] != null && {
                "X-Forwarded-User": params["X-Forwarded-User"]
            }),
            ...(params?.["X-Forwarded-Email"] != null && {
                "X-Forwarded-Email": params["X-Forwarded-Email"]
            }),
            ...(params?.["X-Request-Id"] != null && {
                "X-Request-Id": params["X-Request-Id"]
            }),
            ...(params?.["X-Forwarded-Access-Token"] != null && {
                "X-Forwarded-Access-Token": params["X-Forwarded-Access-Token"]
            }),
            ...options?.headers
        }
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export function useReclassifyCatalog(options?: {
    mutation?: UseMutationOptions<{
        data: DiscoverResult;
    }, ApiError, {
        params: ReclassifyCatalogParams;
    }>;
}) {
    return useMutation({
        mutationFn: (vars)=>reclassifyCatalog(vars.params),
        ...options?.mutation
    });
}
// Admin tile-ACL grant + metadata rescan.
// See `docs/rollback-obo-gaps-2026-04-17.md` §11.2 for why these two
// actions are separate: Grant runs under OBO (admin must already have
// CAN_MANAGE on each tile), Rescan runs under the app SP because the
// Agent Bricks detail endpoint requires `all-apis` which OBO can't carry.
export type TileActionStatus =
    | "granted"
    | "already_granted"
    | "unauthorized"
    | "failed"
    | "refreshed"
    | "unchanged"
    | "skipped";
export interface TileActionRow {
    endpoint_name: string;
    tile_id?: string | null;
    status: TileActionStatus;
    message?: string;
}
export interface GrantAccessResult {
    granted?: number;
    already_granted?: number;
    unauthorized?: number;
    failed?: number;
    skipped?: number;
    rows?: TileActionRow[];
}
export interface RescanMetadataResult {
    refreshed?: number;
    unchanged?: number;
    failed?: number;
    skipped?: number;
    rows?: TileActionRow[];
}
export const grantCatalogAccess = async (options?: RequestInit): Promise<{
    data: GrantAccessResult;
}> => {
    const res = await fetch("/api/v1/admin/catalog/grant-access", {
        ...options,
        method: "POST"
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export function useGrantCatalogAccess(options?: {
    mutation?: UseMutationOptions<{
        data: GrantAccessResult;
    }, ApiError, void>;
}) {
    return useMutation({
        mutationFn: () => grantCatalogAccess(),
        ...options?.mutation
    });
}
export const rescanCatalogMetadata = async (options?: RequestInit): Promise<{
    data: RescanMetadataResult;
}> => {
    const res = await fetch("/api/v1/admin/catalog/rescan-metadata", {
        ...options,
        method: "POST"
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export function useRescanCatalogMetadata(options?: {
    mutation?: UseMutationOptions<{
        data: RescanMetadataResult;
    }, ApiError, void>;
}) {
    return useMutation({
        mutationFn: () => rescanCatalogMetadata(),
        ...options?.mutation
    });
}
export interface UpdateAdminCatalogEntryParams {
    endpoint_name: string;
}
export const updateAdminCatalogEntry = async (params: UpdateAdminCatalogEntryParams, data: CatalogEntryUpdate, options?: RequestInit): Promise<{
    data: CatalogEntryOut;
}> =>{
    const res = await fetch(`/api/v1/admin/catalog/${params.endpoint_name}`, {
        ...options,
        method: "PUT",
        headers: {
            "Content-Type": "application/json",
            ...options?.headers
        },
        body: JSON.stringify(data)
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export function useUpdateAdminCatalogEntry(options?: {
    mutation?: UseMutationOptions<{
        data: CatalogEntryOut;
    }, ApiError, {
        params: UpdateAdminCatalogEntryParams;
        data: CatalogEntryUpdate;
    }>;
}) {
    return useMutation({
        mutationFn: (vars)=>updateAdminCatalogEntry(vars.params, vars.data),
        ...options?.mutation
    });
}
export const getAdminSettings = async (options?: RequestInit): Promise<{
    data: AdminSettingsOut;
}> =>{
    const res = await fetch("/api/v1/admin/settings", {
        ...options,
        method: "GET"
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export const getAdminSettingsKey = ()=>{
    return [
        "/api/v1/admin/settings"
    ] as const;
};
export function useGetAdminSettings<TData = {
    data: AdminSettingsOut;
}>(options?: {
    query?: Omit<UseQueryOptions<{
        data: AdminSettingsOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: getAdminSettingsKey(),
        queryFn: ()=>getAdminSettings(),
        ...options?.query
    });
}
export function useGetAdminSettingsSuspense<TData = {
    data: AdminSettingsOut;
}>(options?: {
    query?: Omit<UseSuspenseQueryOptions<{
        data: AdminSettingsOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useSuspenseQuery({
        queryKey: getAdminSettingsKey(),
        queryFn: ()=>getAdminSettings(),
        ...options?.query
    });
}
export interface UpdateAdminSettingParams {
    key: string;
}
export const updateAdminSetting = async (params: UpdateAdminSettingParams, data: AdminSettingUpdate, options?: RequestInit): Promise<{
    data: AdminSettingOut;
}> =>{
    const res = await fetch(`/api/v1/admin/settings/${params.key}`, {
        ...options,
        method: "PUT",
        headers: {
            "Content-Type": "application/json",
            ...options?.headers
        },
        body: JSON.stringify(data)
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export function useUpdateAdminSetting(options?: {
    mutation?: UseMutationOptions<{
        data: AdminSettingOut;
    }, ApiError, {
        params: UpdateAdminSettingParams;
        data: AdminSettingUpdate;
    }>;
}) {
    return useMutation({
        mutationFn: (vars)=>updateAdminSetting(vars.params, vars.data),
        ...options?.mutation
    });
}
// Phase 1 — UC tag config (controls which UC tag key/value pair opts a
// Unity Catalog function or connection into the agent catalog).
export interface UCTagConfig {
    agent_tag_key: string;
    agent_tag_value: string;
    agent_kind_tag_key: string;
}
export interface UCTagConfigUpdate {
    agent_tag_key?: string | null;
    agent_tag_value?: string | null;
    agent_kind_tag_key?: string | null;
}
export const getUCTagConfig = async (options?: RequestInit): Promise<{
    data: UCTagConfig;
}> => {
    const res = await fetch("/api/v1/admin/tag-config", {
        ...options,
        method: "GET"
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export const getUCTagConfigKey = () => {
    return [
        "/api/v1/admin/tag-config"
    ] as const;
};
export function useGetUCTagConfig<TData = {
    data: UCTagConfig;
}>(options?: {
    query?: Omit<UseQueryOptions<{
        data: UCTagConfig;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: getUCTagConfigKey(),
        queryFn: () => getUCTagConfig(),
        ...options?.query
    });
}
export const updateUCTagConfig = async (data: UCTagConfigUpdate, options?: RequestInit): Promise<{
    data: UCTagConfig;
}> => {
    const res = await fetch("/api/v1/admin/tag-config", {
        ...options,
        method: "PUT",
        headers: {
            "Content-Type": "application/json",
            ...options?.headers
        },
        body: JSON.stringify(data)
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export function useUpdateUCTagConfig(options?: {
    mutation?: UseMutationOptions<{
        data: UCTagConfig;
    }, ApiError, UCTagConfigUpdate>;
}) {
    return useMutation({
        mutationFn: (data) => updateUCTagConfig(data),
        ...options?.mutation
    });
}
export interface ListAgentsParams {
    search?: string | null;
    type?: string | null;
    "X-Forwarded-Host"?: string | null;
    "X-Forwarded-Preferred-Username"?: string | null;
    "X-Forwarded-User"?: string | null;
    "X-Forwarded-Email"?: string | null;
    "X-Request-Id"?: string | null;
    "X-Forwarded-Access-Token"?: string | null;
}
export const listAgents = async (params?: ListAgentsParams, options?: RequestInit): Promise<{
    data: AgentListOut;
}> =>{
    const searchParams = new URLSearchParams();
    if (params?.search != null) searchParams.set("search", String(params?.search));
    if (params?.type != null) searchParams.set("type", String(params?.type));
    const queryString = searchParams.toString();
    const url = queryString ? `/api/v1/agents?${queryString}` : "/api/v1/agents";
    const res = await fetch(url, {
        ...options,
        method: "GET",
        headers: {
            ...(params?.["X-Forwarded-Host"] != null && {
                "X-Forwarded-Host": params["X-Forwarded-Host"]
            }),
            ...(params?.["X-Forwarded-Preferred-Username"] != null && {
                "X-Forwarded-Preferred-Username": params["X-Forwarded-Preferred-Username"]
            }),
            ...(params?.["X-Forwarded-User"] != null && {
                "X-Forwarded-User": params["X-Forwarded-User"]
            }),
            ...(params?.["X-Forwarded-Email"] != null && {
                "X-Forwarded-Email": params["X-Forwarded-Email"]
            }),
            ...(params?.["X-Request-Id"] != null && {
                "X-Request-Id": params["X-Request-Id"]
            }),
            ...(params?.["X-Forwarded-Access-Token"] != null && {
                "X-Forwarded-Access-Token": params["X-Forwarded-Access-Token"]
            }),
            ...options?.headers
        }
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export const listAgentsKey = (params?: ListAgentsParams)=>{
    return [
        "/api/v1/agents",
        params
    ] as const;
};
export function useListAgents<TData = {
    data: AgentListOut;
}>(options?: {
    params?: ListAgentsParams;
    query?: Omit<UseQueryOptions<{
        data: AgentListOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: listAgentsKey(options?.params),
        queryFn: ()=>listAgents(options?.params),
        ...options?.query
    });
}
export function useListAgentsSuspense<TData = {
    data: AgentListOut;
}>(options?: {
    params?: ListAgentsParams;
    query?: Omit<UseSuspenseQueryOptions<{
        data: AgentListOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useSuspenseQuery({
        queryKey: listAgentsKey(options?.params),
        queryFn: ()=>listAgents(options?.params),
        ...options?.query
    });
}
export interface DiscoverAgentsParams {
    "X-Forwarded-Host"?: string | null;
    "X-Forwarded-Preferred-Username"?: string | null;
    "X-Forwarded-User"?: string | null;
    "X-Forwarded-Email"?: string | null;
    "X-Request-Id"?: string | null;
    "X-Forwarded-Access-Token"?: string | null;
}
export const discoverAgents = async (params?: DiscoverAgentsParams, options?: RequestInit): Promise<{
    data: DiscoverResult;
}> =>{
    const res = await fetch("/api/v1/agents/discover", {
        ...options,
        method: "POST",
        headers: {
            ...(params?.["X-Forwarded-Host"] != null && {
                "X-Forwarded-Host": params["X-Forwarded-Host"]
            }),
            ...(params?.["X-Forwarded-Preferred-Username"] != null && {
                "X-Forwarded-Preferred-Username": params["X-Forwarded-Preferred-Username"]
            }),
            ...(params?.["X-Forwarded-User"] != null && {
                "X-Forwarded-User": params["X-Forwarded-User"]
            }),
            ...(params?.["X-Forwarded-Email"] != null && {
                "X-Forwarded-Email": params["X-Forwarded-Email"]
            }),
            ...(params?.["X-Request-Id"] != null && {
                "X-Request-Id": params["X-Request-Id"]
            }),
            ...(params?.["X-Forwarded-Access-Token"] != null && {
                "X-Forwarded-Access-Token": params["X-Forwarded-Access-Token"]
            }),
            ...options?.headers
        }
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export function useDiscoverAgents(options?: {
    mutation?: UseMutationOptions<{
        data: DiscoverResult;
    }, ApiError, {
        params: DiscoverAgentsParams;
    }>;
}) {
    return useMutation({
        mutationFn: (vars)=>discoverAgents(vars.params),
        ...options?.mutation
    });
}
export interface GetAgentParams {
    endpoint_name: string;
    "X-Forwarded-Host"?: string | null;
    "X-Forwarded-Preferred-Username"?: string | null;
    "X-Forwarded-User"?: string | null;
    "X-Forwarded-Email"?: string | null;
    "X-Request-Id"?: string | null;
    "X-Forwarded-Access-Token"?: string | null;
}
export const getAgent = async (params: GetAgentParams, options?: RequestInit): Promise<{
    data: AgentDetailOut;
}> =>{
    const res = await fetch(`/api/v1/agents/${params.endpoint_name}`, {
        ...options,
        method: "GET",
        headers: {
            ...(params?.["X-Forwarded-Host"] != null && {
                "X-Forwarded-Host": params["X-Forwarded-Host"]
            }),
            ...(params?.["X-Forwarded-Preferred-Username"] != null && {
                "X-Forwarded-Preferred-Username": params["X-Forwarded-Preferred-Username"]
            }),
            ...(params?.["X-Forwarded-User"] != null && {
                "X-Forwarded-User": params["X-Forwarded-User"]
            }),
            ...(params?.["X-Forwarded-Email"] != null && {
                "X-Forwarded-Email": params["X-Forwarded-Email"]
            }),
            ...(params?.["X-Request-Id"] != null && {
                "X-Request-Id": params["X-Request-Id"]
            }),
            ...(params?.["X-Forwarded-Access-Token"] != null && {
                "X-Forwarded-Access-Token": params["X-Forwarded-Access-Token"]
            }),
            ...options?.headers
        }
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export const getAgentKey = (params?: GetAgentParams)=>{
    return [
        "/api/v1/agents/{endpoint_name}",
        params
    ] as const;
};
export function useGetAgent<TData = {
    data: AgentDetailOut;
}>(options: {
    params: GetAgentParams;
    query?: Omit<UseQueryOptions<{
        data: AgentDetailOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: getAgentKey(options.params),
        queryFn: ()=>getAgent(options.params),
        ...options?.query
    });
}
export function useGetAgentSuspense<TData = {
    data: AgentDetailOut;
}>(options: {
    params: GetAgentParams;
    query?: Omit<UseSuspenseQueryOptions<{
        data: AgentDetailOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useSuspenseQuery({
        queryKey: getAgentKey(options.params),
        queryFn: ()=>getAgent(options.params),
        ...options?.query
    });
}
export interface CheckAgentAccessParams {
    endpoint_name: string;
    "X-Forwarded-Host"?: string | null;
    "X-Forwarded-Preferred-Username"?: string | null;
    "X-Forwarded-User"?: string | null;
    "X-Forwarded-Email"?: string | null;
    "X-Request-Id"?: string | null;
    "X-Forwarded-Access-Token"?: string | null;
}
export const checkAgentAccess = async (params: CheckAgentAccessParams, options?: RequestInit): Promise<{
    data: AgentAccessOut;
}> =>{
    const res = await fetch(`/api/v1/agents/${params.endpoint_name}/access`, {
        ...options,
        method: "GET",
        headers: {
            ...(params?.["X-Forwarded-Host"] != null && {
                "X-Forwarded-Host": params["X-Forwarded-Host"]
            }),
            ...(params?.["X-Forwarded-Preferred-Username"] != null && {
                "X-Forwarded-Preferred-Username": params["X-Forwarded-Preferred-Username"]
            }),
            ...(params?.["X-Forwarded-User"] != null && {
                "X-Forwarded-User": params["X-Forwarded-User"]
            }),
            ...(params?.["X-Forwarded-Email"] != null && {
                "X-Forwarded-Email": params["X-Forwarded-Email"]
            }),
            ...(params?.["X-Request-Id"] != null && {
                "X-Request-Id": params["X-Request-Id"]
            }),
            ...(params?.["X-Forwarded-Access-Token"] != null && {
                "X-Forwarded-Access-Token": params["X-Forwarded-Access-Token"]
            }),
            ...options?.headers
        }
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export const checkAgentAccessKey = (params?: CheckAgentAccessParams)=>{
    return [
        "/api/v1/agents/{endpoint_name}/access",
        params
    ] as const;
};
export function useCheckAgentAccess<TData = {
    data: AgentAccessOut;
}>(options: {
    params: CheckAgentAccessParams;
    query?: Omit<UseQueryOptions<{
        data: AgentAccessOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: checkAgentAccessKey(options.params),
        queryFn: ()=>checkAgentAccess(options.params),
        ...options?.query
    });
}
export function useCheckAgentAccessSuspense<TData = {
    data: AgentAccessOut;
}>(options: {
    params: CheckAgentAccessParams;
    query?: Omit<UseSuspenseQueryOptions<{
        data: AgentAccessOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useSuspenseQuery({
        queryKey: checkAgentAccessKey(options.params),
        queryFn: ()=>checkAgentAccess(options.params),
        ...options?.query
    });
}
export interface ListGenieSpacesParams {
    "X-Forwarded-Host"?: string | null;
    "X-Forwarded-Preferred-Username"?: string | null;
    "X-Forwarded-User"?: string | null;
    "X-Forwarded-Email"?: string | null;
    "X-Request-Id"?: string | null;
    "X-Forwarded-Access-Token"?: string | null;
}
export const listGenieSpaces = async (params?: ListGenieSpacesParams, options?: RequestInit): Promise<{
    data: GenieSpaceListOut;
}> =>{
    const res = await fetch("/api/v1/catalog/genie-spaces", {
        ...options,
        method: "GET",
        headers: {
            ...(params?.["X-Forwarded-Host"] != null && {
                "X-Forwarded-Host": params["X-Forwarded-Host"]
            }),
            ...(params?.["X-Forwarded-Preferred-Username"] != null && {
                "X-Forwarded-Preferred-Username": params["X-Forwarded-Preferred-Username"]
            }),
            ...(params?.["X-Forwarded-User"] != null && {
                "X-Forwarded-User": params["X-Forwarded-User"]
            }),
            ...(params?.["X-Forwarded-Email"] != null && {
                "X-Forwarded-Email": params["X-Forwarded-Email"]
            }),
            ...(params?.["X-Request-Id"] != null && {
                "X-Request-Id": params["X-Request-Id"]
            }),
            ...(params?.["X-Forwarded-Access-Token"] != null && {
                "X-Forwarded-Access-Token": params["X-Forwarded-Access-Token"]
            }),
            ...options?.headers
        }
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export const listGenieSpacesKey = (params?: ListGenieSpacesParams)=>{
    return [
        "/api/v1/catalog/genie-spaces",
        params
    ] as const;
};
export function useListGenieSpaces<TData = {
    data: GenieSpaceListOut;
}>(options?: {
    params?: ListGenieSpacesParams;
    query?: Omit<UseQueryOptions<{
        data: GenieSpaceListOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: listGenieSpacesKey(options?.params),
        queryFn: ()=>listGenieSpaces(options?.params),
        ...options?.query
    });
}
export function useListGenieSpacesSuspense<TData = {
    data: GenieSpaceListOut;
}>(options?: {
    params?: ListGenieSpacesParams;
    query?: Omit<UseSuspenseQueryOptions<{
        data: GenieSpaceListOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useSuspenseQuery({
        queryKey: listGenieSpacesKey(options?.params),
        queryFn: ()=>listGenieSpaces(options?.params),
        ...options?.query
    });
}
// NOTE: The generated `chatStream` / `useChatStream` helpers were removed
// because they used `await res.json()` which cannot parse the SSE response
// from `POST /api/v1/chat/{endpoint_name}`. The real streaming client lives
// in `src/agent_hub/ui/hooks/use-chat.ts` (fetch + response.body
// ReadableStream reader). If apx codegen re-adds these stubs, delete them
// again or wrap them around the `use-chat` hook instead.
export const listConversations = async (options?: RequestInit): Promise<{
    data: ConversationListOut;
}> =>{
    const res = await fetch("/api/v1/conversations", {
        ...options,
        method: "GET"
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export const listConversationsKey = ()=>{
    return [
        "/api/v1/conversations"
    ] as const;
};
export function useListConversations<TData = {
    data: ConversationListOut;
}>(options?: {
    query?: Omit<UseQueryOptions<{
        data: ConversationListOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: listConversationsKey(),
        queryFn: ()=>listConversations(),
        ...options?.query
    });
}
export function useListConversationsSuspense<TData = {
    data: ConversationListOut;
}>(options?: {
    query?: Omit<UseSuspenseQueryOptions<{
        data: ConversationListOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useSuspenseQuery({
        queryKey: listConversationsKey(),
        queryFn: ()=>listConversations(),
        ...options?.query
    });
}
export interface GetConversationParams {
    conversation_id: string;
}
export const getConversation = async (params: GetConversationParams, options?: RequestInit): Promise<{
    data: ConversationDetailOut;
}> =>{
    const res = await fetch(`/api/v1/conversations/${params.conversation_id}`, {
        ...options,
        method: "GET"
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export const getConversationKey = (params?: GetConversationParams)=>{
    return [
        "/api/v1/conversations/{conversation_id}",
        params
    ] as const;
};
export function useGetConversation<TData = {
    data: ConversationDetailOut;
}>(options: {
    params: GetConversationParams;
    query?: Omit<UseQueryOptions<{
        data: ConversationDetailOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: getConversationKey(options.params),
        queryFn: ()=>getConversation(options.params),
        ...options?.query
    });
}
export function useGetConversationSuspense<TData = {
    data: ConversationDetailOut;
}>(options: {
    params: GetConversationParams;
    query?: Omit<UseSuspenseQueryOptions<{
        data: ConversationDetailOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useSuspenseQuery({
        queryKey: getConversationKey(options.params),
        queryFn: ()=>getConversation(options.params),
        ...options?.query
    });
}
export interface DeleteConversationParams {
    conversation_id: string;
}
export const deleteConversation = async (params: DeleteConversationParams, options?: RequestInit): Promise<{
    data: DeleteResult;
}> =>{
    const res = await fetch(`/api/v1/conversations/${params.conversation_id}`, {
        ...options,
        method: "DELETE"
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export function useDeleteConversation(options?: {
    mutation?: UseMutationOptions<{
        data: DeleteResult;
    }, ApiError, {
        params: DeleteConversationParams;
    }>;
}) {
    return useMutation({
        mutationFn: (vars)=>deleteConversation(vars.params),
        ...options?.mutation
    });
}
export interface DebugMyScopesParams {
    "X-Forwarded-Host"?: string | null;
    "X-Forwarded-Preferred-Username"?: string | null;
    "X-Forwarded-User"?: string | null;
    "X-Forwarded-Email"?: string | null;
    "X-Request-Id"?: string | null;
    "X-Forwarded-Access-Token"?: string | null;
}
export const debugMyScopes = async (params?: DebugMyScopesParams, options?: RequestInit): Promise<{
    data: ScopeDebugOut;
}> =>{
    const res = await fetch("/api/v1/debug/me/scopes", {
        ...options,
        method: "GET",
        headers: {
            ...(params?.["X-Forwarded-Host"] != null && {
                "X-Forwarded-Host": params["X-Forwarded-Host"]
            }),
            ...(params?.["X-Forwarded-Preferred-Username"] != null && {
                "X-Forwarded-Preferred-Username": params["X-Forwarded-Preferred-Username"]
            }),
            ...(params?.["X-Forwarded-User"] != null && {
                "X-Forwarded-User": params["X-Forwarded-User"]
            }),
            ...(params?.["X-Forwarded-Email"] != null && {
                "X-Forwarded-Email": params["X-Forwarded-Email"]
            }),
            ...(params?.["X-Request-Id"] != null && {
                "X-Request-Id": params["X-Request-Id"]
            }),
            ...(params?.["X-Forwarded-Access-Token"] != null && {
                "X-Forwarded-Access-Token": params["X-Forwarded-Access-Token"]
            }),
            ...options?.headers
        }
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export const debugMyScopesKey = (params?: DebugMyScopesParams)=>{
    return [
        "/api/v1/debug/me/scopes",
        params
    ] as const;
};
export function useDebugMyScopes<TData = {
    data: ScopeDebugOut;
}>(options?: {
    params?: DebugMyScopesParams;
    query?: Omit<UseQueryOptions<{
        data: ScopeDebugOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: debugMyScopesKey(options?.params),
        queryFn: ()=>debugMyScopes(options?.params),
        ...options?.query
    });
}
export function useDebugMyScopesSuspense<TData = {
    data: ScopeDebugOut;
}>(options?: {
    params?: DebugMyScopesParams;
    query?: Omit<UseSuspenseQueryOptions<{
        data: ScopeDebugOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useSuspenseQuery({
        queryKey: debugMyScopesKey(options?.params),
        queryFn: ()=>debugMyScopes(options?.params),
        ...options?.query
    });
}
export const healthLive = async (options?: RequestInit): Promise<{
    data: HealthLiveOut;
}> =>{
    const res = await fetch("/api/v1/health/live", {
        ...options,
        method: "GET"
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export const healthLiveKey = ()=>{
    return [
        "/api/v1/health/live"
    ] as const;
};
export function useHealthLive<TData = {
    data: HealthLiveOut;
}>(options?: {
    query?: Omit<UseQueryOptions<{
        data: HealthLiveOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: healthLiveKey(),
        queryFn: ()=>healthLive(),
        ...options?.query
    });
}
export function useHealthLiveSuspense<TData = {
    data: HealthLiveOut;
}>(options?: {
    query?: Omit<UseSuspenseQueryOptions<{
        data: HealthLiveOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useSuspenseQuery({
        queryKey: healthLiveKey(),
        queryFn: ()=>healthLive(),
        ...options?.query
    });
}
export const healthReady = async (options?: RequestInit): Promise<{
    data: HealthReadyOut;
}> =>{
    const res = await fetch("/api/v1/health/ready", {
        ...options,
        method: "GET"
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export const healthReadyKey = ()=>{
    return [
        "/api/v1/health/ready"
    ] as const;
};
export function useHealthReady<TData = {
    data: HealthReadyOut;
}>(options?: {
    query?: Omit<UseQueryOptions<{
        data: HealthReadyOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: healthReadyKey(),
        queryFn: ()=>healthReady(),
        ...options?.query
    });
}
export function useHealthReadySuspense<TData = {
    data: HealthReadyOut;
}>(options?: {
    query?: Omit<UseSuspenseQueryOptions<{
        data: HealthReadyOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useSuspenseQuery({
        queryKey: healthReadyKey(),
        queryFn: ()=>healthReady(),
        ...options?.query
    });
}
export const currentUser = async (options?: RequestInit): Promise<{
    data: UserOut;
}> =>{
    const res = await fetch("/api/v1/me", {
        ...options,
        method: "GET"
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try {
            parsed = JSON.parse(body);
        } catch  {
            parsed = body;
        }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return {
        data: await res.json()
    };
};
export const currentUserKey = ()=>{
    return [
        "/api/v1/me"
    ] as const;
};
export function useCurrentUser<TData = {
    data: UserOut;
}>(options?: {
    query?: Omit<UseQueryOptions<{
        data: UserOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: currentUserKey(),
        queryFn: ()=>currentUser(),
        ...options?.query
    });
}
export function useCurrentUserSuspense<TData = {
    data: UserOut;
}>(options?: {
    query?: Omit<UseSuspenseQueryOptions<{
        data: UserOut;
    }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useSuspenseQuery({
        queryKey: currentUserKey(),
        queryFn: ()=>currentUser(),
        ...options?.query
    });
}
// -- Phase 4 (Apr 26 2026): App config + user prefs + pins + per-message
//    chart / suggestions rehydration. These endpoints aren't in the apx
//    codegen spec yet, so they're hand-written below using the same
//    helper shape so the rest of the UI feels uniform.
export type ChartKindApi = "bar" | "line" | "pie" | "scatter" | "table";
export interface ChartArtifactOut {
    chart_id: string;
    message_id: string;
    conversation_id: string;
    chart_kind: ChartKindApi;
    title: string;
    // The backend persists the structured form ``[{name, type}, ...]`` so
    // ``pick_chart`` can re-run if we ever want to. The frontend only
    // needs names. We accept both shapes here; the ``ChartHydrator``
    // flattens to ``string[]`` before the artifact reaches any view.
    columns: Array<string | { name: string; type?: string }>;
    rows: Array<Array<string | number | boolean | null>>;
    option: Record<string, unknown>;
    truncated: boolean;
    // 0-based render order within a multi-chart Genie turn. Optional so
    // the pre-multichart backend (before the idx column ALTER ran) still
    // decodes cleanly; treat missing as 0.
    idx?: number;
    created_at?: string;
}
export interface ChartListOut {
    message_id: string;
    charts: ChartArtifactOut[];
}
export type SuggestionSourceApi = "genie_native" | "llm" | "fallback";
export interface SuggestionsOut {
    message_id: string;
    source: SuggestionSourceApi;
    suggestions: string[];
}
export interface FeatureFlagOut {
    master_on: boolean;
    default_on: boolean;
    effective_on: boolean;
}
export interface FeatureFlagsOut {
    ai_suggestions: FeatureFlagOut;
    charts: FeatureFlagOut;
    pinned: FeatureFlagOut;
}
export interface AppConfigOut {
    legacy_ui: boolean;
    feature_flags: FeatureFlagsOut;
}
export type ThemeModeApi = "system" | "light" | "dark";
export interface UserFeatureOverridesApi {
    ai_suggestions?: boolean | null;
    charts?: boolean | null;
    pinned?: boolean | null;
}
export interface UserPrefsOut {
    theme: ThemeModeApi;
    feature_overrides: UserFeatureOverridesApi;
    updated_at?: string | null;
}
export interface UserPrefsUpdate {
    theme?: ThemeModeApi;
    feature_overrides?: UserFeatureOverridesApi;
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

// -- App config (legacy_ui + resolved feature flags). Cold-boot path.

export const getAppConfig = async (options?: RequestInit): Promise<{
    data: AppConfigOut;
}> => {
    const res = await fetch("/api/v1/app/config", {
        ...options,
        method: "GET"
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try { parsed = JSON.parse(body); } catch { parsed = body; }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return { data: await res.json() };
};
export const getAppConfigKey = () => ["/api/v1/app/config"] as const;
export function useGetAppConfig<TData = { data: AppConfigOut; }>(options?: {
    query?: Omit<UseQueryOptions<{ data: AppConfigOut; }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: getAppConfigKey(),
        queryFn: () => getAppConfig(),
        ...options?.query
    });
}

// -- User prefs (theme + per-user feature overrides).

export const getUserPrefs = async (options?: RequestInit): Promise<{
    data: UserPrefsOut;
}> => {
    const res = await fetch("/api/v1/user/prefs", {
        ...options,
        method: "GET"
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try { parsed = JSON.parse(body); } catch { parsed = body; }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return { data: await res.json() };
};
export const getUserPrefsKey = () => ["/api/v1/user/prefs"] as const;
export function useGetUserPrefs<TData = { data: UserPrefsOut; }>(options?: {
    query?: Omit<UseQueryOptions<{ data: UserPrefsOut; }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: getUserPrefsKey(),
        queryFn: () => getUserPrefs(),
        ...options?.query
    });
}
export const putUserPrefs = async (data: UserPrefsUpdate, options?: RequestInit): Promise<{
    data: UserPrefsOut;
}> => {
    const res = await fetch("/api/v1/user/prefs", {
        ...options,
        method: "PUT",
        headers: { "Content-Type": "application/json", ...options?.headers },
        body: JSON.stringify(data)
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try { parsed = JSON.parse(body); } catch { parsed = body; }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return { data: await res.json() };
};
export function usePutUserPrefs(options?: {
    mutation?: UseMutationOptions<{ data: UserPrefsOut; }, ApiError, UserPrefsUpdate>;
}) {
    return useMutation({
        mutationFn: (data) => putUserPrefs(data),
        ...options?.mutation
    });
}

// -- Pins (per-user, per-agent saved questions).

export interface ListPinsParams { endpoint_name: string; }
export const listPins = async (params: ListPinsParams, options?: RequestInit): Promise<{
    data: PinListOut;
}> => {
    const res = await fetch(`/api/v1/pins/${encodeURIComponent(params.endpoint_name)}`, {
        ...options,
        method: "GET"
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try { parsed = JSON.parse(body); } catch { parsed = body; }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return { data: await res.json() };
};
export const listPinsKey = (params?: ListPinsParams) =>
    ["/api/v1/pins/{endpoint_name}", params] as const;
export function useListPins<TData = { data: PinListOut; }>(options: {
    params: ListPinsParams;
    query?: Omit<UseQueryOptions<{ data: PinListOut; }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: listPinsKey(options.params),
        queryFn: () => listPins(options.params),
        ...options?.query
    });
}
export interface CreatePinParams { endpoint_name: string; }
export const createPin = async (params: CreatePinParams, data: PinIn, options?: RequestInit): Promise<{
    data: PinOut;
}> => {
    const res = await fetch(`/api/v1/pins/${encodeURIComponent(params.endpoint_name)}`, {
        ...options,
        method: "POST",
        headers: { "Content-Type": "application/json", ...options?.headers },
        body: JSON.stringify(data)
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try { parsed = JSON.parse(body); } catch { parsed = body; }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return { data: await res.json() };
};
export function useCreatePin(options?: {
    mutation?: UseMutationOptions<{ data: PinOut; }, ApiError, {
        params: CreatePinParams;
        data: PinIn;
    }>;
}) {
    return useMutation({
        mutationFn: (vars) => createPin(vars.params, vars.data),
        ...options?.mutation
    });
}
export interface UpdatePinParams { endpoint_name: string; pin_id: string; }
export const updatePin = async (params: UpdatePinParams, data: PinPatch, options?: RequestInit): Promise<{
    data: PinOut;
}> => {
    const res = await fetch(
        `/api/v1/pins/${encodeURIComponent(params.endpoint_name)}/${encodeURIComponent(params.pin_id)}`,
        {
            ...options,
            method: "PATCH",
            headers: { "Content-Type": "application/json", ...options?.headers },
            body: JSON.stringify(data)
        }
    );
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try { parsed = JSON.parse(body); } catch { parsed = body; }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return { data: await res.json() };
};
export function useUpdatePin(options?: {
    mutation?: UseMutationOptions<{ data: PinOut; }, ApiError, {
        params: UpdatePinParams;
        data: PinPatch;
    }>;
}) {
    return useMutation({
        mutationFn: (vars) => updatePin(vars.params, vars.data),
        ...options?.mutation
    });
}
export interface DeletePinParams { endpoint_name: string; pin_id: string; }
export const deletePin = async (params: DeletePinParams, options?: RequestInit): Promise<{
    data: DeleteResult;
}> => {
    const res = await fetch(
        `/api/v1/pins/${encodeURIComponent(params.endpoint_name)}/${encodeURIComponent(params.pin_id)}`,
        { ...options, method: "DELETE" }
    );
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try { parsed = JSON.parse(body); } catch { parsed = body; }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return { data: await res.json() };
};
export function useDeletePin(options?: {
    mutation?: UseMutationOptions<{ data: DeleteResult; }, ApiError, {
        params: DeletePinParams;
    }>;
}) {
    return useMutation({
        mutationFn: (vars) => deletePin(vars.params),
        ...options?.mutation
    });
}

// -- Pin click telemetry --
// Fire-and-forget from PinnedQuestionsBar when the user resubmits a
// pinned question. The backend records a ``click`` event in
// ``pin_events`` so the dev team can measure which pins actually get
// reused. We never await this call from the UI -- telemetry failures
// must not delay the chat send.
export interface RecordPinClickParams { endpoint_name: string; pin_id: string; }
export interface PinClickResult { ok: boolean; recorded: boolean; }
export const recordPinClick = async (
    params: RecordPinClickParams,
    options?: RequestInit,
): Promise<{ data: PinClickResult }> => {
    const res = await fetch(
        `/api/v1/pins/${encodeURIComponent(params.endpoint_name)}/${encodeURIComponent(params.pin_id)}/click`,
        { ...options, method: "POST" },
    );
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try { parsed = JSON.parse(body); } catch { parsed = body; }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return { data: await res.json() };
};

// -- Per-message rehydrate (chart + suggestions). Used on conversation
//    reload so the streaming SSE events aren't required to render an
//    older transcript.

export interface GetMessageChartParams { message_id: string; }
export const getMessageChart = async (params: GetMessageChartParams, options?: RequestInit): Promise<{
    data: ChartArtifactOut;
}> => {
    const res = await fetch(
        `/api/v1/messages/${encodeURIComponent(params.message_id)}/chart`,
        { ...options, method: "GET" }
    );
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try { parsed = JSON.parse(body); } catch { parsed = body; }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return { data: await res.json() };
};
export const getMessageChartKey = (params?: GetMessageChartParams) =>
    ["/api/v1/messages/{message_id}/chart", params] as const;
export function useGetMessageChart<TData = { data: ChartArtifactOut; }>(options: {
    params: GetMessageChartParams;
    query?: Omit<UseQueryOptions<{ data: ChartArtifactOut; }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: getMessageChartKey(options.params),
        queryFn: () => getMessageChart(options.params),
        ...options?.query
    });
}

// List-variant for messages that carry more than one chart. The single
// ``getMessageChart`` call returns only the primary artifact (idx=0)
// for back-compat; this endpoint returns every chart attached to the
// message in stable render order.
export interface ListMessageChartsParams { message_id: string; }
export const listMessageCharts = async (
    params: ListMessageChartsParams,
    options?: RequestInit,
): Promise<{ data: ChartListOut }> => {
    const res = await fetch(
        `/api/v1/messages/${encodeURIComponent(params.message_id)}/charts`,
        { ...options, method: "GET" },
    );
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try { parsed = JSON.parse(body); } catch { parsed = body; }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return { data: await res.json() };
};
export const listMessageChartsKey = (params?: ListMessageChartsParams) =>
    ["/api/v1/messages/{message_id}/charts", params] as const;
export function useListMessageCharts<TData = { data: ChartListOut }>(options: {
    params: ListMessageChartsParams;
    query?: Omit<UseQueryOptions<{ data: ChartListOut }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: listMessageChartsKey(options.params),
        queryFn: () => listMessageCharts(options.params),
        ...options?.query,
    });
}
export interface GetMessageSuggestionsParams {
    message_id: string;
    refresh?: boolean;
}
export const getMessageSuggestions = async (
    params: GetMessageSuggestionsParams,
    options?: RequestInit,
): Promise<{
    data: SuggestionsOut;
}> => {
    const search = new URLSearchParams();
    if (params.refresh) search.set("refresh", "true");
    const qs = search.toString();
    const url =
        `/api/v1/messages/${encodeURIComponent(params.message_id)}/suggestions` +
        (qs ? `?${qs}` : "");
    const res = await fetch(url, { ...options, method: "GET" });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try { parsed = JSON.parse(body); } catch { parsed = body; }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return { data: await res.json() };
};
export const getMessageSuggestionsKey = (params?: GetMessageSuggestionsParams) =>
    ["/api/v1/messages/{message_id}/suggestions", params] as const;
export function useGetMessageSuggestions<TData = { data: SuggestionsOut; }>(options: {
    params: GetMessageSuggestionsParams;
    query?: Omit<UseQueryOptions<{ data: SuggestionsOut; }, ApiError, TData>, "queryKey" | "queryFn">;
}) {
    return useQuery({
        queryKey: getMessageSuggestionsKey(options.params),
        queryFn: () => getMessageSuggestions(options.params),
        ...options?.query
    });
}

// -- Manual UC endpoint registration (Option C fallback) ---------------------
//
// Admin-only fallback when tag-discovery isn't available. POST creates a
// `uc:` or `mcp:` catalog row directly; DELETE removes it; GET lists just
// the manually-registered subset so the admin card stays focused. Shape
// mirrors the Phase 4 hand-written helpers above so the rest of the UI
// feels uniform.

export type ManualUCObjectTypeApi = "function" | "connection";
export type ManualUCKindApi = "http" | "mcp";

export interface ManualUCEndpointInApi {
    uc_full_name: string;
    object_type: ManualUCObjectTypeApi;
    kind: ManualUCKindApi;
    display_name?: string | null;
    description?: string | null;
}

export const listManualUCEndpoints = async (
    options?: RequestInit,
): Promise<{ data: CatalogEntryOut[]; }> => {
    const res = await fetch("/api/v1/admin/uc-endpoints", {
        ...options,
        method: "GET",
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try { parsed = JSON.parse(body); } catch { parsed = body; }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return { data: await res.json() };
};
export const listManualUCEndpointsKey = () =>
    ["/api/v1/admin/uc-endpoints"] as const;
export function useListManualUCEndpoints<
    TData = { data: CatalogEntryOut[]; }
>(options?: {
    query?: Omit<
        UseQueryOptions<{ data: CatalogEntryOut[]; }, ApiError, TData>,
        "queryKey" | "queryFn"
    >;
}) {
    return useQuery({
        queryKey: listManualUCEndpointsKey(),
        queryFn: () => listManualUCEndpoints(),
        ...options?.query,
    });
}

export const registerManualUCEndpoint = async (
    data: ManualUCEndpointInApi,
    options?: RequestInit,
): Promise<{ data: CatalogEntryOut; }> => {
    const res = await fetch("/api/v1/admin/uc-endpoints", {
        ...options,
        method: "POST",
        headers: { "Content-Type": "application/json", ...options?.headers },
        body: JSON.stringify(data),
    });
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try { parsed = JSON.parse(body); } catch { parsed = body; }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return { data: await res.json() };
};
export function useRegisterManualUCEndpoint(options?: {
    mutation?: UseMutationOptions<
        { data: CatalogEntryOut; },
        ApiError,
        ManualUCEndpointInApi
    >;
}) {
    return useMutation({
        mutationFn: (data) => registerManualUCEndpoint(data),
        ...options?.mutation,
    });
}

export interface UnregisterManualUCEndpointParams { endpoint_name: string; }
export const unregisterManualUCEndpoint = async (
    params: UnregisterManualUCEndpointParams,
    options?: RequestInit,
): Promise<{ data: DeleteResult; }> => {
    // endpoint_name contains a colon (e.g. "uc:main.default.fn"). We match
    // the router's `:path` converter by NOT encoding the colon separator,
    // but we do encode the rest so weird catalog/schema names don't break
    // the URL.
    const res = await fetch(
        `/api/v1/admin/uc-endpoints/${encodeURI(params.endpoint_name)}`,
        { ...options, method: "DELETE" },
    );
    if (!res.ok) {
        const body = await res.text();
        let parsed: unknown;
        try { parsed = JSON.parse(body); } catch { parsed = body; }
        throw new ApiError(res.status, res.statusText, parsed);
    }
    return { data: await res.json() };
};
export function useUnregisterManualUCEndpoint(options?: {
    mutation?: UseMutationOptions<
        { data: DeleteResult; },
        ApiError,
        UnregisterManualUCEndpointParams
    >;
}) {
    return useMutation({
        mutationFn: (params) => unregisterManualUCEndpoint(params),
        ...options?.mutation,
    });
}
