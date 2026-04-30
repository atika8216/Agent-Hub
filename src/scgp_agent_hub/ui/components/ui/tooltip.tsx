import {
  type ComponentPropsWithoutRef,
  type ElementRef,
  forwardRef,
} from "react";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { motion } from "motion/react";

import { cn } from "@/lib/utils";

const TooltipProvider = TooltipPrimitive.Provider;
const Tooltip = TooltipPrimitive.Root;
const TooltipTrigger = TooltipPrimitive.Trigger;

/*
 * iOS-style tooltip: radius ``--radius-md`` (10px), overlay surface
 * with a 1px hairline, soft shadow in light mode / translucent feel
 * in dark mode. Kept compact -- tooltips are for affordance, not
 * paragraphs.
 *
 * The previous version relied on Tailwind ``animate-in fade-in-0
 * zoom-in-95`` utilities. Those classes ship with
 * ``tailwindcss-animate`` -- a plugin we don't depend on -- so the
 * animation was silently inert. Switched to ``motion`` for a guaranteed
 * iOS-cadence fade + scale entrance. ``forceMount`` lets motion
 * control the open/close transitions instead of Radix's class swap.
 */
const TooltipContent = forwardRef<
  ElementRef<typeof TooltipPrimitive.Content>,
  ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 6, children, ...props }, ref) => (
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      asChild
      {...props}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{
          opacity: 1,
          scale: 1,
          transition: { duration: 0.12, ease: [0.25, 0.1, 0.25, 1] },
        }}
        exit={{
          opacity: 0,
          scale: 0.96,
          transition: { duration: 0.1, ease: [0.25, 0.1, 0.25, 1] },
        }}
        className={cn(
          "z-50 max-w-xs rounded-[var(--radius-md)]",
          "border border-border bg-surface-overlay",
          "px-3 py-1.5 text-[0.75rem] leading-[1.35] text-text-secondary",
          "shadow-[0_4px_16px_0_oklch(0_0_0/0.08)]",
          "dark:shadow-[inset_0_1px_0_oklch(1_0_0/0.05),_0_4px_16px_0_oklch(0_0_0/0.3)]",
          className,
        )}
      >
        {children}
      </motion.div>
    </TooltipPrimitive.Content>
  </TooltipPrimitive.Portal>
));
TooltipContent.displayName = TooltipPrimitive.Content.displayName;

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider };
