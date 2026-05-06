/**
 * User preferences client for ``/api/v1/user/prefs``.
 *
 * Kept in a handwritten module (instead of ``api.ts``) so the iOS redesign
 * can move forward without waiting on the OpenAPI regeneration pipeline.
 *
 * Phase 4 extends the payload with ``feature_overrides`` so the user can
 * opt out of admin-enabled features individually (see
 * ``feature_flags_service.is_enabled`` for the resolution rules).
 */
import { ApiError } from "@/lib/api";

export type ThemePref = "system" | "light" | "dark";

export interface UserFeatureOverrides {
  ai_suggestions?: boolean | null;
  charts?: boolean | null;
  pinned?: boolean | null;
}

export interface UserPrefs {
  theme: ThemePref;
  feature_overrides?: UserFeatureOverrides;
  updated_at?: string | null;
}

export interface UserPrefsUpdate {
  theme?: ThemePref;
  feature_overrides?: UserFeatureOverrides;
}

const ENDPOINT = "/api/v1/user/prefs";

export async function getUserPrefs(): Promise<UserPrefs | null> {
  const res = await fetch(ENDPOINT, { method: "GET" });
  if (res.status === 401 || res.status === 403) return null;
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, res.statusText, body);
  }
  return (await res.json()) as UserPrefs;
}

export async function putUserPrefs(update: UserPrefsUpdate): Promise<UserPrefs | null> {
  const res = await fetch(ENDPOINT, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  if (res.status === 401 || res.status === 403) return null;
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, res.statusText, body);
  }
  return (await res.json()) as UserPrefs;
}
