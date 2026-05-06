import { useEffect, useRef, useState } from "react";
import { useRouterState } from "@tanstack/react-router";

/*
 * Track route push/pop direction so route transitions can mimic
 * UINavigationController:
 *   - push  → new screen slides in from the right
 *   - pop   → leaving screen slides out to the right
 *
 * The implementation latches onto the browser's ``popstate`` event,
 * which fires synchronously *before* React re-renders with the new
 * location. By the time the pathname-watching effect runs we already
 * know whether the navigation was a back/forward gesture or a fresh
 * push, and we can clear the latch.
 *
 * This is intentionally history-API-agnostic: it works for in-app
 * links (push), the browser back button (pop), iOS edge-swipe
 * (popstate), and ``window.history.back()`` calls alike.
 */
export function useRouteDirection(): { forward: boolean } {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const popRef = useRef(false);
  const lastPathRef = useRef(pathname);
  const [forward, setForward] = useState(true);

  useEffect(() => {
    const handlePop = () => {
      popRef.current = true;
    };
    window.addEventListener("popstate", handlePop);
    return () => window.removeEventListener("popstate", handlePop);
  }, []);

  useEffect(() => {
    if (lastPathRef.current === pathname) return;
    setForward(!popRef.current);
    popRef.current = false;
    lastPathRef.current = pathname;
  }, [pathname]);

  return { forward };
}
