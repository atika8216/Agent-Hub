import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";

import { EChartCard } from "./echart-card";
import {
  useGetMessageChart,
  useListMessageCharts,
  type ChartArtifactOut,
} from "@/lib/api";
import { useChartsFor, useChatStore } from "@/stores/chat-store";
import type { ChartArtifact, ChartKind } from "@/lib/types";

// The backend persists columns as ``[{name, type}, ...]`` dicts (the
// structured form is what ``pick_chart`` needs). The frontend artifact
// only needs the display names, so we flatten at the rehydrate boundary
// -- keeping ``ChartArtifact.columns`` a plain ``string[]`` means the
// EChartCard table header and CSV export stay trivial. We accept both
// shapes defensively so a future backend change (or an old cached
// artifact) doesn't crash the UI.
type FetchedColumn = string | { name?: unknown; type?: unknown } | null | undefined;
function flattenColumnNames(cols: unknown): string[] {
  if (!Array.isArray(cols)) return [];
  return cols.map((c: FetchedColumn) => {
    if (typeof c === "string") return c;
    if (c && typeof c === "object" && "name" in c) {
      const name = (c as { name?: unknown }).name;
      return typeof name === "string" ? name : "";
    }
    return "";
  });
}

function toArtifact(out: ChartArtifactOut): ChartArtifact {
  return {
    chart_id: out.chart_id,
    message_id: out.message_id,
    conversation_id: out.conversation_id,
    chart_kind: out.chart_kind as ChartKind,
    title: out.title,
    option: out.option,
    columns: flattenColumnNames(out.columns),
    rows: out.rows,
    truncated: out.truncated,
    idx: typeof out.idx === "number" ? out.idx : 0,
    created_at: out.created_at,
  };
}

interface ChartHydratorProps {
  messageId: string;
  // Streaming-time artifacts (chart_id known, rows empty). When non-empty
  // we render off the live list immediately and let the lazy fetch fill
  // in the missing ``rows`` in the background so CSV / table view works
  // the moment the user asks for it.
  initial: ChartArtifact[];
  // Hint from the conversation reload payload (``MessageOut.chart_count``).
  // When > 1 we jump straight to the list endpoint. When 1 we keep the
  // cheaper single-chart fetch for back-compat. When 0 / undefined we
  // only render from the live store and skip fetching altogether.
  expectedCount?: number;
}

/*
 * Per-message wrapper that:
 *   1. If live artifacts exist in the store (from SSE), renders them
 *      immediately.
 *   2. In parallel (or instead, on reload), calls the appropriate list /
 *      single endpoint to pull the full rows+columns and stuff them into
 *      the chat store -- after which the stack below reads from the same
 *      Zustand slice as the live chart path.
 *   3. When more than one chart is attached, renders a compact
 *      "Chart N of M" caption above each card plus a top rail of numbered
 *      chips that scroll the picked card into view. Keeps the stack
 *      navigable when Genie returns 4+ follow-up drill-downs without
 *      reaching for a carousel.
 */
