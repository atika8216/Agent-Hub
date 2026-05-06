import { forwardRef, type ButtonHTMLAttributes } from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/*
 * iOS-voice button primitive. Radii + heights follow UIKit defaults:
 *   - default height 36 (desktop density, still >= 32 for AA touch)
 *   - radius ``--radius-md`` (10px) -- iOS "rounded" control
 *   - active state uses a subtle scale + opacity dip instead of darken-on-hover
 *     so the interaction feels like a tap, not a web button
 *
 * Focus is ``ring-2 ring-offset-2`` in the chrome-accent (iOS system blue)
 * so it matches the global ``:focus-visible`` ring we set in globals.css.
 */
const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-2 whitespace-nowrap",
    "rounded-[var(--radius-md)] font-medium select-none",
    "transition-[background-color,transform,opacity] duration-150",
    "active:scale-[0.98] active:opacity-90",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2",
    "focus-visible:ring-info focus-visible:ring-offset-background",
    "disabled:pointer-events-none disabled:opacity-40",
  ].join(" "),
  {
    variants: {
      variant: {
        /** Brand accent CTA -- red-orange, white text. Reserve for the one primary action per view. */
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        /** Neutral button on elevated surfaces. */
        secondary:
          "bg-surface-elevated text-text-primary border border-border hover:bg-surface-overlay",
        /** Transparent -- only the label is visible until hover. */
        ghost:
          "text-text-secondary hover:text-text-primary hover:bg-surface-elevated",
        /** Destructive action. */
        destructive: "bg-error text-primary-foreground hover:bg-error/90",
        /** Inline link styled as a button. */
        link: "text-info underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 text-[0.9375rem]",
        sm: "h-8 px-3 text-[0.8125rem] rounded-[var(--radius-sm)]",
        lg: "h-11 px-6 text-[1rem] rounded-[var(--radius-lg)]",
        icon: "h-9 w-9 rounded-[var(--radius-md)]",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
