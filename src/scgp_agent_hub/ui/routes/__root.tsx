import { Outlet, createRootRoute } from "@tanstack/react-router";
import { Toaster } from "sonner";

import { TooltipProvider } from "@/components/ui/tooltip";

export const Route = createRootRoute({
  component: RootComponent,
});

function RootComponent() {
  return (
    <TooltipProvider delayDuration={150}>
      <Outlet />
      <Toaster
        position="bottom-right"
        // Toasts stay in sync with the active theme via data-theme on <html>.
        // We set the surface + text colors to semantic tokens so the toast
        // matches both light and dark modes without extra work.
        toastOptions={{
          style: {
            background: "var(--color-surface-elevated)",
            border: "1px solid var(--color-border)",
            color: "var(--color-text-primary)",
            fontFamily: "var(--font-sans)",
            borderRadius: "var(--radius-md)",
          },
        }}
      />
    </TooltipProvider>
  );
}
