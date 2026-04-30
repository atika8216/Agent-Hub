import { memo, useCallback, useState } from "react";
import { Bot, Check, Copy, Pin } from "lucide-react";
import { motion } from "motion/react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";

import { MarkdownRenderer } from "./markdown-renderer";
import { ChartHydrator } from "./chart-hydrator";
import { useChartsFor, useChatStore } from "@/stores/chat-store";
import { useTheme } from "@/providers/theme-provider";
import { bubbleVariants } from "@/lib/motion";
import type { Message } from "@/lib/types";
import { listPinsKey, useCreatePin } from "@/lib/api";

/*
 * Two message surfaces that intentionally read very differently:
 *
 *   - User messages: a small muted pill on the right. Asymmetric corner
 *     on the tail side. Quiet enough to read as "just what I asked" --
 *     the loud brand-red version hijacked the eye and pushed the
 *     assistant answer into a secondary role.
 *
 *   - Assistant messages: a document-flow "hairline card" -- a 2px left
 *     accent line + generous pl-4 padding, with a tiny Bot glyph +
 *     "Assistant" label above. No fill, no border box, no right-hand
 *     constraint. Markdown renders as real body copy with H1/H2/H3
 *     rhythm; any chart attachments sit at the top of the same hairline
 *     column so a multi-part answer reads as one grouped artifact.
 *
 * A hover-only "Copy" action sits below assistant answers; the
 * existing "Pin" action sits to the left of user bubbles.
 */
export const MessageBubble = memo(function MessageBubble({
  message,
  layoutId,
}: {
  message: Message;
  /*
   * When provided, the bubble participates in a shared-layout
   * transition. The chat routes pass ``stream-{conversationId}`` to
   * the most-recent assistant bubble *only when no stream is active*,
   * so motion can morph the StreamingMessage placeholder into this
   * final hairline column without a flash. See ``streaming-message.tsx``.
   */
  layoutId?: string;
}) {
  const isUser = message.role === "user";
  const { featureFlags } = useTheme();
  const endpointName = useChatStore((s) => s.endpointName);
  const queryClient = useQueryClient();
  // ``useChartsFor`` must be called unconditionally to satisfy the
  // Rules of Hooks (the user-bubble branch returns early below). The
  // result is ignored on the user branch.
  const live = useChartsFor(message.id);

  const pinMutation = useCreatePin({
    mutation: {
      onSuccess: () => {
        if (endpointName) {
          queryClient.invalidateQueries({
            queryKey: listPinsKey({ endpoint_name: endpointName }),
          });
        }
        toast.success("Pinned for this agent.");
      },
      onError: (err) => {
        if (err.status === 409) {
          toast.message("Already pinned.");
        } else if (err.status === 422) {
          toast.error("You've reached the pin limit for this agent.");
        } else {
          toast.error("Couldn't pin that question.");
        }
      },
    },
  });

  const pinsEnabled = featureFlags.pinned.effective_on;
  const canPin = isUser && pinsEnabled && Boolean(endpointName);

  const handlePin = useCallback(() => {
    if (!endpointName) return;
    pinMutation.mutate({
      params: { endpoint_name: endpointName },
      data: { text: message.content, label: null },
    });
  }, [endpointName, message.content, pinMutation]);

  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      toast.success("Copied");
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      toast.error("Couldn't copy — clipboard access denied.");
    }
  }, [message.content]);

  if (isUser) {
    return (
      <motion.div
        className="group flex justify-end"
        variants={bubbleVariants}
        initial="initial"
        animate="animate"
      >
        {canPin && (
          <button
            type="button"
            onClick={handlePin}
            disabled={pinMutation.isPending}
            title="Pin this question"
            aria-label="Pin this question"
            className={[
              "mr-1 mt-2 self-start rounded-full p-1.5",
              "text-text-muted opacity-0 transition-opacity",
              "hover:bg-surface-elevated hover:text-info",
              "group-hover:opacity-100 focus-visible:opacity-100",
              "disabled:pointer-events-none disabled:opacity-30",
            ].join(" ")}
          >
            <Pin className="h-3.5 w-3.5" />
          </button>
        )}
        <div
          className={[
            "max-w-[82%] rounded-[20px] rounded-br-[6px]",
            "bg-surface-overlay px-3.5 py-1.5 text-text-primary",
          ].join(" ")}
        >
          <p className="whitespace-pre-wrap text-[0.9375rem] leading-[1.45]">
            {message.content}
          </p>
        </div>
      </motion.div>
    );
  }

  // Phase 4: each assistant answer may carry one-or-more Genie charts
  // (live via the streaming ``chart`` SSE events, or rehydrated on
  // conversation reload via ``GET /messages/{id}/charts``). The chart
  // stack renders *above* the textual answer inside the same hairline
  // column, matching the backend's emit order -- the user sees the
  // visualizations first and the explanation second, grouped as one
  // answer.
  const chartsEffective = featureFlags.charts.effective_on;
  const expectedCount =
    message.chart_count ?? (message.chart_id ? 1 : 0);
  const showChart =
    chartsEffective && (live.length > 0 || expectedCount > 0);

  return (
    <motion.div
      className="group flex flex-col gap-1.5"
      variants={bubbleVariants}
      initial="initial"
      animate="animate"
    >
      <div className="flex items-center gap-1.5 text-[0.75rem] font-medium text-text-muted">
        <Bot className="h-3.5 w-3.5" aria-hidden="true" />
        <span>Assistant</span>
      </div>
      <motion.div
        layoutId={layoutId}
        className={[
          "flex min-w-0 flex-col gap-2",
          "border-l-2 border-border/60 pl-4 py-1",
        ].join(" ")}
      >
        {showChart && (
          <ChartHydrator
            messageId={message.id}
            // Streamed artifacts have no rows (the SSE events ship the
            // ECharts option only). The hydrator backfills full rows
            // via the list / single endpoint so the "View as table" and
            // CSV export paths work the moment the user asks for them.
            initial={live}
            expectedCount={expectedCount}
          />
        )}
        <div className="min-w-0 text-text-primary">
          <MarkdownRenderer content={message.content} />
        </div>
      </motion.div>
      <div
        className={[
          "flex items-center gap-1 pl-[22px]",
          "opacity-0 transition-opacity",
          "group-hover:opacity-100 focus-within:opacity-100",
        ].join(" ")}
      >
        <button
          type="button"
          onClick={handleCopy}
          aria-label={copied ? "Copied" : "Copy message"}
          title={copied ? "Copied" : "Copy message"}
          className={[
            "inline-flex items-center gap-1 rounded-md px-1.5 py-1",
            "text-[0.6875rem] font-medium text-text-muted",
            "hover:bg-surface-elevated hover:text-text-primary",
            "focus-visible:outline-none focus-visible:ring-2",
            "focus-visible:ring-info/40",
          ].join(" ")}
        >
          {copied ? (
            <Check className="h-3.5 w-3.5 text-success" aria-hidden="true" />
          ) : (
            <Copy className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
    </motion.div>
  );
});
