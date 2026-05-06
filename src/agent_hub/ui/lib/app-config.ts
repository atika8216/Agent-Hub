/**
 * Public app-config client for ``/api/v1/app/config``.
 *
 * The endpoint is unauthenticated and only returns feature flags the
 * frontend needs before ThemeProvider hydrates (see Phase 3 of the master
 * roadmap). Keep this module free of runtime dependencies so we can import
 * it from the earliest boot path.
 *
 * Phase 4 extends the payload with the resolved suggestion / chart / pin
 * feature flags so the cold-boot path can decide whether to render any of
 * the new chrome at all.
 */

export interface FeatureFlag {
  master_on: boolean;
  default_on: boolean;
  effective_on: boolean;
}

export interface FeatureFlagsConfig {
  ai_suggestions: FeatureFlag;
  charts: FeatureFlag;
  pinned: FeatureFlag;
}

export interface AppConfig {
  legacy_ui: boolean;
  feature_flags: FeatureFlagsConfig;
}

const ENDPOINT = "/api/v1/app/config";

const OFF_FLAG: FeatureFlag = {
  master_on: false,
  default_on: false,
  effective_on: false,
};

const DEFAULT_FLAGS: FeatureFlagsConfig = {
  ai_suggestions: OFF_FLAG,
  charts: OFF_FLAG,
  pinned: OFF_FLAG,
};

function coerceFlag(raw: unknown): FeatureFlag {
  if (!raw || typeof raw !== "object") return OFF_FLAG;
  const obj = raw as Record<string, unknown>;
  return {
    master_on: Boolean(obj.master_on),
    default_on: Boolean(obj.default_on),
    effective_on: Boolean(obj.effective_on),
  };
}

function coerceFlags(raw: unknown): FeatureFlagsConfig {
  if (!raw || typeof raw !== "object") return DEFAULT_FLAGS;
  const obj = raw as Record<string, unknown>;
  return {
    ai_suggestions: coerceFlag(obj.ai_suggestions),
    charts: coerceFlag(obj.charts),
    pinned: coerceFlag(obj.pinned),
  };
}

/**
 * Fetch the app config with a safe fallback. Any error (network, non-JSON
 * response, etc.) resolves to ``{ legacy_ui: false, feature_flags: <off> }``
 * so a broken config endpoint never blocks the UI from rendering, and the
 * Phase 4 features stay invisible until the backend can confirm they are
 * actually on.
 */
export async function fetchAppConfig(): Promise<AppConfig> {
  try {
    const res = await fetch(ENDPOINT, {
      method: "GET",
      cache: "no-store",
    });
    if (!res.ok) {
      return { legacy_ui: false, feature_flags: DEFAULT_FLAGS };
    }
    const body = (await res.json()) as Partial<AppConfig> & {
      feature_flags?: unknown;
    };
    return {
      legacy_ui: Boolean(body?.legacy_ui),
      feature_flags: coerceFlags(body?.feature_flags),
    };
  } catch {
    return { legacy_ui: false, feature_flags: DEFAULT_FLAGS };
  }
}
