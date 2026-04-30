import { memo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  Wrench,
} from "lucide-react";

import type { ChatTimelineEvent } from "@/lib/types";

interface ToolCallBlockProps {
  callEvent: Extract<ChatTimelineEvent, { kind: "tool_call" }>;
  resultEvent?: Extract<ChatTimelineEvent, { kind: "tool_result" }>;
}

/*
 * iOS-style tool-call affordance.
 *
 * Collapsed: a compact chip that fits on one line -- status glyph + tool
 * name + single-word outcome. Looks like an iMessage receipt ("sent",
 * "delivered") rather than a heavy card, so a long assistant turn with
 * several tool calls doesn't feel noisy.
 *
 * Expanded: flips to a full-width card with the tool's arguments
 * pretty-printed. Only renders the heavier card shape when the user
 * actually wants the detail.
 *
 * No colored side-stripe borders anywhere (per the Clarity design
 * charter). Depth is carried by a single 1 px hairline border; no
 * inset shadow gymnastics.
 */
export const ToolCallBlock = memo(function ToolCallBlock({
  callEvent,
  resultEvent,
}: ToolCallBlockProps) {
  const [expanded, setExpanded] = useState(false);
  const isError = resultEvent?.is_error === true;
  const isPending = resultEvent == null;

  const statusLabel = isPending ? "Running" : isError ? "Failed" : "Done";
  const StatusIcon = isPending ? Loader2 : isError ? AlertCircle : CheckCircle2;
  const statusTone = isPending
    ? "text-info"
    : isError
      ? "text-error"
      : "text-success";

  const hasInput = Object.keys(callEvent.input ?? {}).length > 0;

  // Collapsed: single-row pill.
  if (!expanded) {
    return (
      <button
        type="button"
        onClick={() => setExpanded(true)}
        aria-expanded={false}
        className={[
          "inline-flex max-w-full items-center gap-1.5",
          "rounded-full border border-border bg-surface-elevated",
          "px-3 py-1.5",
          "text-[0.8125rem] text-text-primary",
          "transition-colors hover:bg-surface-overlay",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info/40",
        ].join(" ")}
        title={`${callEvent.name} — ${statusLabel}`}
      >
        <StatusIcon
          className={[
            "h-3.5 w-3.5 shrink-0",
            statusTone,
            isPending ? "animate-spin" : "",
          ].join(" ")}
        />
        <Wrench className="h-3 w-3 shrink-0 text-text-muted" aria-hidden="true" />
        <span className="truncate font-medium">{callEvent.name}</span>
        <span className={`text-[0.75rem] ${statusTone}`}>· {statusLabel}</span>
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-text-muted" />
      </button>
    );
  }

  // Expanded: full card.
  return (
    <div
      className={[
        "rounded-[var(--radius-lg)] border border-border",
        "bg-surface-elevated overflow-hidden",
      ].join(" ")}
    >
      <button
        type="button"
        onClick={() => setExpanded(false)}
        className={[
          "flex w-full items-center gap-2 px-4 py-3 text-left",
          "transition-colors hover:bg-surface-overlay/50",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info/40",
        ].join(" ")}
        aria-expanded
      >
        <span
          className={[
            "flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
            "bg-surface-overlay",
            statusTone,
          ].join(" ")}
        >
          <StatusIcon
            className={[
              "h-3.5 w-3.5",
              isPending ? "animate-spin" : "",
            ].join(" ")}
          />
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-[0.875rem] font-medium text-text-primary">
            {callEvent.name}
          </span>
          <span className={`text-[0.75rem] ${statusTone}`}>{statusLabel}</span>
        </span>
        <ChevronDown className="h-4 w-4 text-text-muted" />
      </button>

      <div className="border-t border-border px-4 py-3 text-[0.8125rem] text-text-muted">
        {hasInput ? (
          <div className="space-y-1.5">
            <div className="text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-text-muted">
              Input
            </div>
            <pre
              className={[
                "max-h-48 overflow-auto",
                "rounded-[var(--radius-md)] border border-border",
                "bg-surface-recessed px-3 py-2",
                "font-mono text-[0.75rem] leading-[1.4] text-text-primary",
              ].join(" ")}
            >
              {JSON.stringify(callEvent.input, null, 2)}
            </pre>
          </div>
        ) : (
          <div className="italic">No arguments</div>
        )}
      </div>
    </div>
  );
});
