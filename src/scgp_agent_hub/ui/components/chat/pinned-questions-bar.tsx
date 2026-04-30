import { memo, useCallback, useState } from "react";
import { Pin, Pencil } from "lucide-react";

import { Button } from "@/components/ui/button";
import { recordPinClick, useListPins } from "@/lib/api";
import { useTheme } from "@/providers/theme-provider";
import { PinDrawer } from "./pin-drawer";

interface PinnedQuestionsBarProps {
  endpointName: string;
  onPick: (text: string) => void;
  disabled?: boolean;
}

/*
 * Per-agent saved-questions strip rendered above the suggestions bar.
 * Pins are scoped to ``(user_email, endpoint_name)`` -- switching agents
 * swaps the pin list. The "Manage" affordance opens a drawer with full
 * CRUD; quick-fill chips inline submit immediately.
 *
 * Hides itself when the feature is off, the pin list is empty *and* no
 * drawer is visible (we still expose a minimal "Pin questions" entry
 * point so a fresh user can discover the feature -- but only on hover
 * so it doesn't add visual noise).
 */
export const PinnedQuestionsBar = memo(function PinnedQuestionsBar({
  endpointName,
  onPick,
  disabled = false,
}: PinnedQuestionsBarProps) {
  const { featureFlags } = useTheme();
  const enabled = featureFlags.pinned.effective_on;
  const [drawerOpen, setDrawerOpen] = useState(false);

  const query = useListPins({
    params: { endpoint_name: endpointName },
    query: {
      enabled: enabled && Boolean(endpointName),
      retry: false,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  });

  const pins = query.data?.data?.pins ?? [];

  const handlePick = useCallback(
    (text: string) => {
      if (disabled) return;
      onPick(text);
    },
    [disabled, onPick],
  );

  // Fire-and-forget click telemetry. We intentionally do NOT await this
  // call -- a pin click must submit the chat instantly, and a slow or
  // failing telemetry endpoint must not block the user. The backend
  // logs + swallows write failures, so there's nothing to surface here.
  const handleChipClick = useCallback(
    (pinId: string, text: string) => {
      if (disabled) return;
      void recordPinClick({ endpoint_name: endpointName, pin_id: pinId }).catch(
        () => {
          // Swallow: telemetry is best-effort. A 404 here would mean the
          // pin was deleted between render and click, which isn't worth
          // surfacing to the user.
        },
      );
      onPick(text);
    },
    [disabled, endpointName, onPick],
  );

  if (!enabled) return null;
  // While the list is loading don't render an empty pill row -- just
  // wait. The bar typically populates inside ~80 ms on Lakebase and
  // the chat composer below holds the layout.
  if (query.isLoading) return null;

  return (
    <>
      {pins.length > 0 ? (
        <div className="flex w-full flex-wrap items-center gap-1.5">
          <div className="flex items-center gap-1 pr-1 text-[0.6875rem] uppercase tracking-wide text-text-muted">
            <Pin className="h-3 w-3" />
            <span>Pinned</span>
          </div>
          {pins.slice(0, 8).map((pin) => (
            <button
              key={pin.id}
              type="button"
              onClick={() => handleChipClick(pin.id, pin.text)}
              disabled={disabled}
              title={pin.text}
              className={[
                "max-w-[240px] truncate rounded-full",
                "border border-border bg-surface-elevated",
                "px-3 py-1 text-[0.8125rem] text-text-primary",
                "transition-colors duration-150",
                "hover:border-info hover:bg-info/5",
                "focus:outline-none focus-visible:border-info focus-visible:ring-2 focus-visible:ring-info/20",
                "disabled:cursor-not-allowed disabled:opacity-50",
              ].join(" ")}
            >
              {pin.label || pin.text}
            </button>
          ))}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setDrawerOpen(true)}
            className="ml-auto h-6 gap-1 px-2 text-[0.75rem]"
            title="Manage pinned questions"
          >
            <Pencil className="h-3 w-3" />
            Manage
          </Button>
        </div>
      ) : (
        <div className="flex w-full justify-end">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setDrawerOpen(true)}
            className="h-6 gap-1 px-2 text-[0.75rem]"
            title="Pin questions you reuse often"
          >
            <Pin className="h-3 w-3" />
            Pin questions
          </Button>
        </div>
      )}

      <PinDrawer
        endpointName={endpointName}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        onPick={handlePick}
      />
    </>
  );
});
