import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { BarChart3, Loader2, Pin, Save, Sparkles } from "lucide-react";
import { toast } from "sonner";

import {
  getAdminSettingsKey,
  useGetAdminSettings,
  useUpdateAdminSetting,
} from "@/lib/api";
import { useTheme } from "@/providers/theme-provider";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

/*
 * Phase 4: three master switches for the new chat experience.
 *
 * The raw value lives at ``admin_settings.feature_flags`` as JSON of the
 * shape:
 *
 *   {
 *     "ai_suggestions": {"enabled": bool, "default_on": bool,
 *                        "models": {"default": "...", "<AGENT_TYPE>": "..."}},
 *     "charts":         {"enabled": bool, "default_on": bool, "max_rows": int},
 *     "pinned":         {"enabled": bool, "default_on": bool, "max_per_agent": int}
 *   }
 *
 * We expose only the fields that have business value for an admin to
 * flip in the UI -- ``enabled`` (master kill), ``default_on`` (default
 * behavior for users who never touched their override), and the
 * suggestion ``models.default`` slot. The ``max_rows`` /
 * ``max_per_agent`` knobs and per-agent-type model overrides remain in
 * the backend defaults until someone actually asks for a UI to manage
 * them.
 */

interface FeatureKnob {
  enabled: boolean;
  default_on: boolean;
}

interface FeatureFlagsBlob {
  ai_suggestions: FeatureKnob & {
    models?: Record<string, string | undefined>;
  };
  charts: FeatureKnob & { max_rows?: number };
  pinned: FeatureKnob & { max_per_agent?: number };
}

const DEFAULT_BLOB: FeatureFlagsBlob = {
  ai_suggestions: {
    enabled: false,
    default_on: true,
    models: { default: "databricks-meta-llama-3-3-70b-instruct" },
  },
  charts: { enabled: false, default_on: true, max_rows: 5000 },
  pinned: { enabled: false, default_on: true, max_per_agent: 30 },
};

function coerceKnob(raw: unknown): FeatureKnob {
  if (!raw || typeof raw !== "object") return { enabled: false, default_on: false };
  const obj = raw as Record<string, unknown>;
  return {
    enabled: Boolean(obj.enabled),
    default_on: Boolean(obj.default_on),
  };
}

function coerceBlob(raw: unknown): FeatureFlagsBlob {
  if (!raw || typeof raw !== "object") return { ...DEFAULT_BLOB };
  const obj = raw as Record<string, unknown>;
  const ai = (obj.ai_suggestions as Record<string, unknown>) ?? {};
  const charts = (obj.charts as Record<string, unknown>) ?? {};
  const pinned = (obj.pinned as Record<string, unknown>) ?? {};
  const models = (ai.models as Record<string, unknown>) ?? {};
  return {
    ai_suggestions: {
      ...coerceKnob(ai),
      models: Object.fromEntries(
        Object.entries(models).map(([k, v]) => [k, typeof v === "string" ? v : undefined]),
      ),
    },
    charts: {
      ...coerceKnob(charts),
      max_rows:
        typeof charts.max_rows === "number" && charts.max_rows > 0
          ? charts.max_rows
          : DEFAULT_BLOB.charts.max_rows,
    },
    pinned: {
      ...coerceKnob(pinned),
      max_per_agent:
        typeof pinned.max_per_agent === "number" && pinned.max_per_agent > 0
          ? pinned.max_per_agent
          : DEFAULT_BLOB.pinned.max_per_agent,
    },
  };
}

