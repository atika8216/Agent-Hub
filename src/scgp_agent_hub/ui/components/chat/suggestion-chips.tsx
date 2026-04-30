import { memo, useCallback, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  getMessageSuggestions,
  getMessageSuggestionsKey,
  useGetMessageSuggestions,
} from "@/lib/api";
import { useChatStore, useSuggestionsFor } from "@/stores/chat-store";
import { useTheme } from "@/providers/theme-provider";
import type { SuggestionsPayload } from "@/lib/types";

interface SuggestionChipsProps {
  /**
   * The id of the assistant message these suggestions belong to. When
   * null (e.g. mid-stream before the first chart/suggestions event has
   * landed) the bar renders nothing.
   */
  messageId: string | null;
  /**
   * Click handler -- the chat parent forwards this to ``sendMessage``
   * so a chip click submits the question for the active agent.
   */
  onPick: (text: string) => void;
  /**
   * Optional: when true, the message indicates ``has_suggestions`` from
   * the conversation reload payload, so we kick off the hydrate fetch.
   */
  hasReloadSuggestions?: boolean;
  disabled?: boolean;
}

/*
 * Renders up to 3 short follow-up chips above the chat composer.
 *
 * Source resolution order:
 *   1. Live SSE payload in the chat store (set by ``use-chat`` from a
 *      ``suggestions`` event during streaming).
 *   2. Cached payload from ``GET /messages/{id}/suggestions`` (used on
 *      conversation reload when ``has_suggestions`` is true).
 *
 * The bar self-hides when there are no chips, when the feature is off
 * for the user, or when the agent is mid-stream so the chips don't
 * paint over the previous turn's suggestions while a new answer arrives.
 */
export const SuggestionChips = memo(function SuggestionChips({
  messageId,
  onPick,
  hasReloadSuggestions = false,
  disabled = false,
}: SuggestionChipsProps) {
  const { featureFlags } = useTheme();
  const isStreaming = useChatStore((s) => s.isStreaming);
  const live = useSuggestionsFor(messageId);
  const setSuggestions = useChatStore((s) => s.setSuggestions);
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);

  const enabled = featureFlags.ai_suggestions.effective_on;

  const fetchEnabled =
    enabled &&
    Boolean(messageId) &&
    !live &&
    hasReloadSuggestions &&
    !isStreaming;

  const query = useGetMessageSuggestions({
    params: { message_id: messageId ?? "" },
    query: {
      enabled: fetchEnabled,
      retry: false,
      staleTime: Infinity,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    },
  });

  // Stash the fetched payload back into the store so the chips re-render
  // off the same single source of truth as live ones (and re-mounting
  // the bar on a different message id doesn't refetch).
  if (query.data?.data && messageId && !live) {
    const data = query.data.data;
    const payload: SuggestionsPayload = {
      message_id: data.message_id,
      source: data.source,
      suggestions: data.suggestions,
    };
    setSuggestions(payload);
  }

  const payload = live;
  const suggestions = useMemo(
    () => payload?.suggestions?.slice(0, 3) ?? [],
    [payload],
  );

  const handleRefresh = useCallback(async () => {
    if (!messageId) return;
    setRefreshing(true);
    try {
      const refreshed = await getMessageSuggestions({
        message_id: messageId,
        refresh: true,
      });
      const data = refreshed.data;
      setSuggestions({
        message_id: data.message_id,
        source: data.source,
        suggestions: data.suggestions,
      });
      queryClient.setQueryData(
        getMessageSuggestionsKey({ message_id: messageId }),
        refreshed,
      );
    } catch {
      // Swallow refresh errors -- this is a "nice to have" affordance
      // and the existing chips are still shown.
    } finally {
      setRefreshing(false);
    }
  }, [messageId, queryClient, setSuggestions]);

  // Hide silently when feature is off, no message id yet, or no chips
  // returned. Keeping the shell rendered while loading would cause a
  // layout jump -- the lazy load is fast enough that empty-then-pop is
  // acceptable.
  if (!enabled || !messageId || isStreaming) return null;
  if (suggestions.length === 0) return null;

  return (
    <div className="flex w-full flex-wrap items-center gap-1.5">
      <div className="flex items-center gap-1 pr-1 text-[0.6875rem] uppercase tracking-wide text-text-muted">
        <Sparkles className="h-3 w-3" />
        <span>Try next</span>
      </div>
      {suggestions.map((text, i) => (
        <SuggestionChip
          key={`${messageId}-${i}`}
          text={text}
          onClick={() => onPick(text)}
          disabled={disabled}
        />
      ))}
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={handleRefresh}
        disabled={disabled || refreshing}
        title="Regenerate suggestions"
        aria-label="Regenerate suggestions"
        className="ml-auto h-6 w-6 rounded-full p-0"
      >
        <RefreshCw
          className={[
            "h-3 w-3",
            refreshing ? "animate-spin" : "",
          ].join(" ")}
        />
      </Button>
    </div>
  );
});

interface SuggestionChipProps {
  text: string;
  onClick: () => void;
  disabled?: boolean;
}

function SuggestionChip({ text, onClick, disabled }: SuggestionChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={[
        "group max-w-[260px] truncate rounded-full",
        "border border-border bg-surface-elevated",
        "px-3 py-1 text-[0.8125rem] text-text-primary",
        "transition-colors duration-150",
        "hover:border-info hover:bg-info/5",
        "focus:outline-none focus-visible:border-info focus-visible:ring-2 focus-visible:ring-info/20",
        "disabled:cursor-not-allowed disabled:opacity-50",
      ].join(" ")}
      title={text}
    >
      {text}
    </button>
  );
}
