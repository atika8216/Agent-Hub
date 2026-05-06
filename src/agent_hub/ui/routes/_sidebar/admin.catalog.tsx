import { useMemo, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import {
  Eye,
  EyeOff,
  KeyRound,
  LayoutGrid,
  Loader2,
  RefreshCw,
  Search,
  ServerCrash,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";

import { useAdminCatalog, useUpdateCatalogEntry } from "@/hooks/use-admin";
import {
  useDiscoverAgents,
  useGrantCatalogAccess,
  useRescanCatalogMetadata,
} from "@/lib/api";
import type {
  CatalogEntryOut,
  GrantAccessResult,
  RescanMetadataResult,
} from "@/lib/api";
import { agentTypeLabel, agentTypeVariant } from "@/lib/agent-type";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ManualEndpointsCard } from "@/components/admin/manual-endpoints-card";
import { TagConfigCard } from "@/components/admin/tag-config-card";
import { useQueryClient } from "@tanstack/react-query";
import { listAdminCatalogKey, listAgentsKey } from "@/lib/api";

export const Route = createFileRoute("/_sidebar/admin/catalog")({
  component: AdminCatalogPage,
});

function AdminCatalogPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, refetch } = useAdminCatalog();
  const { updateEntry, isPending: isUpdating } = useUpdateCatalogEntry();
  const discover = useDiscoverAgents({
    mutation: {
      onSuccess: (result) => {
        const r = result.data;
        toast.success("Discovery complete", {
          description: `Found ${r.discovered ?? 0} agents (${r.new ?? 0} new, ${r.updated ?? 0} updated)`,
        });
        queryClient.invalidateQueries({ queryKey: listAdminCatalogKey() });
        // listAgents triggers a per-agent OBO access probe on refetch
        // (seconds on MAS-heavy workspaces). Keep it stale without
        // forcing a refetch -- the next /catalog mount will pick up the
        // new rows, and the admin stays on /admin/catalog with an
        // instant UI.
        queryClient.invalidateQueries({
          queryKey: listAgentsKey(),
          refetchType: "none",
        });
      },
      onError: (err) => {
        toast.error("Discovery failed", { description: err.message });
      },
    },
  });

  const rescan = useRescanCatalogMetadata({
    mutation: {
      onSuccess: (result) => {
        const r: RescanMetadataResult = result.data;
        const refreshed = r.refreshed ?? 0;
        const unchanged = r.unchanged ?? 0;
        const failed = r.failed ?? 0;
        const skipped = r.skipped ?? 0;
        const parts = [
          `${refreshed} refreshed`,
          `${unchanged} unchanged`,
        ];
        if (failed) parts.push(`${failed} failed`);
        if (skipped) parts.push(`${skipped} skipped`);
        const headline =
          refreshed > 0
            ? "Metadata refreshed"
            : failed > 0
              ? "Rescan completed with issues"
              : "Metadata already up to date";
        (failed > 0 ? toast.warning : toast.success)(headline, {
          description: parts.join(", ") + ".",
        });
        queryClient.invalidateQueries({ queryKey: listAdminCatalogKey() });
        queryClient.invalidateQueries({
          queryKey: listAgentsKey(),
          refetchType: "none",
        });
      },
      onError: (err) => {
        toast.error("Rescan failed", { description: err.message });
      },
    },
  });

  const grant = useGrantCatalogAccess({
    mutation: {
      onSuccess: (result) => {
        const r: GrantAccessResult = result.data;
        const granted = r.granted ?? 0;
        const already = r.already_granted ?? 0;
        const unauth = r.unauthorized ?? 0;
        const failed = r.failed ?? 0;
        const skipped = r.skipped ?? 0;
        const description =
          `${granted} granted, ${already} already set` +
          (unauth ? `, ${unauth} not yours to manage` : "") +
          (failed ? `, ${failed} failed` : "") +
          (skipped ? `, ${skipped} skipped` : "") +
          ".";
        const headline =
          granted > 0
            ? "Access granted"
            : already > 0 && unauth === 0 && failed === 0
              ? "Access already in place"
              : "Grant completed with issues";

        // When we actually granted something, prompt to rescan right
        // away so names update without the admin hunting for the
        // second button.
        if (granted > 0) {
          toast.success(headline, {
            description,
            action: {
              label: "Rescan now",
              onClick: () => rescan.mutate(),
            },
            duration: 8000,
          });
        } else if (unauth > 0 || failed > 0) {
          toast.warning(headline, { description, duration: 8000 });
        } else {
          toast.success(headline, { description });
        }
      },
      onError: (err) => {
        toast.error("Grant failed", { description: err.message });
      },
    },
  });

  const [search, setSearch] = useState("");
  const entries = data?.data ?? [];

  const filtered = useMemo(() => {
    if (!search.trim()) return entries;
    const q = search.toLowerCase();
    return entries.filter(
      (e) =>
        e.endpoint_name.toLowerCase().includes(q) ||
        (e.display_name ?? "").toLowerCase().includes(q),
    );
  }, [entries, search]);

  const visibleCount = entries.filter((e) => e.visible !== false).length;
  const hiddenCount = entries.length - visibleCount;

  const handleToggleVisibility = (entry: CatalogEntryOut) => {
    const next = !(entry.visible ?? true);
    updateEntry(entry.endpoint_name, { visible: next });
    toast.success(next ? "Agent shown" : "Agent hidden", {
      description: entry.display_name ?? entry.endpoint_name,
    });
  };

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
            <LayoutGrid className="h-[18px] w-[18px]" />
          </div>
          <div>
            <h1
              className={[
                "text-[1.75rem] font-bold leading-[1.15] tracking-[-0.025em]",
                "font-[family-name:var(--font-display)] text-text-primary",
              ].join(" ")}
            >
              Catalog Management
            </h1>
            <p className="mt-0.5 text-[0.9375rem] text-text-muted">
              Control which agents appear in the user catalog. Hidden agents
              stay registered but are not shown.
            </p>
            <p className="mt-1 text-[0.75rem] leading-[1.45] text-text-muted">
              New MAS, Agent, KA, External, Genie Space, HTTP Connection, and
              MCP Endpoint entries are visible by default; plain served models
              default to hidden. Per-user access is still enforced by
              Databricks permissions (OBO).
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button
            variant="secondary"
            onClick={() => discover.mutate({ params: {} })}
            disabled={discover.isPending}
          >
            {discover.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            Discover agents
          </Button>
          <Button
            variant="secondary"
            onClick={() => rescan.mutate()}
            disabled={rescan.isPending || grant.isPending}
            title="Re-read Agent Bricks tile details (names, descriptions, sub-agents) for every MAS/KA row."
          >
            {rescan.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )}
            Rescan metadata
          </Button>
          <Button
            onClick={() => grant.mutate()}
            disabled={grant.isPending || rescan.isPending}
            title="Add this app's service principal as a manager on every MAS/KA tile you own, so metadata rescans can read their details."
          >
            {grant.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <KeyRound className="h-4 w-4" />
            )}
            Grant catalog access
          </Button>
        </div>
      </header>

      <TagConfigCard />

      <ManualEndpointsCard />

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[240px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name or endpoint..."
            className={[
              "block h-9 w-full rounded-[var(--radius-md)]",
              "border border-border bg-surface-elevated",
              "pl-9 pr-3 text-[0.875rem] text-text-primary placeholder:text-text-muted",
              "transition-[border-color,box-shadow] duration-150",
              "focus:border-info focus:outline-none focus:ring-2 focus:ring-info/30",
            ].join(" ")}
          />
        </div>
        <div className="flex gap-2">
          <Badge variant="success" shape="pill">
            {visibleCount} visible
          </Badge>
          {hiddenCount > 0 && (
            <Badge variant="default" shape="pill">
              {hiddenCount} hidden
            </Badge>
          )}
        </div>
      </div>

      {isLoading ? (
        <div className="flex h-64 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-text-muted" />
        </div>
      ) : isError ? (
        <Card className="border-error/30 bg-error/5">
          <CardContent className="flex items-center gap-3 p-6 text-sm text-error">
            <ServerCrash className="h-5 w-5" />
            <div className="flex-1">
              Failed to load catalog. Try refreshing or check the backend.
            </div>
            <Button variant="secondary" size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          </CardContent>
        </Card>
      ) : entries.length === 0 ? (
        <EmptyState onDiscover={() => discover.mutate({ params: {} })} isDiscovering={discover.isPending} />
      ) : filtered.length === 0 ? (
        <Card>
          <CardContent className="p-8 text-center text-sm text-text-muted">
            No agents match &quot;{search}&quot;.
          </CardContent>
        </Card>
      ) : (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-[0.875rem]">
              <thead
                className={[
                  "bg-surface text-left",
                  "text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-text-muted",
                ].join(" ")}
              >
                <tr>
                  <th className="px-4 py-3">Display name</th>
                  <th className="px-4 py-3">Endpoint</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Sub-agents</th>
                  <th className="px-4 py-3">Visibility</th>
                  <th className="px-4 py-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {filtered.map((entry) => {
                  const visible = entry.visible !== false;
                  return (
                    <tr
                      key={entry.endpoint_name}
                      className={
                        visible
                          ? "bg-surface-elevated transition-colors duration-150 hover:bg-surface-overlay"
                          : "bg-surface-elevated/40 text-text-muted transition-colors duration-150 hover:bg-surface-elevated"
                      }
                    >
                      <td className="px-4 py-3">
                        <div
                          className={
                            visible
                              ? "font-medium text-text-primary"
                              : "font-medium"
                          }
                        >
                          {entry.display_name || entry.endpoint_name}
                        </div>
                      </td>
                      <td className="px-4 py-3 font-[family-name:var(--font-mono)] text-[0.75rem]">
                        {entry.endpoint_name}
                      </td>
                      <td className="px-4 py-3">
                        <Badge
                          variant={agentTypeVariant(entry.agent_type)}
                          shape="pill"
                        >
                          {agentTypeLabel(entry.agent_type)}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-[0.75rem]">
                        {entry.sub_agent_count ?? 0}
                      </td>
                      <td className="px-4 py-3">
                        {visible ? (
                          <Badge variant="success" shape="pill" className="gap-1">
                            <Eye className="h-3 w-3" /> Visible
                          </Badge>
                        ) : (
                          <Badge variant="default" shape="pill" className="gap-1">
                            <EyeOff className="h-3 w-3" /> Hidden
                          </Badge>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <VisibilityToggle
                          visible={visible}
                          disabled={isUpdating}
                          onToggle={() => handleToggleVisibility(entry)}
                          label={entry.display_name ?? entry.endpoint_name}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

/*
 * iOS-style toggle switch for the visibility column. We avoid radix/UI kits
 * here because we want the compact, tactile feel of a native iOS switch
 * without extra dependencies.
 */
function VisibilityToggle({
  visible,
  disabled,
  onToggle,
  label,
}: {
  visible: boolean;
  disabled: boolean;
  onToggle: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={visible}
      aria-label={`${visible ? "Hide" : "Show"} ${label}`}
      onClick={onToggle}
      disabled={disabled}
      className={[
        "relative inline-flex h-[28px] w-[48px] shrink-0 items-center rounded-full",
        "transition-[background-color] duration-200",
        "focus-visible:outline-none focus-visible:ring-2",
        "focus-visible:ring-info focus-visible:ring-offset-2",
        "focus-visible:ring-offset-surface-elevated",
        "disabled:cursor-not-allowed disabled:opacity-50",
        visible ? "bg-success" : "bg-surface-overlay",
      ].join(" ")}
    >
      <span
        aria-hidden="true"
        className={[
          "inline-block h-[24px] w-[24px] transform rounded-full bg-white",
          "shadow-[0_1px_2px_0_oklch(0_0_0/0.15)]",
          "transition-transform duration-200",
          visible ? "translate-x-[22px]" : "translate-x-[2px]",
        ].join(" ")}
      />
    </button>
  );
}

function EmptyState({
  onDiscover,
  isDiscovering,
}: {
  onDiscover: () => void;
  isDiscovering: boolean;
}) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center gap-4 py-16 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
          <LayoutGrid className="h-6 w-6" />
        </div>
        <div>
          <h3
            className={[
              "text-[1.0625rem] font-semibold tracking-[-0.01em]",
              "font-[family-name:var(--font-display)] text-text-primary",
            ].join(" ")}
          >
            No agents yet
          </h3>
          <p className="mt-1 max-w-sm text-[0.875rem] leading-[1.45] text-text-muted">
            Run discovery to scan your workspace for MAS serving endpoints.
            They will appear here once registered.
          </p>
        </div>
        <Button onClick={onDiscover} disabled={isDiscovering}>
          {isDiscovering ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
          Discover agents
        </Button>
      </CardContent>
    </Card>
  );
}
