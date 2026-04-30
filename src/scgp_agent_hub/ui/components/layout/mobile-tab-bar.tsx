import { Link, useRouterState } from "@tanstack/react-router";
import { LayoutGrid, MessageSquare, Settings } from "lucide-react";
import { motion } from "motion/react";

import { cn } from "@/lib/utils";
import { iosSpring } from "@/lib/motion";
import { useCurrentUser } from "@/hooks/use-current-user";

/*
 * Bottom tab bar for viewports < 768px, modelled on iOS UITabBar.
 *
 * Visible only on mobile (md:hidden). Desktop continues to use the left
 * sidebar. Positioned ``fixed bottom-0`` with a safe-area inset so the
 * tabs don't collide with the home indicator on iPhones.
 *
 * iOS-style affordances added in the motion pass:
 *   - Shared ``layoutId="mobile-tab-pill"`` indicator that springs
 *     between tabs as the active route changes. Uses ``iosSpring`` so
 *     it lands without overshoot.
 *   - ``whileTap={{ scale: 0.92 }}`` on each tab gives the
 *     unmistakable iOS press feedback. Reduce-motion users skip this
 *     automatically via motion's runtime check.
 */
interface Tab {
  to: string;
  icon: typeof LayoutGrid;
  label: string;
}

const BASE_TABS: Tab[] = [
  { to: "/catalog", icon: LayoutGrid, label: "Catalog" },
  { to: "/chat", icon: MessageSquare, label: "Chat" },
];

const ADMIN_TAB: Tab = { to: "/admin/catalog", icon: Settings, label: "Admin" };

export function MobileTabBar() {
  const { isAdmin } = useCurrentUser();
  const tabs: Tab[] = isAdmin ? [...BASE_TABS, ADMIN_TAB] : BASE_TABS;
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  return (
    <nav
      aria-label="Primary"
      className={cn(
        "md:hidden",
        "fixed inset-x-0 bottom-0 z-40",
        "border-t border-border bg-surface-elevated/90 backdrop-blur",
        "pb-[env(safe-area-inset-bottom,0)]",
      )}
    >
      <ul className="mx-auto flex max-w-xl items-stretch justify-around px-2 py-1.5">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive =
            pathname === tab.to || pathname.startsWith(`${tab.to}/`);
          return (
            <motion.li
              key={tab.to}
              className="relative flex-1"
              whileTap={{ scale: 0.92 }}
              transition={iosSpring}
            >
              <Link
                to={tab.to}
                className={cn(
                  "group relative flex flex-col items-center justify-center gap-0.5 py-1.5",
                  "transition-colors duration-[var(--duration-fast,120ms)] ease-[var(--ease-ios)]",
                  isActive ? "text-info" : "text-text-muted",
                )}
              >
                {isActive && (
                  <motion.span
                    layoutId="mobile-tab-pill"
                    aria-hidden="true"
                    className={cn(
                      "absolute inset-x-3 inset-y-1 -z-10",
                      "rounded-[var(--radius-md)] bg-info/10",
                    )}
                    transition={iosSpring}
                  />
                )}
                <Icon className="h-[22px] w-[22px]" />
                <span className="text-[10px] font-medium leading-none">
                  {tab.label}
                </span>
              </Link>
            </motion.li>
          );
        })}
      </ul>
    </nav>
  );
}
