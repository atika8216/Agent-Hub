import { memo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Loader2, Pin, Plus, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  listPinsKey,
  recordPinClick,
  useCreatePin,
  useDeletePin,
  useListPins,
  useUpdatePin,
} from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";

interface PinDrawerProps {
  endpointName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPick: (text: string) => void;
}

/*
 * Full-CRUD drawer for the user's pinned questions on the active agent.
 *
 * We deliberately use a centered dialog rather than a side-sheet
 * because:
 *   - The chat already takes the full vertical viewport on iPad-class
 *     widths; a slide-over from the right would compete with the
 *     agent's transcript.
 *   - The pin list rarely exceeds ~10 items, so a 480px-wide modal
 *     with internal scroll fits comfortably without resizing chrome.
 *
 * Drag-to-reorder is intentionally NOT implemented in this iteration:
 * the plan calls it out as a "do not over-engineer" follow-up.
 * Position is server-side and editable through the future bulk API.
 */
export const PinDrawer = memo(function PinDrawer({
  endpointName,
  open,
  onOpenChange,
  onPick,
}: PinDrawerProps) {
  const [newText, setNewText] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingLabel, setEditingLabel] = useState("");
  const queryClient = useQueryClient();

  const list = useListPins({
    params: { endpoint_name: endpointName },
    query: {
      enabled: open && Boolean(endpointName),
      retry: false,
      refetchOnWindowFocus: false,
    },
  });

  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: listPinsKey({ endpoint_name: endpointName }),
    });

  const createMutation = useCreatePin({
    mutation: {
      onSuccess: () => {
        invalidate();
        setNewText("");
        setNewLabel("");
      },
      onError: (err) => {
        const status = err.status;
        if (status === 409) {
          toast.error("You've already pinned that exact question.");
        } else if (status === 422) {
          toast.error("You've reached the pin limit for this agent.");
        } else {
          toast.error("Couldn't pin that question. Try again?");
        }
      },
    },
  });

  const updateMutation = useUpdatePin({
    mutation: {
      onSuccess: () => {
        invalidate();
        setEditingId(null);
        setEditingLabel("");
      },
      onError: () => toast.error("Couldn't update the pin label."),
    },
  });

  const deleteMutation = useDeletePin({
    mutation: {
      onSuccess: () => invalidate(),
      onError: () => toast.error("Couldn't delete the pin."),
    },
  });

  const pins = list.data?.data?.pins ?? [];

  const handleCreate = () => {
    const text = newText.trim();
    if (!text) return;
    const label = newLabel.trim();
    createMutation.mutate({
      params: { endpoint_name: endpointName },
      data: {
        text,
        label: label || null,
      },
    });
  };

  const handleStartEdit = (id: string, current: string | null | undefined) => {
    setEditingId(id);
    setEditingLabel(current ?? "");
  };

  const handleSaveEdit = (id: string) => {
    updateMutation.mutate({
      params: { endpoint_name: endpointName, pin_id: id },
      data: { label: editingLabel.trim() || null },
    });
  };

  const handleDelete = (id: string) => {
    deleteMutation.mutate({
      params: { endpoint_name: endpointName, pin_id: id },
    });
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay
          className={[
            "fixed inset-0 z-40",
            "bg-black/40 backdrop-blur-sm",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0",
            "data-[state=closed]:animate-out data-[state=closed]:fade-out-0",
          ].join(" ")}
        />
        <Dialog.Content
          aria-describedby={undefined}
          className={[
            "fixed left-1/2 top-1/2 z-50",
            "w-[min(92vw,520px)] max-h-[80vh]",
            "-translate-x-1/2 -translate-y-1/2",
            "rounded-[var(--radius-xl)] border border-border bg-surface text-text-primary",
            "shadow-2xl",
            "flex flex-col overflow-hidden",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
          ].join(" ")}
        >
          <header className="flex items-center justify-between border-b border-border px-5 py-3">
            <Dialog.Title className="flex items-center gap-2 font-[family-name:var(--font-display)] text-[1rem] font-semibold">
              <Pin className="h-4 w-4 text-info" />
              Pinned questions
            </Dialog.Title>
            <Dialog.Close asChild>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                title="Close"
                aria-label="Close"
              >
                <X className="h-4 w-4" />
              </Button>
            </Dialog.Close>
          </header>

          <div className="flex flex-col gap-3 px-5 py-4 border-b border-border">
            <p className="text-[0.75rem] text-text-muted">
              Pin questions you ask this agent often. Click a pin in the
              chat composer to fill the input instantly.
            </p>
            <div className="flex flex-col gap-2">
              <Input
                value={newText}
                onChange={(e) => setNewText(e.target.value)}
                placeholder="Question text..."
                disabled={createMutation.isPending}
              />
              <div className="flex gap-2">
                <Input
                  value={newLabel}
                  onChange={(e) => setNewLabel(e.target.value)}
                  placeholder="Optional short label"
                  disabled={createMutation.isPending}
                  className="flex-1"
                />
                <Button
                  type="button"
                  onClick={handleCreate}
                  disabled={!newText.trim() || createMutation.isPending}
                  size="default"
                >
                  {createMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Plus className="h-4 w-4" />
                  )}
                  Pin
                </Button>
              </div>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
            {list.isLoading ? (
              <div className="flex items-center justify-center py-8 text-text-muted">
                <Loader2 className="h-5 w-5 animate-spin" />
              </div>
            ) : pins.length === 0 ? (
              <p className="px-3 py-6 text-center text-[0.8125rem] text-text-muted">
                No pinned questions yet. Add one above to get started.
              </p>
            ) : (
              <ul className="flex flex-col gap-1">
                {pins.map((pin) => (
                  <li
                    key={pin.id}
                    className={[
                      "group flex items-start gap-2",
                      "rounded-[var(--radius-md)] px-3 py-2",
                      "hover:bg-surface-elevated",
                    ].join(" ")}
                  >
                    <button
                      type="button"
                      onClick={() => {
                        // Fire-and-forget: telemetry must not delay the
                        // chat send. See pinned-questions-bar.tsx for
                        // the matching comment + rationale.
                        void recordPinClick({
                          endpoint_name: endpointName,
                          pin_id: pin.id,
                        }).catch(() => {});
                        onPick(pin.text);
                        onOpenChange(false);
                      }}
                      className="min-w-0 flex-1 text-left"
                      title="Use this pin"
                    >
                      {editingId === pin.id ? (
                        <Input
                          value={editingLabel}
                          onChange={(e) => setEditingLabel(e.target.value)}
                          onClick={(e) => e.stopPropagation()}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleSaveEdit(pin.id);
                            if (e.key === "Escape") setEditingId(null);
                          }}
                          placeholder="Label"
                          autoFocus
                        />
                      ) : (
                        <p className="text-[0.875rem] font-medium text-text-primary">
                          {pin.label || pin.text}
                        </p>
                      )}
                      <p className="mt-0.5 truncate text-[0.75rem] text-text-muted">
                        {pin.text}
                      </p>
                    </button>
                    <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                      {editingId === pin.id ? (
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() => handleSaveEdit(pin.id)}
                          disabled={updateMutation.isPending}
                          className="h-7"
                        >
                          Save
                        </Button>
                      ) : (
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() => handleStartEdit(pin.id, pin.label)}
                          className="h-7"
                        >
                          Edit
                        </Button>
                      )}
                      <Button
                        type="button"
                        size="icon"
                        variant="ghost"
                        onClick={() => handleDelete(pin.id)}
                        disabled={deleteMutation.isPending}
                        title="Delete pin"
                        aria-label="Delete pin"
                        className="h-7 w-7"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
});
