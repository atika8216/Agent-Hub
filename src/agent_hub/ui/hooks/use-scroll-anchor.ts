import { useEffect, useRef, type RefObject } from "react";

/*
 * Smoothly keep a scroll container pinned to the bottom while
 * respecting the user's manual scrollback.
 *
 * Why this exists: the previous approach -- ``el.scrollTo({ behavior:
 * "smooth" })`` on every token -- stacks smooth-scroll requests on
 * each render, which on iOS Safari produces visible stuttering and
 * occasionally drops frames during streaming. Worse, it pulls the
 * user back to the bottom even if they had scrolled up to read
 * earlier content.
 *
 * The hook here:
 *   - Tracks whether the user is currently "pinned" (within
 *     ``threshold`` px of the bottom). The instant they scroll up,
 *     we stop autoscrolling so they can read freely.
 *   - When pinned, schedules a single rAF write per dependency tick;
 *     no smooth interpolation, no stacking. The browser composites
 *     the new scrollTop on the same frame as the layout that grew
 *     the content, so streaming feels like a continuous "follow the
 *     last token" rather than a series of jumps.
 *   - Re-pins automatically when the user scrolls back to the
 *     bottom, matching iMessage's behavior.
 */
export function useScrollAnchor<T extends HTMLElement>(
  ref: RefObject<T | null>,
  deps: ReadonlyArray<unknown>,
  options: { threshold?: number } = {},
) {
  const { threshold = 80 } = options;
  const pinnedRef = useRef(true);

  // Track whether we should still auto-stick. The user scrolling up
  // breaks the seal; scrolling back to the bottom re-engages it.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => {
      const distanceFromBottom =
        el.scrollHeight - el.scrollTop - el.clientHeight;
      pinnedRef.current = distanceFromBottom <= threshold;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [ref, threshold]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!pinnedRef.current) return;
    const id = requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
    return () => cancelAnimationFrame(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deps are passed by caller
  }, deps);

  // Lazy-inflation guard: a chart artifact hydrates AFTER the initial
  // scroll settles, bumping the container height from ~96 px (skeleton)
  // to ~480 px (canvas + legend). Without this, the latest user message
  // ends up floating above the fold and the viewport looks "stuck".
  // A ``ResizeObserver`` on the first-child content wrapper fires
  // exactly when content actually grows -- cheap, no polling, and only
  // scrolls when the user was still pinned, so manual scrollback is
  // preserved.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const content = el.firstElementChild;
    if (!content || typeof ResizeObserver === "undefined") return;

    let lastHeight = (content as HTMLElement).offsetHeight;
    let rafId: number | null = null;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const nextHeight = entry.contentRect.height;
      if (nextHeight <= lastHeight) {
        lastHeight = nextHeight;
        return;
      }
      lastHeight = nextHeight;
      if (!pinnedRef.current) return;
      if (rafId !== null) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
        rafId = null;
      });
    });

    observer.observe(content);
    return () => {
      observer.disconnect();
      if (rafId !== null) cancelAnimationFrame(rafId);
    };
  }, [ref]);
}
