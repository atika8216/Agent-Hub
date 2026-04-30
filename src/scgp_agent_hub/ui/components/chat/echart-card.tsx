import { Suspense, lazy, memo, useCallback, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  BarChart3,
  Download,
  ImageDown,
  Loader2,
  Maximize2,
  Table2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { useTheme } from "@/providers/theme-provider";
import type { ChartArtifact } from "@/lib/types";

// Lazy-load the (heavy) ECharts react wrapper *and* the core lib at the
// boundary of this component so users who never see a Genie chart pay
// 0 KB for the ~700 KB echarts bundle. The dynamic import is keyed on
// the wrapper rather than ``echarts/core`` because the wrapper imports
// the full registry; sniping individual chart kinds is a follow-up
// optimization once we know which ones get used in practice.
const ReactECharts = lazy(() =>
  import("echarts-for-react").then((m) => ({ default: m.default })),
);

// Shape of the instance handle the wrapper hands us via ``onChartReady``.
// Kept loose on purpose: ``echarts-for-react`` hands back ``EChartsType``
// whose ``getDataURL`` accepts ``"svg" | "png" | "jpeg"`` and a wider
// ``backgroundColor`` union. We only read ``getDataURL``, so we declare
// it as an unknown-args callable returning ``string`` -- the structural
// check at the one call site stays the same without forcing a cast.
type ECInstance = {
  getDataURL: (options?: Record<string, unknown>) => string;
};

interface EChartCardProps {
  artifact: ChartArtifact;
}

/*
 * Renders a Genie SQL result as an Apache ECharts visualization. The
 * backend already builds the full ``option`` (tooltip, dataZoom,
 * legend, toolbox, brush) so this component is intentionally thin —
 * its job is theme integration, the table-mode toggle, CSV / PNG
 * export, and a true full-screen expand modal. Drill-down is pure
 * ECharts (axisPointer / brush / restore via the toolbox) so no
 * extra round-trips.
 *
 * The card uses a 16:9 aspect-ratio constraint instead of a hard
 * pixel height so wide layouts breathe and narrow bars don't get
 * crushed. Full-screen expand swaps to a Radix Dialog so it can
 * actually fill the viewport -- the prior in-card 320→480 toggle
 * felt meaningless on anything bigger than an iPad.
 */
export const EChartCard = memo(function EChartCard({
  artifact,
}: EChartCardProps) {
  const [mode, setMode] = useState<"chart" | "table">(
    artifact.chart_kind === "table" ? "table" : "chart",
  );
  const [fullscreen, setFullscreen] = useState(false);

  return (
    <>
      <ChartCardShell
        artifact={artifact}
        mode={mode}
        setMode={setMode}
        onExpand={() => setFullscreen(true)}
      />
      {fullscreen && (
        <FullscreenChartDialog
          artifact={artifact}
          mode={mode}
          setMode={setMode}
          onClose={() => setFullscreen(false)}
        />
      )}
    </>
  );
});

interface ShellProps {
  artifact: ChartArtifact;
  mode: "chart" | "table";
  setMode: (next: "chart" | "table") => void;
  onExpand?: () => void;
  // When rendered inside the fullscreen dialog, the body fills the
  // viewport instead of using the 16:9 aspect-ratio constraint.
  fullscreen?: boolean;
}

function ChartCardShell({
  artifact,
  mode,
  setMode,
  onExpand,
  fullscreen = false,
}: ShellProps) {
  const { resolved } = useTheme();
  const echartsTheme = resolved === "dark" ? "dark" : undefined;
  const chartInstanceRef = useRef<ECInstance | null>(null);

  const isTableKind = artifact.chart_kind === "table";
  const option = useMemo(() => normalizeLegacyOption(artifact.option), [artifact.option]);

  // ECharts' ``saveAsImage`` toolbox feature works but uses the current
  // canvas background which is transparent by default. We resolve a
  // solid backdrop that matches the surrounding surface so the export
  // looks right in light *and* dark mode without the user having to
  // flip the toolbox. Matches the Clarity card background tokens.
  const resolveBackground = useCallback(() => {
    if (typeof window === "undefined") return "#ffffff";
    const rootStyles = window.getComputedStyle(document.documentElement);
    const surface = rootStyles.getPropertyValue("--color-surface").trim();
    return surface || (resolved === "dark" ? "#0f0f10" : "#ffffff");
  }, [resolved]);

  const downloadPng = useCallback(() => {
    const chart = chartInstanceRef.current;
    if (!chart) return;
    const url = chart.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: resolveBackground(),
    });
    if (!url) return;
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slugify(artifact.title || "chart")}-${artifact.chart_id.slice(0, 8)}.png`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, [artifact.chart_id, artifact.title, resolveBackground]);

  const downloadCsv = useCallback(() => {
    const cols = artifact.columns;
    const lines: string[] = [];
    lines.push(cols.map(escapeCsv).join(","));
    for (const row of artifact.rows) {
      lines.push(row.map((cell) => escapeCsv(formatCell(cell))).join(","));
    }
    const blob = new Blob([lines.join("\n")], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slugify(artifact.title || "chart")}-${artifact.chart_id.slice(0, 8)}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [artifact]);

  const chartBodyStyle = fullscreen
    ? { width: "100%", height: "100%" }
    : { width: "100%", height: "100%" };

  // Container sizing: in-card uses 16:9 aspect ratio so narrow layouts
  // don't crush the canvas; fullscreen lets flexbox stretch it to fill.
  const chartBoxClass = fullscreen
    ? "relative flex-1 min-h-0 w-full"
    : "relative w-full [aspect-ratio:16/9] min-h-[240px]";

  return (
    <div
      className={[
        "rounded-[var(--radius-lg)] border border-border",
        "bg-surface-elevated text-text-primary",
        "overflow-hidden",
        // Fullscreen uses a flex column so the chart fills the remaining
        // viewport space after the header.
        fullscreen ? "flex h-full flex-col" : "",
      ].join(" ")}
    >
      <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-3.5 w-3.5 shrink-0 text-info" />
            <h4
              className={[
                "truncate text-[0.875rem] font-semibold",
                "font-[family-name:var(--font-display)] text-text-primary",
              ].join(" ")}
              title={artifact.title}
            >
              {artifact.title || "Result"}
            </h4>
          </div>
          <p className="mt-0.5 text-[0.75rem] text-text-muted">
            {artifact.rows.length.toLocaleString()} row
            {artifact.rows.length === 1 ? "" : "s"} ·{" "}
            {capitalize(artifact.chart_kind)}
            {artifact.truncated ? " · truncated" : ""}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {!isTableKind && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setMode(mode === "chart" ? "table" : "chart")}
              title={mode === "chart" ? "View as table" : "View as chart"}
            >
              {mode === "chart" ? (
                <>
                  <Table2 className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Table</span>
                </>
              ) : (
                <>
                  <BarChart3 className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Chart</span>
                </>
              )}
            </Button>
          )}
          {!isTableKind && mode === "chart" && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={downloadPng}
              title="Download PNG"
              aria-label="Download PNG"
            >
              <ImageDown className="h-3.5 w-3.5" />
            </Button>
          )}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={downloadCsv}
            title="Download CSV"
            aria-label="Download CSV"
          >
            <Download className="h-3.5 w-3.5" />
          </Button>
          {!fullscreen && !isTableKind && mode === "chart" && onExpand && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={onExpand}
              title="Expand"
              aria-label="Expand"
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </header>

      <div className={fullscreen ? "flex flex-1 min-h-0 bg-surface" : "bg-surface"}>
        {mode === "chart" && !isTableKind ? (
          <div className={chartBoxClass}>
            <Suspense
              fallback={
                <div className="flex h-full w-full items-center justify-center text-text-muted">
                  <Loader2 className="h-5 w-5 animate-spin" />
                </div>
              }
            >
              <ReactECharts
                option={option}
                theme={echartsTheme}
                notMerge
                lazyUpdate
                style={chartBodyStyle}
                opts={{ renderer: "canvas" }}
                onChartReady={(instance: ECInstance) => {
                  chartInstanceRef.current = instance;
                }}
              />
            </Suspense>
          </div>
        ) : (
          <ResultTable artifact={artifact} fullscreen={fullscreen} />
        )}
      </div>

      {artifact.truncated && !fullscreen && (
        <p className="border-t border-border px-4 py-2 text-[0.6875rem] text-text-muted">
          Result was truncated to keep the page snappy. Re-run with a
          tighter filter to see the full set.
        </p>
      )}
    </div>
  );
}

/*
 * Full-screen Radix Dialog: 100vw × 100vh with inner padding. Preserves
 * table mode when the user was in table view before expanding. Esc /
 * overlay click closes (free via Radix); the close button is focusable
 * alongside the header actions so Tab cycles cleanly. Focus trap is
 * handled automatically by Radix.
 */
function FullscreenChartDialog({
  artifact,
  mode,
  setMode,
  onClose,
}: {
  artifact: ChartArtifact;
  mode: "chart" | "table";
  setMode: (next: "chart" | "table") => void;
  onClose: () => void;
}) {
  return (
    <Dialog.Root open onOpenChange={(next) => (next ? null : onClose())}>
      <Dialog.Portal>
        <Dialog.Overlay
          className={[
            "fixed inset-0 z-[80] bg-black/40",
            "backdrop-blur-sm",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0",
            "data-[state=closed]:animate-out data-[state=closed]:fade-out-0",
          ].join(" ")}
        />
        <Dialog.Content
          className={[
            "fixed inset-0 z-[90] flex flex-col p-6 outline-none",
            "data-[state=open]:animate-in data-[state=open]:zoom-in-[0.98]",
          ].join(" ")}
          aria-describedby={undefined}
        >
          <Dialog.Title className="sr-only">
            {artifact.title || "Chart"}
          </Dialog.Title>
          <div className="flex flex-1 min-h-0 flex-col overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface-elevated shadow-[0_20px_40px_-24px_oklch(0_0_0/0.4)]">
            <div className="relative flex flex-1 min-h-0 flex-col">
              <div className="absolute right-3 top-3 z-10">
                <Dialog.Close asChild>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label="Close full-screen"
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </Dialog.Close>
              </div>
              <ChartCardShell
                artifact={artifact}
                mode={mode}
                setMode={setMode}
                fullscreen
              />
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/*
 * Compact tabular view for the "View as table" toggle and the
 * ``chart_kind: "table"`` fallback path. Keeps the same surface
 * styling as the chart so the toggle reads as a mode flip rather
 * than a different component. In fullscreen mode the table expands
 * to the available height instead of the per-card 480 px cap.
 */
function ResultTable({
  artifact,
  fullscreen,
}: {
  artifact: ChartArtifact;
  fullscreen: boolean;
}) {
  const cols = artifact.columns;
  const rows = artifact.rows;

  if (!rows.length) {
    return (
      <p className="px-4 py-6 text-center text-[0.8125rem] text-text-muted">
        No rows returned.
      </p>
    );
  }

  return (
    <div
      className={[
        "overflow-auto",
        fullscreen ? "flex-1 min-h-0" : "max-h-[480px]",
      ].join(" ")}
    >
      <table className="w-full border-collapse text-[0.8125rem]">
        <thead className="sticky top-0 bg-surface-elevated">
          <tr>
            {cols.map((c) => (
              <th
                key={c}
                className={[
                  "border-b border-border px-3 py-2 text-left font-medium",
                  "whitespace-nowrap text-text-secondary",
                ].join(" ")}
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr
              key={ri}
              className={[
                "border-b border-border/60",
                ri % 2 === 0 ? "" : "bg-surface-elevated/40",
              ].join(" ")}
            >
              {row.map((cell, ci) => (
                <td
                  key={ci}
                  className="px-3 py-1.5 align-top text-text-primary"
                >
                  {formatCell(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// -- helpers --

/*
 * Charts persisted before the bottom-strip fix had ``legend.top: "bottom"``
 * plus ``grid.bottom: 56``, which carved a short band under the plot that
 * read as a second mini-chart. New charts are built with the legend anchored
 * above the plot (see ``chart_service.py``), but anything already stored in
 * ``chart_artifacts.option_json`` would otherwise keep the old layout on
 * reload. This pure function rewrites the option at the render boundary so
 * both code paths produce the same visual result -- no DB migration needed.
 * Handles the object form of ``legend`` and the rarely-used array form.
 */
function normalizeLegacyOption(option: unknown): unknown {
  if (!option || typeof option !== "object") return option;
  const src = option as Record<string, unknown>;

  let mutated = false;
  const out: Record<string, unknown> = { ...src };

  const rewriteLegend = (leg: Record<string, unknown>): Record<string, unknown> => {
    if (leg.top === "bottom") {
      mutated = true;
      const { top: _drop, ...rest } = leg;
      return { ...rest, top: 28, left: "center", orient: "horizontal" };
    }
    return leg;
  };

  const legend = src.legend;
  if (legend && typeof legend === "object") {
    if (Array.isArray(legend)) {
      const nextArr = (legend as unknown[]).map((entry) =>
        entry && typeof entry === "object"
          ? rewriteLegend(entry as Record<string, unknown>)
          : entry,
      );
      if (mutated) out.legend = nextArr;
    } else {
      const nextLegend = rewriteLegend(legend as Record<string, unknown>);
      if (mutated) out.legend = nextLegend;
    }
  }

  const grid = src.grid;
  if (grid && typeof grid === "object" && !Array.isArray(grid)) {
    const g = grid as Record<string, unknown>;
    const needsGridFix = g.top === 48 || g.bottom === 56;
    if (needsGridFix) {
      mutated = true;
      out.grid = { ...g, top: 64, bottom: 32 };
    }
  }

  return mutated ? out : option;
}

function escapeCsv(value: string): string {
  if (value == null) return "";
  // RFC 4180: wrap in quotes when the cell contains a comma, quote, or
  // newline. Double up any embedded quotes.
  const needsQuotes = /[",\n\r]/.test(value);
  const escaped = value.replace(/"/g, '""');
  return needsQuotes ? `"${escaped}"` : escaped;
}

function formatCell(value: string | number | boolean | null): string {
  if (value == null) return "";
  if (typeof value === "number") {
    // Show large counts with thousand separators; small numbers as-is.
    return Number.isInteger(value)
      ? value.toLocaleString()
      : Number.isFinite(value)
        ? String(value)
        : "";
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "chart";
}

function capitalize(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}
