import { useState, useMemo } from "react";
import { createFileRoute } from "@tanstack/react-router";

import { useAgents, useGenieSpaces } from "@/hooks/use-agents";
import { AgentCard } from "@/components/catalog/agent-card";
import { AgentCardSkeletonGrid } from "@/components/catalog/agent-skeleton";
import { EmptyCatalog } from "@/components/catalog/empty-catalog";
import { GenieSpaceCard } from "@/components/catalog/genie-space-card";
import { SearchInput } from "@/components/catalog/search-input";
import type { GenieSpace } from "@/lib/types";

export const Route = createFileRoute("/_sidebar/catalog/")({
  component: CatalogPage,
});

const FILTER_OPTIONS = [
  "All",
  "Supervisor Agent",
  "Knowledge Assistant",
  "Custom Agent Endpoint",
  "Genie Space",
  "HTTP Connection",
  "MCP Endpoint",
  "External Model",
  "Accessible",
] as const;
type Filter = (typeof FILTER_OPTIONS)[number];

const TYPE_FILTERS: Partial<Record<Filter, string>> = {
  "Supervisor Agent": "MAS",
  "Knowledge Assistant": "KA",
  "Custom Agent Endpoint": "AGENT",
  "Genie Space": "GENIE_SPACE",
  "HTTP Connection": "HTTP_CONNECTION",
  "MCP Endpoint": "MCP_ENDPOINT",
  "External Model": "EXTERNAL",
};

