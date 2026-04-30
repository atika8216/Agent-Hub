import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/*
 * iOS-voice text input. 8px radius (slightly tighter than cards/buttons --
 * iOS keeps form controls visually "inset" into their container),
 * surface-elevated background, hairline border that brightens on focus
 * to the chrome-accent (system blue).
 *
 * The outline is suppressed so the border colour is the focus signal;
 * the global :focus-visible ring still fires for keyboard users through
 * the ring utilities already on the host page.
 */
const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type = "text", ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "block w-full rounded-[var(--radius-sm)]",
        "border border-border bg-surface-elevated",
        "px-3 py-2 text-[0.9375rem] leading-[1.35] text-text-primary",
        "placeholder:text-text-muted",
        "transition-[border-color,box-shadow] duration-150",
        "focus:border-info focus:outline-none focus:ring-2 focus:ring-info/30",
        "disabled:pointer-events-none disabled:opacity-50",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export { Input };
