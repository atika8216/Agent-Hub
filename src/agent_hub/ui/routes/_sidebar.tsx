import { Outlet, createFileRoute } from "@tanstack/react-router";
import { SidebarLayout } from "@/components/layout/sidebar-layout";

export const Route = createFileRoute("/_sidebar")({
  component: SidebarLayoutRoute,
});

/*
 * The outer shell renders a bare ``<Outlet />``. Earlier revisions
 * wrapped the outlet in an ``AnimatePresence mode="wait"`` so top-level
 * section swaps could crossfade, but that wrapper intermittently
 * stalled under React 19 strict-mode double-commits and when the
 * outgoing subtree contained its own layout animations (e.g. the
 * /chat conversation list). Exit never reported ``onExitComplete``,
 * so the new route was mounted in memory but never painted -- users
 * saw a blank content pane on /catalog, /preferences, and
 * /admin/catalog while the sidebar nav pill was already updated.
 *
 * Per-page motion (chat bubble spring, catalog card -> hero shared
 * ``layoutId`` morph, stagger-child entrance) lives inside each
 * route component and is unaffected by dropping the outer wrapper.
 */
function SidebarLayoutRoute() {
  return (
    <SidebarLayout>
      <Outlet />
    </SidebarLayout>
  );
}
