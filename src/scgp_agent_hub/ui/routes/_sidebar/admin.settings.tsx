import { useEffect, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Activity, Brain, BrainCircuit, Check, Layers, Loader2, Save, Settings, ZapOff } from "lucide-react";
import { toast } from "sonner";

import { useAdminSettings, useUpdateMemoryMode } from "@/hooks/use-admin";
import { useHealthReady } from "@/lib/api";
import type { MemoryMode } from "@/components/chat/agent-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FeatureFlagsCard } from "@/components/admin/feature-flags-card";

export const Route = createFileRoute("/_sidebar/admin/settings")({
  component: AdminSettingsPage,
});

const MEMORY_OPTIONS: Array<{
  mode: MemoryMode;
  title: string;
  description: string;
  icon: typeof Brain;
}> = [
  {
    mode: "off",
    title: "Off",
    description: "Each request is sent in isolation. The agent has no memory of prior turns.",
    icon: ZapOff,
  },
  {
    mode: "short_term",
    title: "Short-term",
    description: "Recent messages from the current conversation are included as context.",
    icon: Brain,
  },
  {
    mode: "long_term",
    title: "Long-term",
    description: "Durable insights from past conversations are extracted and reused across sessions.",
    icon: BrainCircuit,
  },
  {
    mode: "both",
    title: "Both",
    description: "Recent conversation history plus long-term insights are sent to the agent.",
    icon: Layers,
  },
];

function AdminSettingsPage() {
  const { memoryMode, isLoading } = useAdminSettings();
  const update = useUpdateMemoryMode();
  const health = useHealthReady();

  const [draft, setDraft] = useState<MemoryMode | null>(null);

  useEffect(() => {
    if (memoryMode && draft === null) setDraft(memoryMode);
  }, [memoryMode, draft]);

  const dirty = draft !== null && memoryMode && draft !== memoryMode;

  const handleSave = () => {
    if (!draft || !dirty) return;
    update.mutate(
      { params: { key: "memory_mode" }, data: { value: draft } },
      {
        onSuccess: () =>
          toast.success("Memory mode updated", {
            description: `Now using "${draft.replace("_", " ")}" memory.`,
          }),
        onError: (err) =>
          toast.error("Failed to update memory mode", { description: err.message }),
      },
    );
  };

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 p-6 md:p-8">
      <header className="flex items-start gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
          <Settings className="h-[18px] w-[18px]" />
        </div>
        <div>
          <h1
            className={[
              "text-[1.75rem] font-bold leading-[1.15] tracking-[-0.025em]",
              "font-[family-name:var(--font-display)] text-text-primary",
            ].join(" ")}
          >
            Settings
          </h1>
          <p className="mt-0.5 text-[0.9375rem] text-text-muted">
            Configure global behavior of the agent hub.
          </p>
        </div>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Memory mode</CardTitle>
          <CardDescription>
            How much context is passed to agents on each turn. Changes apply
            globally and immediately to all conversations.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading || draft === null ? (
            <div className="flex h-32 items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-text-muted" />
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {MEMORY_OPTIONS.map((opt) => {
                const Icon = opt.icon;
                const selected = draft === opt.mode;
                return (
                  <button
                    key={opt.mode}
                    type="button"
                    onClick={() => setDraft(opt.mode)}
                    aria-pressed={selected}
                    className={[
                      "flex items-start gap-3 rounded-[var(--radius-lg)] border p-4 text-left",
                      "transition-[background-color,border-color,box-shadow] duration-150",
                      "focus-visible:outline-none focus-visible:ring-2",
                      "focus-visible:ring-info focus-visible:ring-offset-2",
                      "focus-visible:ring-offset-surface-elevated",
                      selected
                        ? "border-info bg-info/5 ring-1 ring-info"
                        : "border-border bg-surface hover:border-border-strong hover:bg-surface-elevated",
                    ].join(" ")}
                  >
                    <div
                      className={[
                        "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full",
                        selected
                          ? "bg-info/15 text-info"
                          : "bg-surface-elevated text-text-secondary",
                      ].join(" ")}
                    >
                      <Icon className="h-[18px] w-[18px]" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <h3 className="text-[0.9375rem] font-semibold text-text-primary">
                          {opt.title}
                        </h3>
                        {selected && (
                          <Check className="h-3.5 w-3.5 text-info" />
                        )}
                      </div>
                      <p className="mt-1 text-[0.8125rem] leading-[1.45] text-text-muted">
                        {opt.description}
                      </p>
                    </div>
                  </button>
                );
              })}
            </div>
          )}

          <div className="mt-5 flex items-center justify-between border-t border-border pt-4">
            <p className="text-[0.75rem] text-text-muted">
              {dirty
                ? `Pending change: ${draft?.replace("_", " ")}`
                : "No unsaved changes"}
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

      <FeatureFlagsCard />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-text-muted" />
            System status
          </CardTitle>
          <CardDescription>
            Live health of the supporting services.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div
            className={[
              "overflow-hidden rounded-[var(--radius-md)]",
              "border border-border bg-surface",
            ].join(" ")}
          >
            <StatusRow
              label="Backend"
              value={
                health.data?.data.status ??
                (health.isLoading ? "checking..." : "unknown")
              }
              ok={health.data?.data.status === "ok"}
              loading={health.isLoading}
            />
            <StatusRow
              label="Database"
              value={
                health.data?.data.database ??
                (health.isLoading ? "checking..." : "unknown")
              }
              ok={health.data?.data.database === "ok"}
              loading={health.isLoading}
            />
            <StatusRow
              label="Workspace"
              value={
                health.data?.data.workspace ??
                (health.isLoading ? "checking..." : "unknown")
              }
              ok={health.data?.data.workspace === "ok"}
              loading={health.isLoading}
            />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function StatusRow({
  label,
  value,
  ok,
  loading,
}: {
  label: string;
  value: string;
  ok: boolean;
  loading: boolean;
}) {
  return (
    <div
      className={[
        "flex items-center justify-between px-4 py-3",
        "border-b border-border last:border-b-0",
      ].join(" ")}
    >
      <span className="text-[0.875rem] text-text-secondary">{label}</span>
      {loading ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin text-text-muted" />
      ) : (
        <Badge variant={ok ? "success" : "warning"} shape="pill">
          {value}
        </Badge>
      )}
    </div>
  );
}
