import { memo, useCallback, useState } from "react";
import { Trash2 } from "lucide-react";

import { agentGlyph } from "@/lib/agent-glyph";
import { agentTypeFromEndpointName } from "@/lib/agent-type";
import type { Conversation } from "@/lib/types";

export function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

type Density = "compact" | "comfortable";

interface ConversationRowProps {
  conversation: Conversation;
  isActive?: boolean;
  density?: Density;
  showGlyph?: boolean;
  showPreview?: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

/**
 * Shared cell for both the left conversation rail and the full-page
 * /chat index list. `density` toggles the existing rail spacing vs
 * the roomier iOS-style list cell (bigger glyph + message preview).
 */
export const ConversationRow = memo(function ConversationRow({
  conversation,
  isActive = false,
  density = "compact",
  showGlyph,
  showPreview,
  onSelect,
  onDelete,
}: ConversationRowProps) {
  const [confirming, setConfirming] = useState(false);
  const comfortable = density === "comfortable";
  const withGlyph = showGlyph ?? comfortable;
  const withPreview = showPreview ?? comfortable;

  const handleDelete = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      e.preventDefault();
      if (confirming) {
        onDelete(conversation.id);
        setConfirming(false);
      } else {
        setConfirming(true);
        setTimeout(() => setConfirming(false), 3000);
      }
    },
    [confirming, conversation.id, onDelete],
  );

  const agentType = agentTypeFromEndpointName(conversation.endpoint_name);
  const { icon: Icon, tint, fg } = agentGlyph(agentType);

  const containerClass = comfortable
    ? `group relative flex w-full items-center gap-3 rounded-[var(--radius-md)] border border-border bg-surface px-4 py-3 text-left transition-colors hover:bg-surface-elevated ${
        isActive ? "bg-surface-elevated" : ""
      }`
    : `group relative flex w-full flex-col gap-0.5 rounded-lg px-3 py-2.5 text-left transition-colors ${
        isActive
          ? "bg-primary/10 text-text-primary"
          : "text-text-secondary hover:bg-surface-elevated hover:text-text-primary"
      }`;

  return (
    <button
      type="button"
      onClick={() => onSelect(conversation.id)}
      className={containerClass}
    >
      {withGlyph && (
        <span
          aria-hidden="true"
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--radius-sm)]"
          style={{ backgroundColor: tint, color: fg }}
        >
          <Icon className="h-5 w-5" />
        </span>
      )}

      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-baseline gap-2">
          <span
            className={
              comfortable
                ? "truncate text-[0.9375rem] font-semibold leading-tight text-text-primary"
                : "truncate text-sm font-medium leading-tight"
            }
          >
            {conversation.title}
          </span>
          {comfortable && (
            <span className="ml-auto shrink-0 text-xs text-text-muted">
              {timeAgo(conversation.updated_at)}
            </span>
          )}
        </div>

        {withPreview && conversation.last_message_preview ? (
          <p className="truncate text-[0.8125rem] leading-snug text-text-secondary">
            {conversation.last_message_preview}
          </p>
        ) : null}

        <div
          className={
            comfortable
              ? "flex items-center gap-2 text-xs text-text-muted"
              : "flex items-center gap-2 text-xs text-text-muted"
          }
        >
          <span className="truncate">{conversation.display_name}</span>
          {!comfortable && (
            <span className="shrink-0">{timeAgo(conversation.updated_at)}</span>
          )}
        </div>
      </div>

      <button
        type="button"
        onClick={handleDelete}
        aria-label={confirming ? "Confirm delete conversation" : "Delete conversation"}
        className={
          comfortable
            ? `absolute right-3 top-3 rounded p-1 transition-[opacity,background-color,color] duration-[var(--duration-fast,120ms)] ease-[var(--ease-out-quart)] ${
                confirming
                  ? "bg-error/20 text-error opacity-100"
                  : "text-text-muted opacity-0 hover:bg-error/10 hover:text-error group-hover:opacity-100 focus-visible:opacity-100"
              }`
            : `absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 transition-[opacity,background-color,color] duration-[var(--duration-fast,120ms)] ease-[var(--ease-out-quart)] ${
                confirming
                  ? "bg-error/20 text-error opacity-100"
                  : "text-text-muted opacity-0 hover:bg-error/10 hover:text-error group-hover:opacity-100 focus-visible:opacity-100"
              }`
        }
        title={confirming ? "Click again to confirm" : "Delete conversation"}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </button>
  );
});
