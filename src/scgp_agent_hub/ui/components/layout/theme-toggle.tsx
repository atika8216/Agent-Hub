import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme, type ThemeMode } from "@/providers/theme-provider";
import { cn } from "@/lib/utils";

/*
 * iOS-style segmented control for switching between Light / Dark / System.
 *
 * Lives in the sidebar footer so it's always one flick away without
 * crowding the chrome. We intentionally use icons + aria-labels (rather
 * than icon + text) to keep the footer compact on narrow sidebars.
 */
const OPTIONS: { id: ThemeMode; label: string; icon: typeof Sun }[] = [
  { id: "light", label: "Light theme", icon: Sun },
  { id: "dark", label: "Dark theme", icon: Moon },
  { id: "system", label: "Match system", icon: Monitor },
];

export function ThemeToggle({ className }: { className?: string }) {
  const { mode, setMode, legacy } = useTheme();
  // When ``SCGP_LEGACY_UI=1`` is set on the server the theme-switching
  // affordance is meaningless (we force dark) — hide the control entirely
  // so the rollback experience isn't confusing.
  if (legacy) return null;
  return (
    <div
      role="radiogroup"
      aria-label="Appearance"
      className={cn(
        "inline-flex items-center rounded-full bg-surface-overlay p-[3px]",
        "border border-border",
        className,
      )}
    >
      {OPTIONS.map((opt) => {
        const active = mode === opt.id;
        const Icon = opt.icon;
        return (
          <button
            key={opt.id}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={opt.label}
            onClick={() => setMode(opt.id)}
            className={cn(
              "inline-flex h-7 w-8 items-center justify-center rounded-full",
              "transition-[background-color,color,transform] duration-150",
              "active:scale-[0.96]",
              active
                ? "bg-surface-elevated text-text-primary shadow-[0_1px_2px_0_oklch(0_0_0/0.06)]"
                : "text-text-muted hover:text-text-secondary",
            )}
          >
            <Icon className="h-3.5 w-3.5" />
          </button>
        );
      })}
    </div>
  );
}
