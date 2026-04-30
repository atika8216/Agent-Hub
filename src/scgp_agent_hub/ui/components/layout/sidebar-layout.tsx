import { type ReactNode } from "react";
import { LayoutGrid, MessageSquare, Settings, Sliders, Zap } from "lucide-react";
import { NavItem } from "./nav-item";
import { ThemeToggle } from "./theme-toggle";
import { MobileTabBar } from "./mobile-tab-bar";
import { useCurrentUser } from "@/hooks/use-current-user";

interface SidebarLayoutProps {
  children: ReactNode;
}

/*
 * Adaptive app shell:
 *   - Desktop (>= 768px): left sidebar with nav + user + theme toggle
 *   - Mobile (< 768px):   sidebar hidden; primary nav moves to the
 *                         bottom as a UITabBar-style strip
 *
 * The main content pane gets an extra bottom padding on mobile so the
 * last message / catalog row clears the fixed tab bar (84px + safe-area
 * inset). 84 = 64px tab bar + 20px gap so CTAs near the bottom don't
 * get tapped "through" to a nav tab.
 */
export function SidebarLayout({ children }: SidebarLayoutProps) {
  const { user, isAdmin } = useCurrentUser();

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <aside
        className={[
          // Hidden on narrow viewports -- mobile uses the tab bar.
          "hidden md:flex",
          "w-64 shrink-0 flex-col",
          "border-r border-border bg-surface",
        ].join(" ")}
      >
        {/* Wordmark */}
        <div className="flex h-14 items-center gap-2 px-5">
          <Zap className="h-[18px] w-[18px] text-primary" />
          <span className="text-[0.9375rem] font-semibold tracking-[-0.01em] font-[family-name:var(--font-display)]">
            SCGP Agent Hub
          </span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-2">
          <p className="px-3 pt-2 pb-1 text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-text-muted">
            Main
          </p>
          <NavItem to="/catalog" icon={LayoutGrid} label="Agent Catalog" />
          <NavItem to="/chat" icon={MessageSquare} label="Chat" />
          <NavItem to="/preferences" icon={Sliders} label="Preferences" />

          {isAdmin && (
            <>
              <div className="mx-3 my-3 border-t border-border" />
              <p className="px-3 pt-1 pb-1 text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-text-muted">
                Admin
              </p>
              <NavItem
                to="/admin/catalog"
                icon={LayoutGrid}
                label="Catalog Management"
              />
              <NavItem
                to="/admin/settings"
                icon={Settings}
                label="Settings"
              />
            </>
          )}
        </nav>

        {/* Footer: theme toggle + user card */}
        <div className="border-t border-border px-4 py-3">
          <div className="mb-3 flex items-center justify-between">
            <span className="text-[0.75rem] font-medium text-text-muted">
              Appearance
            </span>
            <ThemeToggle />
          </div>
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-surface-overlay text-[0.8125rem] font-medium text-text-secondary">
              {user?.display_name?.charAt(0)?.toUpperCase() ?? "?"}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[0.8125rem] font-medium text-text-primary">
                {user?.display_name ?? "Loading..."}
              </p>
              <p className="truncate text-[0.75rem] text-text-muted">
                {user?.role ?? ""}
              </p>
            </div>
          </div>
        </div>
      </aside>

      <main
        className={[
          "flex min-w-0 flex-1 flex-col overflow-y-auto",
          // Reserve space for the mobile tab bar on <md viewports.
          // The bar itself is ~64px + safe-area inset; we add 20px
          // extra so the final row / CTA isn't visually jammed against
          // the blurred tab bar (admins kept clicking through to the
          // Admin tab when registering manual endpoints on iPad).
          "pb-[calc(84px+env(safe-area-inset-bottom,0px))] md:pb-0",
        ].join(" ")}
      >
        {children}
      </main>

      <MobileTabBar />
    </div>
  );
}
