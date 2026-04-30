import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { motion } from "motion/react";

import type { Agent } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  agentTypeLabel,
  agentTypeVariant,
  showSubComponentCount,
} from "@/lib/agent-type";
import { AccessBadge } from "./access-badge";

interface AgentCardProps {
  agent: Agent;
}

/*
 * Agent catalog card. The iOS-tinged refresh tightens the hierarchy:
 *   - Title + access state sit on the first row with a trailing chevron
 *     (iOS "drill-in" cue).
 *   - Description gets a stable 2-line reserve so cards align in the grid.
 *   - The footer shows the agent-type pill and an optional owner handle,
 *     both muted enough that the card's primary job (title) wins.
 *   - Hover lifts the card with a warmer border + gentle translate-y so
 *     it reads like a control, not a dropped button.
 */
export function AgentCard({ agent }: AgentCardProps) {
  const showCount = showSubComponentCount(agent.agent_type);

  return (
    <Link
      to="/catalog/$agentId"
      params={{ agentId: agent.endpoint_name }}
      className={[
        "group block outline-none rounded-[var(--radius-lg)]",
        "focus-visible:ring-2 focus-visible:ring-info focus-visible:ring-offset-2",
        "focus-visible:ring-offset-background",
      ].join(" ")}
    >
      {/*
       * The ``layoutId`` on this motion wrapper pairs with the
       * matching one on the detail page Hero. Framer Motion tracks
       * the last-known bounds of a shared ``layoutId`` across the
       * router's unmount/mount, so clicking a tile makes it physically
       * grow into the hero -- the iMessage-style continuity called
       * out in ``.impeccable.md`` -- even without an ancestor
       * ``<AnimatePresence>``.
       */}
      <motion.div
        layoutId={`agent-card-${agent.endpoint_name}`}
        className="h-full"
      >
        <Card
          className={[
            "h-full",
            // Scoped transitions (no `transition-all`) for the hover lift /
            // border-swap / shadow bloom, driven by the new motion tokens.
            "transition-[transform,border-color,box-shadow]",
            "duration-[var(--duration-med,200ms)]",
            "ease-[var(--ease-out-quart)]",
            "group-hover:-translate-y-[1px]",
            "group-hover:border-border-strong",
            "group-hover:shadow-[0_4px_16px_0_oklch(0_0_0/0.06)]",
            "dark:group-hover:shadow-[inset_0_1px_0_oklch(1_0_0/0.06)]",
            // iOS tap feedback: subtle scale-down while pressed so the
            // whole card feels like a physical control.
            "group-active:scale-[0.985]",
            "group-active:shadow-sm",
          ].join(" ")}
        >
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <CardTitle className="line-clamp-1">
              {agent.display_name}
            </CardTitle>
            <div className="flex shrink-0 items-center gap-1.5">
              <AccessBadge hasAccess={agent.has_access ?? false} />
              <ChevronRight
                className={[
                  "h-4 w-4 text-text-muted",
                  "transition-transform",
                  "duration-[var(--duration-med,200ms)]",
                  "ease-[var(--ease-out-quart)]",
                  "group-hover:translate-x-[2px]",
                ].join(" ")}
                aria-hidden="true"
              />
            </div>
          </div>
          <p className="line-clamp-2 min-h-[2.5rem] text-[0.875rem] leading-[1.45] text-text-secondary">
            {agent.description || "No description available"}
          </p>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={agentTypeVariant(agent.agent_type)} shape="pill">
              {agentTypeLabel(agent.agent_type)}
            </Badge>
            {showCount && !!agent.sub_agent_count && agent.sub_agent_count > 0 && (
              <Badge variant="default" shape="pill">
                {agent.sub_agent_count} component
                {agent.sub_agent_count !== 1 ? "s" : ""}
              </Badge>
            )}
            {agent.owner_email && (
              <span className="ml-auto max-w-[160px] truncate text-[0.75rem] text-text-muted">
                {agent.owner_email.split("@")[0]}
              </span>
            )}
          </div>
        </CardContent>
        </Card>
      </motion.div>
    </Link>
  );
}