export function FeatureFlagsCard() {
  const settings = useGetAdminSettings();
  const update = useUpdateAdminSetting();
  const queryClient = useQueryClient();
  const { refreshFeatureFlags } = useTheme();

  const remote = useMemo<FeatureFlagsBlob>(
    () => coerceBlob(settings.data?.data?.settings?.["feature_flags"]),
    [settings.data?.data?.settings],
  );

  const [draft, setDraft] = useState<FeatureFlagsBlob | null>(null);

  useEffect(() => {
    if (!settings.isLoading && draft === null) setDraft(remote);
  }, [remote, settings.isLoading, draft]);

  const dirty = useMemo(() => {
    if (!draft) return false;
    return JSON.stringify(draft) !== JSON.stringify(remote);
  }, [draft, remote]);

  const handleSave = () => {
    if (!draft || !dirty) return;
    update.mutate(
      { params: { key: "feature_flags" }, data: { value: draft } },
      {
        onSuccess: () => {
          // Re-fetch admin settings + ask ThemeProvider to re-pull
          // ``/app/config`` so the resolved feature flags propagate to
          // ThemeProvider without a page reload.
          queryClient.invalidateQueries({ queryKey: getAdminSettingsKey() });
          void refreshFeatureFlags();
          toast.success("Feature flags updated");
        },
        onError: (err) =>
          toast.error("Couldn't update feature flags", {
            description: err.message,
          }),
      },
    );
  };

  const isLoading = settings.isLoading || draft === null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Feature flags</CardTitle>
        <CardDescription>
          Master switches and defaults for the suggestion chips, ECharts
          auto-charts, and pinned questions. Master ``OFF`` is a hard
          kill — users will never see the feature regardless of their
          per-user setting. ``Default on`` controls whether new users
          opt in by default.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex h-32 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-text-muted" />
          </div>
        ) : (
          <div className="space-y-4">
            <FlagRow
              icon={Sparkles}
              title="AI chat suggestions"
              description="Up to three context-aware follow-up questions appear above the input after each assistant turn."
              knob={draft!.ai_suggestions}
              onChange={(next) =>
                setDraft({ ...draft!, ai_suggestions: { ...draft!.ai_suggestions, ...next } })
              }
              extra={
                <div className="grid gap-1.5">
                  <label className="text-[0.75rem] uppercase tracking-wide text-text-muted">
                    Default suggestion model
                  </label>
                  <Input
                    value={draft!.ai_suggestions.models?.default ?? ""}
                    onChange={(e) =>
                      setDraft({
                        ...draft!,
                        ai_suggestions: {
                          ...draft!.ai_suggestions,
                          models: {
                            ...(draft!.ai_suggestions.models ?? {}),
                            default: e.target.value,
                          },
                        },
                      })
                    }
                    placeholder="databricks-meta-llama-3-3-70b-instruct"
                  />
                  <p className="text-[0.75rem] text-text-muted">
                    Per-agent-type overrides
                    (<code>ai_suggestions.models.&lt;AGENT_TYPE&gt;</code>) can
                    be edited via API for now.
                  </p>
                </div>
              }
            />

            <FlagRow
              icon={BarChart3}
              title="Auto-charts from Genie"
              description="Turn Genie SQL results into an interactive ECharts visualization above the textual answer."
              knob={draft!.charts}
              onChange={(next) =>
                setDraft({ ...draft!, charts: { ...draft!.charts, ...next } })
              }
            />

            <FlagRow
              icon={Pin}
              title="Pinned questions"
              description="Per-user, per-agent saved questions for quick reuse."
              knob={draft!.pinned}
              onChange={(next) =>
                setDraft({ ...draft!, pinned: { ...draft!.pinned, ...next } })
              }
            />
          </div>
        )}

        <div className="mt-5 flex items-center justify-between border-t border-border pt-4">
          <p className="text-[0.75rem] text-text-muted">
            {dirty ? "Pending changes — click save to apply" : "No unsaved changes"}
          </p>
          <Button onClick={handleSave} disabled={!dirty || update.isPending}>
            {update.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            Save changes
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

interface FlagRowProps {
  icon: typeof Sparkles;
  title: string;
  description: string;
  knob: FeatureKnob;
  onChange: (next: Partial<FeatureKnob>) => void;
  extra?: React.ReactNode;
}

function FlagRow({ icon: Icon, title, description, knob, onChange, extra }: FlagRowProps) {
  return (
    <div
      className={[
        "rounded-[var(--radius-lg)] border border-border bg-surface",
        "p-4",
      ].join(" ")}
    >
      <div className="flex items-start gap-3">
        <div
          className={[
            "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
            knob.enabled ? "bg-info/15 text-info" : "bg-surface-elevated text-text-secondary",
          ].join(" ")}
        >
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-[0.9375rem] font-semibold text-text-primary">{title}</h3>
          <p className="mt-0.5 text-[0.8125rem] leading-[1.45] text-text-muted">{description}</p>
        </div>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <ToggleControl
          label="Master enabled"
          help="Hard kill switch. Off means everyone, including admins."
          checked={knob.enabled}
          onChange={(v) => onChange({ enabled: v })}
        />
        <ToggleControl
          label="Default on"
          help="Whether the feature is on by default for users who never touched their setting."
          checked={knob.default_on}
          onChange={(v) => onChange({ default_on: v })}
          disabled={!knob.enabled}
        />
      </div>
      {extra && <div className="mt-3">{extra}</div>}
    </div>
  );
}

interface ToggleControlProps {
  label: string;
  help: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
}

function ToggleControl({ label, help, checked, onChange, disabled }: ToggleControlProps) {
  return (
    <label
      className={[
        "flex cursor-pointer items-start gap-3 rounded-[var(--radius-md)]",
        "border border-border bg-surface-elevated p-3",
        "transition-colors duration-150",
        disabled ? "cursor-not-allowed opacity-50" : "hover:border-border-strong",
      ].join(" ")}
    >
      <input
        type="checkbox"
        className="mt-1 h-4 w-4 rounded border-border accent-info"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <div className="min-w-0 flex-1">
        <p className="text-[0.875rem] font-medium text-text-primary">{label}</p>
        <p className="mt-0.5 text-[0.75rem] text-text-muted">{help}</p>
      </div>
    </label>
  );
}
