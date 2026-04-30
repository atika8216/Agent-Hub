import { cn } from "@/lib/utils";

interface AccessBadgeProps {
  hasAccess: boolean;
  className?: string;
  showLabel?: boolean;
}

export function AccessBadge({
  hasAccess,
  className,
  showLabel = false,
}: AccessBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-xs font-medium",
        hasAccess ? "text-success" : "text-error",
        className
      )}
      title={hasAccess ? "You have access" : "Access required"}
    >
      <span
        className={cn(
          "h-2 w-2 rounded-full",
          hasAccess ? "bg-success" : "bg-error"
        )}
      />
      {showLabel && (hasAccess ? "Accessible" : "No access")}
    </span>
  );
}
