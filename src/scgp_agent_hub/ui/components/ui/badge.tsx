import { type HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/*
 * iOS-style pill badge. Two changes vs the Observatory badge:
 *   1. Uppercase tracking dropped in favour of SF/Pretendard's natural
 *      small-caps feel -- iOS uses sentence-case status text. The ``uppercase``
 *      + tracking combo was the single loudest remnant of the cockpit
 *      aesthetic.
 *   2. Pill radius (999px) for semantic statuses; ``--radius-sm`` reserved
 *      for tag-style chips. Variant callers pick via ``shape``.
 */
const badgeVariants = cva(
  [
    "inline-flex items-center gap-1",
    "px-2 py-[3px]",
    "text-[0.6875rem] font-medium leading-none tracking-[0.01em]",
    "font-[family-name:var(--font-sans)]",
  ].join(" "),
  {
    variants: {
      variant: {
        default: "bg-surface-elevated text-text-secondary border border-border",
        mas: "bg-badge-mas/10 text-badge-mas",
        agent: "bg-badge-agent/10 text-badge-agent",
        ka: "bg-badge-ka/10 text-badge-ka",
        model: "bg-badge-model/15 text-text-secondary border border-border",
        external: "bg-badge-external/10 text-badge-external",
        genie: "bg-badge-genie/10 text-badge-genie",
        uc: "bg-badge-uc/10 text-badge-uc",
        mcp: "bg-badge-mcp/10 text-badge-mcp",
        vector: "bg-badge-vector/10 text-badge-vector",
        success: "bg-success/10 text-success",
        error: "bg-error/10 text-error",
        warning: "bg-warning/10 text-warning",
        info: "bg-info/10 text-info",
      },
      shape: {
        /** Pill -- iOS status indicator. */
        pill: "rounded-full",
        /** Chip -- tag/classification (e.g. agent type on a card). */
        chip: "rounded-[var(--radius-sm)]",
      },
    },
    defaultVariants: {
      variant: "default",
      shape: "pill",
    },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, shape, ...props }: BadgeProps) {
  return (
    <span
      className={cn(badgeVariants({ variant, shape }), className)}
      {...props}
    />
  );
}

export { Badge, badgeVariants };
