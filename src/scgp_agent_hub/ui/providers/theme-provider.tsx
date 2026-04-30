import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { fetchAppConfig, type FeatureFlagsConfig } from "@/lib/app-config";
import {
  getUserPrefs,
  putUserPrefs,
  type UserFeatureOverrides,
} from "@/lib/user-prefs";

export type ThemeMode = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const DEFAULT_FLAGS: FeatureFlagsConfig = {
  ai_suggestions: { master_on: false, default_on: false, effective_on: false },
  charts: { master_on: false, default_on: false, effective_on: false },
  pinned: { master_on: false, default_on: false, effective_on: false },
};

interface ThemeContextValue {
  /** The user's stored preference (system / light / dark). */
  mode: ThemeMode;
  /** What's actually applied to the DOM right now. */
  resolved: ResolvedTheme;
  setMode: (mode: ThemeMode) => void;
  /**
   * True when SCGP_LEGACY_UI is set on the server. Consumers should hide
   * the theme toggle and any Clarity-specific affordances when this flips
   * on, so the rollback lever behaves as "new UI disabled" end-to-end.
   */
  legacy: boolean;
  /**
   * Resolved per-feature flags (admin master + admin default + user
   * opt-out folded into a single ``effective_on`` per feature). These
   * are loaded once on mount alongside ``legacy`` so the rest of the
   * app can branch on flag state without re-querying.
   */
  featureFlags: FeatureFlagsConfig;
  /**
   * The user's per-feature opt-out values (or empty when anonymous).
   * Settings UI uses this to render the toggle state directly; the
   * effective state is already computed in ``featureFlags``.
   */
  featureOverrides: UserFeatureOverrides;
  /**
   * Patch the user's overrides locally. Persists in the same
   * fire-and-forget manner as ``setMode``: failures don't roll back
   * the optimistic update because the overrides are non-critical UI
   * preferences.
   */
  setFeatureOverride: (
    key: keyof UserFeatureOverrides,
    value: boolean | null,
  ) => void;
  /**
   * Re-fetch ``/app/config`` so the resolved feature flags reflect the
   * latest admin write. Used by the admin settings UI after a save so
   * the chrome (suggestions, charts, pins) flips on without a reload.
   */
  refreshFeatureFlags: () => Promise<void>;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);
const STORAGE_KEY = "scgp.theme";

function readInitialMode(): ThemeMode {
  if (typeof window === "undefined") return "system";
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "light" || raw === "dark" || raw === "system") return raw;
  } catch {
    // ignore — private mode etc.
  }
  return "system";
}

function systemResolved(): ResolvedTheme {
  if (typeof window === "undefined" || !window.matchMedia) return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

function resolve(mode: ThemeMode): ResolvedTheme {
  if (mode === "system") return systemResolved();
  return mode;
}

function applyToDom(resolved: ResolvedTheme, legacy: boolean): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (legacy) {
    // ``data-legacy-ui`` is the companion to ``data-theme``: consumers
    // can query either in CSS or TS to hide Clarity-only affordances.
    root.setAttribute("data-legacy-ui", "1");
    root.setAttribute("data-theme", "dark");
    root.classList.add("dark");
    return;
  }
  root.removeAttribute("data-legacy-ui");
  root.setAttribute("data-theme", resolved);
  // Keep the legacy ``dark`` class so any Tailwind ``dark:`` utilities
  // lingering from the Observatory direction keep behaving until they
  // are migrated away in a follow-up pass.
  root.classList.toggle("dark", resolved === "dark");
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() => readInitialMode());
  const [resolved, setResolved] = useState<ResolvedTheme>(() =>
    resolve(readInitialMode()),
  );
  const [legacy, setLegacy] = useState<boolean>(false);
  const [featureFlags, setFeatureFlags] =
    useState<FeatureFlagsConfig>(DEFAULT_FLAGS);
  const [featureOverrides, setFeatureOverrides] =
    useState<UserFeatureOverrides>({});

  useEffect(() => {
    applyToDom(resolved, legacy);
  }, [resolved, legacy]);

  // Fetch the server-side SCGP_LEGACY_UI flag plus the resolved Phase
  // 4 feature flags. If the endpoint fails for any reason we assume
  // the flags are off (new Clarity UI remains active, no
  // suggestions / charts / pins chrome renders).
  useEffect(() => {
    let cancelled = false;
    fetchAppConfig()
      .then((cfg) => {
        if (cancelled) return;
        setLegacy(Boolean(cfg.legacy_ui));
        setFeatureFlags(cfg.feature_flags ?? DEFAULT_FLAGS);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // Re-resolve when the preference or the OS theme changes.
  useEffect(() => {
    setResolved(resolve(mode));
    if (mode !== "system") return;
    if (!window.matchMedia) return;
    const mql = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setResolved(resolve("system"));
    mql.addEventListener?.("change", onChange);
    return () => mql.removeEventListener?.("change", onChange);
  }, [mode]);

  // Hydrate the user's saved preference from the backend (best effort —
  // local storage is still the source of truth for first paint). We
  // also pull ``feature_overrides`` here so the settings UI can show
  // the current opt-out state without an extra round trip.
  useEffect(() => {
    let cancelled = false;
    getUserPrefs()
      .then((prefs) => {
        if (cancelled) return;
        if (prefs?.theme === "light" || prefs?.theme === "dark" || prefs?.theme === "system") {
          setModeState(prefs.theme);
        }
        if (prefs?.feature_overrides) {
          setFeatureOverrides(prefs.feature_overrides);
        }
      })
      .catch(() => {
        // Anonymous / debug contexts can 401; fall back to localStorage.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // ignore
    }
    // Fire-and-forget server persistence. If it fails the user still
    // has the local preference until the next successful round-trip.
    putUserPrefs({ theme: next }).catch(() => undefined);
  }, []);

  const setFeatureOverride = useCallback(
    (key: keyof UserFeatureOverrides, value: boolean | null) => {
      setFeatureOverrides((prev) => ({ ...prev, [key]: value }));
      // Optimistically update ``featureFlags.effective_on`` so any
      // chrome reading off the resolved flag flips immediately. The
      // server is the source of truth on the next refresh.
      setFeatureFlags((prev) => {
        const flag = prev[key as keyof FeatureFlagsConfig];
        if (!flag) return prev;
        const userOff = value === false;
        return {
          ...prev,
          [key]: {
            ...flag,
            effective_on: flag.master_on && flag.default_on && !userOff,
          },
        };
      });
      putUserPrefs({
        feature_overrides: { [key]: value } as UserFeatureOverrides,
      }).catch(() => undefined);
    },
    [],
  );

  const refreshFeatureFlags = useCallback(async () => {
    try {
      const cfg = await fetchAppConfig();
      setLegacy(Boolean(cfg.legacy_ui));
      setFeatureFlags(cfg.feature_flags ?? DEFAULT_FLAGS);
    } catch {
      // ignore — keep current flags
    }
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({
      mode,
      resolved,
      setMode,
      legacy,
      featureFlags,
      featureOverrides,
      setFeatureOverride,
      refreshFeatureFlags,
    }),
    [
      mode,
      resolved,
      setMode,
      legacy,
      featureFlags,
      featureOverrides,
      setFeatureOverride,
      refreshFeatureFlags,
    ],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used inside <ThemeProvider>");
  }
  return ctx;
}
