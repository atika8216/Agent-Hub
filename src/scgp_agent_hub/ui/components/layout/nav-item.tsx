import { Link, useRouterState } from "@tanstack/react-router";
import { type LucideIcon } from "lucide-react";
import { motion } from "motion/react";

import { cn } from "@/lib/utils";
import { iosSpring } from "@/lib/motion";

interface NavItemProps {
  to: string;
  icon: LucideIcon;
  label: string;
  collapsed?: boolean;
}

/*
 * Sidebar nav row. iOS settings pattern: when active, the *entire* row
 * gets a chrome-accent fill (system blue @ 10% alpha) and the icon
 * recolours to the accent.
 *
 * The active fill is rendered as a single ``<motion.span layoutId>``
 * shared across every NavItem instance in the sidebar. When the route
 * changes, motion morphs the pill from the previously-active row to
 * the newly-active row using ``iosSpring`` -- the recognizable iOS
 * tab-bar handoff. Only the active row paints the span at any given
 * moment, so the layoutId pairing is always one-to-one.
 *
 * ``relative z-10`` on the icon + label keeps them composited above
 * the pill. The pill itself is absolute-positioned to fill the row
 * and sit beneath the content.
 */
export function NavItem({ to, icon: Icon, label, collapsed }: NavItemProps) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  // ``startsWith(${to}/)`` keeps the parent active for nested routes
  // (e.g. ``/catalog/$agentId`` keeps the "Agent Catalog" item active),
  // matching iOS UINavigationController behavior.
  const isActive = pathname === to || pathname.startsWith(`${to}/`);

  return (
    <Link
      to={to}
      className={cn(
        "group relative flex items-center gap-3 rounded-[var(--radius-md)] px-3 py-2",
        "text-[0.9375rem] font-medium",
        "transition-colors duration-[var(--duration-fast,120ms)] ease-[var(--ease-ios)]",
        isActive
          ? "text-text-primary"
          : "text-text-secondary hover:bg-surface-overlay hover:text-text-primary",
      )}
    >
      {isActive && (
        <motion.span
          layoutId="nav-pill"
          aria-hidden="true"
          className="absolute inset-0 rounded-[var(--radius-md)] bg-info/10"
          transition={iosSpring}
        />
      )}
      <Icon
        className={cn(
          "relative z-10 h-[18px] w-[18px] shrink-0",
          "transition-colors duration-[var(--duration-fast,120ms)] ease-[var(--ease-ios)]",
          isActive
            ? "text-info"
            : "text-text-muted group-hover:text-text-secondary",
        )}
      />
      {!collapsed && <span className="relative z-10 truncate">{label}</span>}
    </Link>
  );
}