function CatalogPage() {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("All");

  const { data: agentsData, isLoading, isError } = useAgents();
  const { data: genieData } = useGenieSpaces();

  const genieSpaces: GenieSpace[] = useMemo(() => {
    const list = genieData?.spaces ?? [];
    // Backend returns `GenieSpaceSummary[]` shape which matches `GenieSpace`.
    return list as GenieSpace[];
  }, [genieData]);

  const filteredAgents = useMemo(() => {
    let list = agentsData?.agents ?? [];

    if (search) {
      const q = search.toLowerCase();
      list = list.filter(
        (a) =>
          a.display_name.toLowerCase().includes(q) ||
          (a.description ?? "").toLowerCase().includes(q) ||
          a.endpoint_name.toLowerCase().includes(q)
      );
    }

    const typeFilter = TYPE_FILTERS[filter];
    if (typeFilter === "GENIE_SPACE") {
      // Genie spaces are rendered from the separate list, not `agents`.
      return [];
    }
    if (typeFilter) {
      list = list.filter(
        (a) => (a.agent_type ?? "").toString().toUpperCase() === typeFilter,
      );
    } else if (filter === "Accessible") {
      list = list.filter((a) => a.has_access);
    }

    return list;
  }, [agentsData, search, filter]);

  const filteredGenieSpaces = useMemo(() => {
    const typeFilter = TYPE_FILTERS[filter];
    // Show Genie Spaces when filter is "All", "Genie Space", or "Accessible"
    // (all returned spaces are scoped to what the user can already see via
    // OBO, so "Accessible" is a no-op here).
    const showGenie =
      filter === "All" || filter === "Genie Space" || filter === "Accessible";
    if (!showGenie) return [];
    if (typeFilter && typeFilter !== "GENIE_SPACE") return [];

    let list = genieSpaces;
    if (search) {
      const q = search.toLowerCase();
      list = list.filter(
        (s) =>
          s.title.toLowerCase().includes(q) ||
          (s.description ?? "").toLowerCase().includes(q) ||
          s.space_id.toLowerCase().includes(q),
      );
    }
    return list;
  }, [genieSpaces, search, filter]);

  const totalResults = filteredAgents.length + filteredGenieSpaces.length;
  const hasSearch = !!(search || filter !== "All");

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-6 md:p-8">
      <header className="space-y-1">
        <h1
          className={[
            "text-[1.75rem] font-bold leading-[1.15] tracking-[-0.025em]",
            "font-[family-name:var(--font-display)] text-text-primary",
          ].join(" ")}
        >
          Agent Catalog
        </h1>
        <p className="text-[0.9375rem] text-text-secondary">
          Browse and connect with available AI agents.
        </p>
      </header>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="w-full sm:max-w-xs">
          <SearchInput value={search} onChange={setSearch} />
        </div>
        {/* Mobile: horizontally scrollable iOS-style chip row. Desktop:
            classic segmented tablist. The two share the same buttons via
            the FILTER_OPTIONS loop, only the wrapper layout differs. */}
        <div
          role="tablist"
          aria-label="Filter agents by type"
          className={[
            // Mobile: scroll-snap chip rail with an edge fade.
            "flex gap-1.5 overflow-x-auto snap-x snap-mandatory",
            "pb-1 -mb-1",
            "[scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
            "px-1 py-1",
            // Desktop: boxed segmented tablist.
            "sm:overflow-visible sm:snap-none sm:flex-wrap",
            "sm:rounded-[var(--radius-md)] sm:border sm:border-border",
            "sm:bg-surface-elevated sm:p-1 sm:gap-1 sm:pb-1 sm:mb-0",
          ].join(" ")}
        >
          {FILTER_OPTIONS.map((opt) => {
            const active = filter === opt;
            return (
              <button
                key={opt}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setFilter(opt)}
                className={[
                  "snap-start shrink-0",
                  // Mobile: 36px chip in an 8px-padded rail -> 44px tap
                  // target from outer edge to outer edge. Desktop keeps
                  // the tighter 28px segmented tab.
                  "h-9 px-4 rounded-full",
                  "sm:h-7 sm:rounded-[var(--radius-sm)] sm:px-3",
                  "text-[0.8125rem] font-medium whitespace-nowrap sm:text-[0.75rem]",
                  "press-tappable",
                  "focus-visible:outline-none focus-visible:ring-2",
                  "focus-visible:ring-info focus-visible:ring-offset-2",
                  "focus-visible:ring-offset-background",
                  "sm:focus-visible:ring-offset-surface-elevated",
                  active
                    ? [
                        // Mobile: filled brand chip.
                        "bg-primary text-primary-foreground border border-primary/30",
                        // Desktop: raised segmented tab.
                        "sm:bg-surface sm:text-text-primary sm:border-0",
                        "sm:shadow-[0_1px_2px_0_oklch(0_0_0/0.08)]",
                        "dark:sm:shadow-[inset_0_1px_0_oklch(1_0_0/0.06)]",
                      ].join(" ")
                    : [
                        // Mobile: hairline chip.
                        "bg-surface-elevated text-text-secondary border border-border",
                        "hover:text-text-primary",
                        // Desktop: ghost segment.
                        "sm:bg-transparent sm:border-0",
                      ].join(" "),
                ].join(" ")}
              >
                {opt}
              </button>
            );
          })}
        </div>
      </div>

      {isLoading ? (
        <AgentCardSkeletonGrid />
      ) : isError ? (
        <div className="py-12 text-center text-[0.9375rem] text-error">
          Failed to load agents. Please try again.
        </div>
      ) : totalResults === 0 ? (
        <EmptyCatalog hasSearch={hasSearch} />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {filteredAgents.map((agent, idx) => (
            <div
              key={agent.endpoint_name}
              className="stagger-child"
              // Cap the stagger delay at 10 children so the last card still
              // lands within ~400ms even on a long page.
              style={{ ["--i" as string]: Math.min(idx, 9) }}
            >
              <AgentCard agent={agent} />
            </div>
          ))}
          {filteredGenieSpaces.map((space, idx) => (
            <div
              key={`genie:${space.space_id}`}
              className="stagger-child"
              style={{
                ["--i" as string]: Math.min(filteredAgents.length + idx, 9),
              }}
            >
              <GenieSpaceCard space={space} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
