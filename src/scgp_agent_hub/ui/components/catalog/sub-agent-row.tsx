import { ChevronRight } from "lucide-react";

import type { SubAgent } from "@/lib/types";
import { subComponentLabel } from "@/lib/agent-type";
import { subComponentGlyph } from "@/lib/agent-glyph";
import { AccessBadge } from "./access-badge";

interface SubAgentRowProps {
  subAgent: SubAgent;
  accessMap?: Record<string, boolean>;
  /** Row index for staggered entrance animation. */
  index?: number;
}

/*
 * iOS "drill-in" cell:
 *   - Tinted icon tile on the left (color keyed off sub-component type so a
 *     Genie Space in a MAS page matches Genie cards elsewhere).
 *   - Title + mono endpoint subtitle so it's obvious which KA endpoint / UC
 *     path / Genie space id this component resolves to.
 *   - Access state + trailing chevron on the right.
 *   - Row is an accessible <button> so the entire surface is tappable and
 *     shows an active-scale press state from `.press-tappable`.
 *
 * The button currently no-ops on click (there's no per-sub-agent route yet);
 * we keep the button semantics anyway because it makes the row feel native
 * and gives the user a keyboard affordance. An Access request link sits in
 * the trailing slot when the user lacks access -- click propagation is
 * stopped so it doesn't fire the outer button.
 */
export function SubAgentRow({ subAgent, accessMap, index }: SubAgentRowProps) {
  const glyph = subComponentGlyph(subAgent.type);
  const Icon = glyph.icon;
  const label = subComponentLabel(subAgent.type);

  const hasAccess: boolean =
    accessMap?.[subAgent.name] ?? subAgent.has_access ?? false;

  const subtitle = subAgent.endpoint_ref || subAgent.description;
  const subtitleIsMono = Boolean(subAgent.endpoint_ref);

  return (
    <div
      className={[
        "relative flex items-center gap-3 px-4 py-3",
        "border-b border-border last:border-b-0",
        "press-tappable",
        "hover:bg-surface-overlay/40 active:bg-surface-overlay/60",
        "stagger-child",
      ].join(" ")}
      style={index !== undefined ? { ["--i" as string]: index } : undefined}
    >
      <div
        aria-hidden="true"
        className={[
          "flex h-10 w-10 shrink-0 items-center justify-center",
          "rounded-[var(--radius-md)]",
        ].join(" ")}
        style={{ background: glyph.tint, color: glyph.fg }}
      >
        <Icon className="h-[18px] w-[18px]" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-[0.9375rem] font-medium text-text-primary">
            {subAgent.name}
          </p>
          <span className="shrink-0 text-[0.6875rem] uppercase tracking-[0.04em] text-text-muted">
            {label}
          </span>
        </div>
        {subtitle && (
          <p
            className={[
              "truncate text-[0.8125rem] text-text-muted",
              subtitleIsMono ? "font-[family-name:var(--font-mono)]" : "",
            ].join(" ")}
          >
            {subtitle}
          </p>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-2">
        <AccessBadge hasAccess={hasAccess} />
        {!hasAccess && subAgent.owner_email && (
          <a
            href={`mailto:${subAgent.owner_email}?subject=Access request: ${subAgent.name}`}
            className={[
              "text-[0.75rem] font-medium text-info",
              "hover:underline focus-visible:underline",
              "focus-visible:outline-none",
            ].join(" ")}
            onClick={(e) => e.stopPropagation()}
          >
            Request
          </a>
        )}
        <ChevronRight
          className="h-4 w-4 text-text-muted"
          aria-hidden="true"
        />
      </div>
    </div>
  );
}
