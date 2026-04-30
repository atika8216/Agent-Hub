import { useCallback, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { useChatStore } from "@/stores/chat-store";
import { listConversationsKey } from "@/lib/api";
import type { ChartArtifact, ChartKind, SSEEvent, SuggestionSource } from "@/lib/types";

// Coerce a chart_kind string off the wire into the typed union. Falls
// back to ``"table"`` so an unknown future kind degrades to the safe
// table view rather than blowing up the renderer.
function coerceChartKind(kind: unknown): ChartKind {
  if (
    kind === "bar" ||
    kind === "line" ||
    kind === "pie" ||
    kind === "scatter" ||
    kind === "table"
  ) {
    return kind;
  }
  return "table";
}

// Same idea for ``source`` on suggestions: the backend only emits one of
// these three labels today, but the wire schema is JSON so we can't rely
// on the type system alone.
function coerceSuggestionSource(s: unknown): SuggestionSource {
  return s === "genie_native" || s === "llm" || s === "fallback" ? s : "fallback";
}

// Genie's kickoff/poll emits italic status placeholders as plain
// ``token`` events -- ``_Preparing warehouse..._\n\n``,
// ``_Generating SQL..._\n\n``, etc. (see backend.chat_service
// ``_genie_status_label``). Keeping those inside the streaming bubble
// reads as noise, and the backend now intentionally persists a clean
// answer body (no prefix). We pull them out of the token stream here,
// surface the current status label to the store so the UI can render
// a status pill *above* the bubble, and let the real answer tokens
// land in ``currentStreamContent`` by themselves.
//
// Returns the cleaned token (possibly empty) and an optional status
// label to push into the store.
const GENIE_STATUS_TOKEN_RE =
  /^_((?:Preparing warehouse|Reviewing context|Generating SQL|Running query|Refreshing results|Submitted|Status:[^_]+))\.\.\._\s*(?:\n{1,2})?/;

function extractGenieStatus(
  token: string,
): { cleaned: string; status: string | null } {
  if (!token) return { cleaned: "", status: null };
  let rest = token;
  let lastStatus: string | null = null;
  // Multiple status tokens can be concatenated into a single SSE
  // ``token`` event if Genie re-enters the same state quickly -- loop
  // until no more status prefixes are found.
  while (true) {
    const match = rest.match(GENIE_STATUS_TOKEN_RE);
    if (!match) break;
    const label = match[1].replace(/^Status:\s*/, "").trim();
    if (label) lastStatus = label;
    rest = rest.slice(match[0].length);
  }
  return { cleaned: rest, status: lastStatus };
}

function reportError(setError: (msg: string) => void, msg: string, description?: string) {
  setError(msg);
  toast.error(msg, description ? { description } : undefined);
}

export function useChat() {
  const store = useChatStore();
  const abortRef = useRef<AbortController | null>(null);
  const queryClient = useQueryClient();

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const invalidateConversations = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: listConversationsKey() });
  }, [queryClient]);

  const sendMessage = useCallback(
    async (
      endpointName: string,
      message: string,
      conversationId?: string | null,
      toolChoice?: string | null,
    ) => {
      const {
        addUserMessage,
        startStream,
        setError,
        setConversationId,
        appendToken,
        setCurrentStatus,
        appendToolCall,
        appendToolResult,
        setPendingToolChoice,
        finishStream,
        setChart,
        setSuggestions,
        setStreamingAssistantId,
      } = useChatStore.getState();

      addUserMessage(message);
      startStream();

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const response = await fetch(`/api/v1/chat/${encodeURIComponent(endpointName)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message,
            conversation_id: conversationId ?? null,
            tool_choice: toolChoice ?? null,
          }),
          signal: controller.signal,
        });

        if (!response.ok) {
          const text = await response.text();
          reportError(
            setError,
            `Chat request failed (${response.status})`,
            text || response.statusText,
          );
          return;
        }

        const reader = response.body?.getReader();
        if (!reader) {
          reportError(setError, "No response stream available");
          return;
        }

        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed.startsWith("data: ")) continue;

            try {
              const event: SSEEvent = JSON.parse(trimmed.slice(6));

              if (event.error) {
                reportError(setError, event.error);
                return;
              }

              // Backend emits this the moment the conversation row + user
              // message are persisted, before any assistant tokens arrive.
              // Flip the URL to /chat/$id and refresh the sidebar immediately.
              if (event.type === "started" && event.conversation_id) {
                setConversationId(event.conversation_id);
                invalidateConversations();
                continue;
              }

              if (event.type === "tool_call" && event.name) {
                appendToolCall(event.name, event.input ?? {});
                continue;
              }

              if (event.type === "tool_result" && event.name) {
                appendToolResult(event.name, Boolean(event.is_error));
                continue;
              }

              if (event.type === "needs_tool_choice" && event.tools) {
                // Backend couldn't auto-pick a tool. Surface the picker in
                // the UI and stop the current turn; the user will resubmit
                // with ``tool_choice`` set to their selection.
                setPendingToolChoice(event.tools);
                finishStream(useChatStore.getState().conversationId ?? "");
                return;
              }

              // Phase 4: chart artifact arrives *before* tokens so the
              // ECharts card can render above the streaming answer. The
              // backend ships the full pre-built ECharts ``option``;
              // we treat it as opaque on the FE. Genie can emit
              // multiple ``query`` attachments per turn, in which case
              // each arrives as its own ``chart`` event with a 0-based
              // ``index`` so the UI can keep them in stable order.
              if (event.type === "chart" && event.message_id) {
                const artifact: ChartArtifact = {
                  chart_id: event.chart_id ?? "",
                  message_id: event.message_id,
                  conversation_id:
                    event.conversation_id ??
                    useChatStore.getState().conversationId ??
                    "",
                  chart_kind: coerceChartKind(event.chart_kind),
                  title: event.title ?? "",
                  option: event.option ?? {},
                  // The streaming event keeps the payload small by omitting
                  // the underlying rows -- the card can fall back to the
                  // ECharts visualization-only view, and the lazy-loaded
                  // ``GET /messages/{id}/charts`` rehydrates the table
                  // data when the user toggles "View as table".
                  columns: [],
                  rows: [],
                  truncated: Boolean(event.truncated),
                  idx: typeof event.index === "number" ? event.index : 0,
                };
                setChart(artifact);
                setStreamingAssistantId(event.message_id);
                continue;
              }

              // Phase 4: suggestions arrive immediately before the
              // ``done`` event so the chips light up the moment the
              // stream finishes. A timeout-fallback empty list is still
              // a valid event -- treat it as "no suggestions" rather
              // than skipping it so the slot becomes ready-to-render.
              if (event.type === "suggestions" && event.message_id) {
                setSuggestions({
                  message_id: event.message_id,
                  source: coerceSuggestionSource(event.source),
                  suggestions: Array.isArray(event.suggestions)
                    ? event.suggestions
                    : [],
                });
                setStreamingAssistantId(event.message_id);
                continue;
              }

              if (event.token) {
                const { cleaned, status } = extractGenieStatus(event.token);
                if (status !== null) {
                  setCurrentStatus(status);
                }
                if (cleaned) {
                  appendToken(cleaned);
                }
                // A pure-status token is a no-op for the answer body --
                // drop it silently. The pill above the bubble already
                // reflects the current Genie phase.
              }

              if (event.done && event.conversation_id) {
                finishStream(event.conversation_id);
                invalidateConversations();
                return;
              }
            } catch {
              // Partial JSON or non-JSON line -- skip
            }
          }
        }

        if (useChatStore.getState().isStreaming) {
          finishStream(conversationId ?? "");
          invalidateConversations();
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          finishStream(useChatStore.getState().conversationId ?? conversationId ?? "");
          invalidateConversations();
          return;
        }
        const msg = err instanceof Error ? err.message : "Network error";
        reportError(useChatStore.getState().setError, "Connection lost", msg);
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
      }
    },
    [invalidateConversations],
  );

  return {
    sendMessage,
    stop,
    isStreaming: store.isStreaming,
    currentStreamContent: store.currentStreamContent,
    currentStatus: store.currentStatus,
    messages: store.messages,
    conversationId: store.conversationId,
    endpointName: store.endpointName,
    error: store.error,
    timelineEvents: store.timelineEvents,
    pendingToolChoice: store.pendingToolChoice,
    setMessages: store.setMessages,
    setEndpoint: store.setEndpoint,
    setConversationId: store.setConversationId,
    setError: store.setError,
    setPendingToolChoice: store.setPendingToolChoice,
    clearTimeline: store.clearTimeline,
    reset: store.reset,
  };
}
