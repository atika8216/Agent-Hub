import { memo } from "react";
import { AlertTriangle, RotateCw } from "lucide-react";
import { motion } from "motion/react";

import { Button } from "@/components/ui/button";
import { iosTween } from "@/lib/motion";

interface ErrorBubbleProps {
  message: string;
  // Optional retry action. When omitted we hide the button so the
  // component doubles as a generic "something went wrong" notice
  // without implying the user can re-run a non-existent prompt.
  onRetry?: () => void;
  // Disable the retry while a request is already in flight. Keeps the
  // bubble visible so the user still sees what happened, just prevents
  // a double-submit.
  retryDisabled?: boolean;
}

/*
 * Clarity-styled error notice that replaces the old inline
 * ``bg-error/10`` div. Sits in the assistant column so it reads like a
 * system message rather than a toast. Copy stays neutral ("Something
 * went wrong") with the actual error message rendered beneath so the
 * user always has context without being shouted at.
 *
 * Warm-red 10 % tint per the Clarity tokens; no hard red borders.
 * Light / dark parity is handled by the underlying color-scheme
 * tokens.
 */
export const ErrorBubble = memo(function ErrorBubble({
  message,
  onRetry,
  retryDisabled,
}: ErrorBubbleProps) {
  return (
    <motion.div
      role="alert"
      aria-live="polite"
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0, transition: iosTween }}
      className={[
        "flex gap-2",
      ].join(" ")}
    >
      <div className="mt-[2px] flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-error/10 text-error">
        <AlertTriangle className="h-3.5 w-3.5" />
      </div>
      <div
        className={[
          "flex min-w-0 max-w-[82%] flex-col gap-2",
          "rounded-[20px] rounded-bl-[6px]",
          "border border-error/20 bg-error/10",
          "px-4 py-3 text-text-primary",
        ].join(" ")}
      >
        <div className="flex flex-col gap-0.5">
          <p className="text-[0.8125rem] font-semibold text-error">
            Something went wrong
          </p>
          <p className="text-[0.8125rem] leading-[1.45] text-text-primary">
            {message || "The assistant couldn't complete that request."}
          </p>
        </div>
        {onRetry && (
          <div>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={onRetry}
              disabled={retryDisabled}
              aria-label="Retry the last message"
            >
              <RotateCw className="mr-1 h-3.5 w-3.5" />
              Try again
            </Button>
          </div>
        )}
      </div>
    </motion.div>
  );
});