export function ChartHydrator({
  messageId,
  initial,
  expectedCount,
}: ChartHydratorProps) {
  const setChartsForMessage = useChatStore((s) => s.setChartsForMessage);
  const live = useChartsFor(messageId);

  // Pick which rehydrate endpoint to call. We key the decision on the
  // *current* count (live + server hint) so we don't call the list
  // endpoint for single-chart messages.
  const shouldFetchList = (expectedCount ?? 0) > 1;
  const shouldFetchSingle =
    !shouldFetchList && (expectedCount ?? 0) >= 1 && live.length === 0;

  const listQuery = useListMessageCharts({
    params: { message_id: messageId },
    query: {
      enabled: shouldFetchList,
      retry: false,
      staleTime: Infinity,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    },
  });

  const singleQuery = useGetMessageChart({
    params: { message_id: messageId },
    query: {
      enabled: shouldFetchSingle,
      retry: false,
      staleTime: Infinity,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    },
  });

  const fetchedList = listQuery.data?.data.charts;
  const fetchedSingle = singleQuery.data?.data;

  useEffect(() => {
    if (fetchedList && fetchedList.length > 0) {
      setChartsForMessage(
        messageId,
        fetchedList.map(toArtifact),
      );
    }
  }, [fetchedList, messageId, setChartsForMessage]);

  useEffect(() => {
    if (fetchedSingle) {
      setChartsForMessage(messageId, [toArtifact(fetchedSingle)]);
    }
  }, [fetchedSingle, messageId, setChartsForMessage]);

  // Resolution order: live (zustand) > fetched list > fetched single >
  // streaming ``initial`` > nothing. ``live`` is already the authoritative
  // list once either fetch has updated the store, so we only fall back
  // to the raw fetched data during the brief gap before the effect runs.
  const artifacts: ChartArtifact[] = useMemo(() => {
    if (live.length > 0) return live;
    if (fetchedList && fetchedList.length > 0) {
      return fetchedList.map(toArtifact);
    }
    if (fetchedSingle) return [toArtifact(fetchedSingle)];
    if (initial.length > 0) return initial;
    return [];
  }, [live, fetchedList, fetchedSingle, initial]);

  if (artifacts.length === 0) {
    const loading = listQuery.isLoading || singleQuery.isLoading;
    if (loading) {
      return (
        <div className="rounded-[var(--radius-lg)] border border-border bg-surface-elevated px-4 py-6">
          <div className="flex items-center justify-center text-text-muted">
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        </div>
      );
    }
    return null;
  }

  if (artifacts.length === 1) {
    return <EChartCard artifact={artifacts[0]} />;
  }

  return <ChartStack messageId={messageId} artifacts={artifacts} />;
}

function ChartStack({
  messageId,
  artifacts,
}: {
  messageId: string;
  artifacts: ChartArtifact[];
}) {
  const [activeIdx, setActiveIdx] = useState(0);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Scroll the picked chart into view when the user taps a chip. The
  // stack lives inside the assistant bubble column so we scope the
  // query to our container -- don't fight the outer ``useScrollAnchor``
  // here.
  useEffect(() => {
    const el = containerRef.current?.querySelector<HTMLElement>(
      `[data-chart-slot="${activeIdx}"]`,
    );
    if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activeIdx]);

  const total = artifacts.length;

  return (
    <div ref={containerRef} className="flex flex-col gap-3">
      {/* Rail of numbered chips. Small, tactile, stays visually quiet
          when the user isn't interacting. */}
      <div
        className="flex flex-wrap items-center gap-1.5 text-[0.75rem] text-text-muted"
        role="tablist"
        aria-label={`${total} charts in this response`}
      >
        <span className="mr-1">Charts:</span>
        {artifacts.map((artifact, i) => {
          const isActive = i === activeIdx;
          return (
            <button
              key={artifact.chart_id || `chart-${messageId}-${i}`}
              type="button"
              role="tab"
              aria-selected={isActive}
              aria-controls={`chart-${messageId}-panel-${i}`}
              onClick={() => setActiveIdx(i)}
              className={[
                "min-w-[1.75rem] rounded-full px-2 py-0.5 text-[0.75rem]",
                "transition-colors focus-visible:outline-none",
                "focus-visible:ring-2 focus-visible:ring-info/40",
                isActive
                  ? "bg-info text-white"
                  : "bg-surface-elevated text-text-secondary hover:bg-surface-overlay",
              ].join(" ")}
              title={artifact.title || `Chart ${i + 1}`}
            >
              {i + 1}
            </button>
          );
        })}
      </div>

      <div className="flex flex-col gap-3">
        {artifacts.map((artifact, i) => (
          <div
            key={artifact.chart_id || `chart-${messageId}-${i}`}
            id={`chart-${messageId}-panel-${i}`}
            data-chart-slot={i}
            className="flex flex-col gap-1"
          >
            <div className="px-1 text-[0.75rem] text-text-muted">
              Chart {i + 1} of {total}
              {artifact.title ? ` · ${artifact.title}` : ""}
            </div>
            <EChartCard artifact={artifact} />
          </div>
        ))}
      </div>
    </div>
  );
}
