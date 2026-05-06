import { memo } from "react";
import { Bot, Loader2 } from "lucide-react";
import { motion, AnimatePresence } from "motion/react";

import { MarkdownRenderer } from "./markdown-renderer";
import { iosTween } from "@/lib/motion";

/*
 * Streaming placeholder for an in-flight assistant answer.
 *
 * Shape mirrors the final MessageBubble's assistant branch exactly: a
 * tiny Bot glyph + "Assistant" label row above a hairline-bordered
 * content column. The shared ``layoutId`` on the content column is
 * what motion morphs into the finalized bubble once the stream lands,
 * so the transition is literally zero-change visually when the
 * streaming surface matches the final surface.
 *
 * While the assistant is "thinking" (no tokens yet) we render the
 * iMessage-style three-dot breathing indicator inline; once tokens
 * arrive we render the markdown with a pulsing caret. A transient
 * status pill (Genie phase labels like "Generating SQL") sits above
 * the column -- never inside the content, so we don't leak progress
 * chatter into the persisted message body.
 *
 * ``useReducedMotion`` is respected globally, so the breathing dots
 * and caret degrade to simple opacity swaps when the OS asks.
 */
export const StreamingMessage = memo(function StreamingMessage({
  content,
  conversationId,
  statusLabel,
}: {
  content: string;
  conversationId?: string | null;
  statusLabel?: string;
}) {
  const isThinking = !content;
  const layoutId = conversationId ? `stream-${conversationId}` : undefined;
  const showStatusPill = Boolean(statusLabel);

  return (
    <motion.div
      className="flex flex-col gap-1.5"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0, transition: iosTween }}
    >
      <div className="flex items-center gap-1.5 text-[0.75rem] font-medium text-text-muted">
        <Bot className="h-3.5 w-3.5" aria-hidden="true" />
        <span>Assistant</span>
      </div>

      <AnimatePresence initial={false}>
        {showStatusPill && (
          <motion.div
            key={statusLabel}
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0, transition: iosTween }}
            exit={{ opacity: 0, y: -4, transition: { duration: 0.12 } }}
            className={[
              "inline-flex w-fit items-center gap-1.5",
              "rounded-full bg-surface-overlay px-2.5 py-1",
              "text-[0.6875rem] font-medium text-text-secondary",
              "border border-border",
            ].join(" ")}
            aria-live="polite"
          >
            <Loader2 className="h-3 w-3 shrink-0 animate-spin text-info" />
            <span className="max-w-[16rem] truncate">{statusLabel}</span>
          </motion.div>
        )}
      </AnimatePresence>

      <motion.div
        layoutId={layoutId}
        className={[
          "flex min-w-0 flex-col gap-2",
          "border-l-2 border-border/60 pl-4 py-1",
          "text-text-primary",
        ].join(" ")}
      >
        {isThinking ? (
          <div
            className="flex items-center gap-1 py-1"
            aria-label="Assistant is thinking"
            role="status"
          >
            <Dot delay={0} />
            <Dot delay={0.18} />
            <Dot delay={0.36} />
          </div>
        ) : (
          <div className="min-w-0">
            <MarkdownRenderer content={content} />
            <motion.span
              className="ml-0.5 inline-block h-[1em] w-[2px] translate-y-[2px] rounded-full bg-info/80"
              animate={{ opacity: [0.2, 1, 0.2] }}
              transition={{ duration: 1.1, ease: "easeInOut", repeat: Infinity }}
              aria-hidden="true"
            />
          </div>
        )}
      </motion.div>
    </motion.div>
  );
});

/*
 * Single thinking dot. The breathing rhythm matches the iMessage
 * typing indicator: a quick rise to full opacity + 1px upward nudge
 * around the 30% mark, then settling. The 0.18s stagger across the
 * three dots produces the unmistakable left-to-right cadence.
 */
function Dot({ delay }: { delay: number }) {
  return (
    <motion.span
      className="inline-block h-[6px] w-[6px] rounded-full bg-text-muted"
      animate={{
        opacity: [0.25, 1, 0.25, 0.25],
        y: [0, -1, 0, 0],
      }}
      transition={{
        duration: 1.4,
        ease: "easeInOut",
        repeat: Infinity,
        delay,
        times: [0, 0.3, 0.6, 1],
      }}
      aria-hidden="true"
    />
  );
}
