import { Outlet, createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/_sidebar/admin")({
  beforeLoad: ({ location }) => {
    if (location.pathname === "/admin" || location.pathname === "/admin/") {
      throw redirect({ to: "/admin/catalog" });
    }
  },
  component: AdminLayout,
});

function AdminLayout() {
  return <Outlet />;
}
