import { Outlet, createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/_sidebar/catalog")({
  component: CatalogLayout,
});

/*
 * Bare ``<Outlet />`` -- no ``AnimatePresence`` at this layer.
 *
 * The card -> hero transition relies on the shared ``layoutId`` between
 * ``agent-card.tsx`` and the Hero in ``catalog.$agentId.tsx``. Framer
 * Motion tracks the last-known bounds of a shared ``layoutId`` across
 * the unmount/mount that the router performs within the same frame,
 * so the morph still fires without any wrapper presence here.
 */
function CatalogLayout() {
  return <Outlet />;
}
