import { createFileRoute } from "@tanstack/react-router";
import { BarChart3, Pin, Sparkles, UserRound } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useTheme } from "@/providers/theme-provider";
import type { FeatureFlag } from "@/lib/app-config";

export const Route = createFileRoute("/_sidebar/preferences")({
  component: PreferencesPage,
});

/*
 * User-side opt-out panel for the three Phase-4 chat features.
 *
 * The two-tier model means the per-user toggles are only meaningful
 * while the admin master is on. We disable the row when the master is
 * off and explain *why* in the help text -- otherwise the user is left
 * staring at a toggle that does nothing.
 *
 * No save button: each toggle persists optimistically through
 * ThemeProvider.setFeatureOverride, matching the rest of the app's
 * fire-and-forget user-prefs pattern.
 */
function PreferencesPage() {
  const { featureFlags, featureOverrides, setFeatureOverride } = useTheme();

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 p-6 md:p-8">
      <header className="flex items-start gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
          <UserRound className="h-[18px] w-[18px]" />
        </div>
        <div>
          <h1
            className={[
              "text-[1.75rem] font-bold leading-[1.15] tracking-[-0.025em]",
              "font-[family-name:var(--font-display)] text-text-primary",
            ].join(" ")}
          >
            Preferences
          </h1>
          <p className="mt-0.5 text-[0.9375rem] text-text-muted">
            Personalize how the chat experience behaves for you. These
            preferences apply across all conversations and devices once
            saved.
          </p>
        </div>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Chat features</CardTitle>
          <CardDescription>
            Toggle the additional chat affordances on or off. If a
            feature is greyed out, an admin has disabled it for the
            entire workspace.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <PrefRow
            icon={Sparkles}
            title="AI chat suggestions"
            description="Show up to three context-aware follow-up question chips above the chat input after each assistant turn."
            featureKey="ai_suggestions"
            flag={featureFlags.ai_suggestions}
            override={featureOverrides.ai_suggestions ?? null}
            onChange={(next) => setFeatureOverride("ai_suggestions", next)}
          />
          <PrefRow
            icon={BarChart3}
            title="Auto-charts from Genie"
            description="Render Genie SQL results as an interactive ECharts chart above the textual answer."
            featureKey="charts"
            flag={featureFlags.charts}
            override={featureOverrides.charts ?? null}
            onChange={(next) => setFeatureOverride("charts", next)}
          />
          <PrefRow
            icon={Pin}
            title="Pinned questions"
            description="Pin questions you ask often for quick reuse. Pins are scoped per agent."
            featureKey="pinned"
            flag={featureFlags.pinned}
            override={featureOverrides.pinned ?? null}
            onChange={(next) => setFeatureOverride("pinned", next)}
          />
        </CardContent>
      </Card>
    </div>
  );
}

interface PrefRowProps {
  icon: typeof Sparkles;
  title: string;
  description: string;
  featureKey: "ai_suggestions" | "charts" | "pinned";
  flag: FeatureFlag;
  override: boolean | null;
  onChange: (next: boolean | null) => void;
}

function PrefRow({
  icon: Icon,
  title,
  description,
  flag,
  override,
  onChange,
}: PrefRowProps) {
  // ``master_on === false`` -> admin has hard-disabled the feature; the
  // toggle is read-only and explains why.
  const masterOff = !flag.master_on;
  // Effective state derives from ``flag.effective_on`` (server resolved)
  // but we let the UI flip through the override directly for snappy
  // feedback. Falls back to admin default when the user has not made a
  // choice yet (override === null).
  const effective =
    override === null ? flag.master_on && flag.default_on : flag.master_on && override;

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
            "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full",
            effective ? "bg-info/15 text-info" : "bg-surface-elevated text-text-secondary",
          ].join(" ")}
        >
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-[0.9375rem] font-semibold text-text-primary">{title}</h3>
          <p className="mt-1 text-[0.8125rem] leading-[1.45] text-text-muted">{description}</p>
          {masterOff && (
            <p className="mt-1.5 text-[0.75rem] text-text-muted">
              Disabled by admin for the whole workspace.
            </p>
          )}
        </div>
        <Toggle
          checked={effective}
          disabled={masterOff}
          onChange={(next) => onChange(next)}
          ariaLabel={title}
        />
      </div>
    </div>
  );
}

interface ToggleProps {
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
  ariaLabel: string;
}

function Toggle({ checked, disabled, onChange, ariaLabel }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      className={[
        "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full",
        "transition-colors duration-150",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info focus-visible:ring-offset-2",
        "focus-visible:ring-offset-surface",
        checked ? "bg-info" : "bg-surface-overlay",
        "disabled:cursor-not-allowed disabled:opacity-40",
      ].join(" ")}
    >
      <span
        className={[
          "inline-block h-5 w-5 transform rounded-full bg-white shadow",
          "transition-transform duration-150",
          checked ? "translate-x-[22px]" : "translate-x-[2px]",
        ].join(" ")}
      />
    </button>
  );
}
