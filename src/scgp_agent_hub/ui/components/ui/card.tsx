import { forwardRef, type HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/*
 * iOS "card" surface. Elevated background + large radius + subtle shadow
 * in light mode, subtle top-edge hairline in dark mode (no shadow -- dark
 * UI reads as light-emitting rather than hovering). Both are applied via
 * class so a parent can override with `className`.
 *
 * The default padding grew from 16 -> 20 to match the new spacing scale
 * in the Phase 3 plan.
 */
const Card = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "rounded-[var(--radius-lg)] border border-border bg-surface-elevated",
        // Light: whisper shadow; Dark: inset hairline. Combined via data-theme.
        "shadow-[0_1px_2px_0_oklch(0_0_0/0.04)]",
        "dark:shadow-[inset_0_1px_0_oklch(1_0_0/0.04)]",
        className,
      )}
      {...props}
    />
  ),
);
Card.displayName = "Card";

const CardHeader = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("flex flex-col gap-1.5 p-5", className)}
      {...props}
    />
  ),
);
CardHeader.displayName = "CardHeader";

const CardTitle = forwardRef<HTMLHeadingElement, HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...props }, ref) => (
    <h3
      ref={ref}
      className={cn(
        "text-[1.0625rem] font-semibold leading-tight tracking-[-0.01em]",
        "font-[family-name:var(--font-display)]",
        className,
      )}
      {...props}
    />
  ),
);
CardTitle.displayName = "CardTitle";

const CardDescription = forwardRef<HTMLParagraphElement, HTMLAttributes<HTMLParagraphElement>>(
  ({ className, ...props }, ref) => (
    <p
      ref={ref}
      className={cn("text-[0.875rem] leading-[1.5] text-text-secondary", className)}
      {...props}
    />
  ),
);
CardDescription.displayName = "CardDescription";

const CardContent = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("p-5 pt-0", className)} {...props} />
  ),
);
CardContent.displayName = "CardContent";

export { Card, CardHeader, CardTitle, CardDescription, CardContent };
