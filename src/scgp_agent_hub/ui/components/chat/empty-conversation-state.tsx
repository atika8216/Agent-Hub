import { memo, useMemo } from "react";
import { Bot, Sparkles } from "lucide-react";
import { motion } from "motion/react";

import { ChatInput } from "@/components/chat/chat-input";
import { useListPins, recordPinClick } from "@/lib/api";
import {
  emptyStateHint,
  emptyStateStarterPrompts,
} from "@/lib/agent-type";
import type { AgentType } from "@/lib/types";
import { iosTween } from "@/lib/motion";

const MAX_PROMPTS = 3;

type StarterPrompt = {
  /**
   * Stable key for react list reconciliation. Pinned prompts use the
   * ``pin_id`` so edits remount cleanly; default prompts use the copy
   * hash to avoid flicker.
   */
  key: string;
  label: string;
  text: string;
  pinId?: string;
};

interface Props {
  displayName: string;
  agentType: AgentType | string;
  description?: string | null;
  endpointName: string;
  onSelect: (text: string) => void;
  onStop?: () => void;
  isStreaming?: boolean;
  disabled?: boolean;
}

/**
 * Centered welcome card shown on a brand-new conversation (or a freshly
 * opened agent). Surfaces the agent identity, a one-line hint tuned to
 * the agent type, and up to three "starter prompts" -- the user's own
 * pinned questions when available, else a static per-type fallback.
 *
 * Clicking a prompt invokes ``onSelect`` (the parent routes this into
 * ``useChat.sendMessage``) and, for pinned rows, fires a best-effort
 * ``recordPinClick`` so product analytics can see which seed prompts
 * actually get picked.
 */
export const EmptyConversationState = memo(function EmptyConversationState({
  displayName,
  agentType,
  description,
  endpointName,
  onSelect,
  onStop,
  isStreaming,
  disabled,
}: Props) {
  // Pins are the source of truth when the user has any; fall back to
  // static per-type prompts otherwise. The query is ``enabled`` here by
  // default -- latency is cheap (tiny Lakebase read) and we only render
  // the empty card when there are zero messages, so at most one fetch
  // happens per fresh conversation.
  const { data: pinsResp, isLoading: pinsLoading } = useListPins({
    params: { endpoint_name: endpointName },
  });

  const starters: StarterPrompt[] = useMemo(() => {
    const pins = pinsResp?.data?.pins ?? [];
    if (pins.length > 0) {
      return [...pins]
        .sort((a, b) => (a.position ?? 0) - (b.position ?? 0))
        .slice(0, MAX_PROMPTS)
        .map((p) => ({
          key: p.id,
          label: p.label?.trim() || truncateLabel(p.text),
          text: p.text,
          pinId: p.id,
        }));
    }
    return emptyStateStarterPrompts(agentType)
      .slice(0, MAX_PROMPTS)
      .map((text, i) => ({
        key: `default-${i}-${text.length}`,
        label: text,
        text,
      }));
  }, [pinsResp, agentType]);

  const hint = description || emptyStateHint(agentType);

  return (
    <div className="flex flex-1 flex-col justify-center">
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0, transition: iosTween }}
        className="mx-auto flex w-full max-w-2xl flex-col items-center gap-5 px-4"
      >
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          <Bot className="h-7 w-7" aria-hidden="true" />
        </div>
        <h3
          className={[
            "text-center text-[1.375rem] font-semibold tracking-[-0.02em]",
            "font-[family-name:var(--font-display)] text-text-primary",
          ].join(" ")}
        >
          {displayName}
        </h3>
        <p className="max-w-md text-center text-sm text-text-muted">{hint}</p>

        {!pinsLoading && starters.length > 0 && (
          <div
            className="mt-1 flex w-full flex-col gap-2"
            role="list"
            aria-label="Starter prompts"
          >
            {starters.map((s, i) => (
              <motion.button
                key={s.key}
                type="button"
                role="listitem"
                initial={{ opacity: 0, y: 4 }}
                animate={{
                  opacity: 1,
                  y: 0,
                  transition: { ...iosTween, delay: 0.04 * i },
                }}
                whileHover={{ y: -1 }}
                whileTap={{ scale: 0.985 }}
                disabled={disabled}
                onClick={() => {
                  if (s.pinId) {
                    void recordPinClick({
                      endpoint_name: endpointName,
                      pin_id: s.pinId,
                    }).catch(() => {});
                  }
                  onSelect(s.text);
                }}
                className={[
                  "group flex w-full items-center gap-3",
                  "rounded-[var(--radius-md)] border border-border",
                  "bg-surface-elevated px-4 py-3 text-left",
                  "text-sm text-text-primary",
                  "transition-colors hover:border-info/60 hover:bg-surface-overlay",
                  "focus-visible:outline-none focus-visible:ring-2",
                  "focus-visible:ring-info focus-visible:ring-offset-2",
                  "focus-visible:ring-offset-surface",
                  "disabled:cursor-not-allowed disabled:opacity-60",
                ].join(" ")}
              >
                <Sparkles
                  className="h-3.5 w-3.5 shrink-0 text-info opacity-70 transition-opacity group-hover:opacity-100"
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1 truncate">{s.label}</span>
              </motion.button>
            ))}
          </div>
        )}

        <div className="mt-4 w-full">
          <ChatInput
            onSend={onSelect}
            onStop={onStop ?? (() => {})}
            isStreaming={Boolean(isStreaming)}
            disabled={Boolean(disabled)}
            placeholder={`Ask ${displayName}...`}
            variant="large"
          />
        </div>
      </motion.div>
    </div>
  );
});

function truncateLabel(text: string, max = 72): string {
  const trimmed = text.trim().replace(/\s+/g, " ");
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max - 1)}\u2026`;
}
