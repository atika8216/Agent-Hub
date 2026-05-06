import { memo, type RefObject, useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { iosTween } from "@/lib/motion";

interface ScrollToBottomFabProps {
  /* The scroll container whose bottom we want to track + scroll to. */
  scrollRef: RefObject<HTMLDivElement | null>;
  /*
   * Reset signal: any value change forces a re-read of the container's
   * bottom distance. Pass message count / streaming token so the FAB
   * hides automatically when fresh content lands at the bottom.
   */
  resetKey?: unknown;
}

/*
 * Small circular FAB pinned above the composer that only appears when
 * the user has scrolled away from the latest message (>48px from the
 * bottom). Clicking it smoothly scrolls the container to the end --
 * matches the affordance in the reference Databricks chat-ui and
 * restores the "jump to latest" gesture that ``useScrollAnchor``
 * silently releases the moment the user scrolls up.
 */
export const ScrollToBottomFab = memo(function ScrollToBottomFab({
  scrollRef,
  resetKey,
}: ScrollToBottomFabProps) {
  const [atBottom, setAtBottom] = useState(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const compute = () => {
      // 48px threshold matches the pixel-perfect gap at which the
      // user stops feeling "on the latest message". Below that, the
      // ``useScrollAnchor`` effect is still pinned and fresh content
      // will show up without nudging the viewport; above that, the
      // anchor is released and the FAB should offer a way back.
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      setAtBottom(distance < 48);
    };

    compute();
    el.addEventListener("scroll", compute, { passive: true });

    // Also recompute on resize (mobile keyboard, sidebar toggle, chart
    // inflation, etc.) so the FAB state doesn't stall on a stale
    // measurement.
    const ro = new ResizeObserver(compute);
    ro.observe(el);

    return () => {
      el.removeEventListener("scroll", compute);
      ro.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- resetKey triggers a re-subscribe
  }, [scrollRef, resetKey]);

  const handleClick = () => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  };

  return (
    <AnimatePresence>
      {!atBottom && (
        <motion.button
          type="button"
          onClick={handleClick}
          aria-label="Scroll to latest message"
          title="Scroll to latest"
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0, transition: iosTween }}
          exit={{ opacity: 0, y: 6, transition: { duration: 0.12 } }}
          className={[
            "absolute left-1/2 -top-5 -translate-x-1/2",
            "inline-flex h-9 w-9 items-center justify-center",
            "rounded-full border border-border bg-surface-elevated",
            "text-text-secondary shadow-[0_4px_12px_0_oklch(0_0_0/0.08)]",
            "transition-colors hover:text-text-primary hover:border-border-strong",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info/40",
            "focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          ].join(" ")}
        >
          <ChevronDown className="h-4 w-4" aria-hidden="true" />
        </motion.button>
      )}
    </AnimatePresence>
  );
});
