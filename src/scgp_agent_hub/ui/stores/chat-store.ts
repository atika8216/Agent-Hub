import { create } from "zustand";

import type {
  ChartArtifact,
  ChatTimelineEvent,
  McpToolDescriptor,
  Message,
  SuggestionSource,
  SuggestionsPayload,
} from "@/lib/types";

/*
 * Phase 4 streaming-state shape: the assistant message id is persisted on
 * the backend *before* tokens are streamed, so each ``chart`` /
 * ``suggestions`` SSE event carries a ``message_id`` we can use to attach
 * the artifact to the right transcript entry. While the stream is in
 * flight that id may not yet be in ``messages`` (we only push the
 * synthesized assistant message on ``finishStream``), so we keep the
 * artifacts in their own ``Record<message_id, …>`` slices and the chat
 * view reads them by id when rendering.
 */
interface ChatState {
  isStreaming: boolean;
  currentStreamContent: string;
  // Live Genie progress label (e.g. "Generating SQL"), rendered as a
  // status pill *above* the streaming bubble. We keep it in the store
  // (not inline in ``currentStreamContent``) so the answer body is
  // strictly the real answer -- matches the clean persist contract in
  // ``chat_service.py`` and survives reloads without italic noise.
  currentStatus: string;
  messages: Message[];
  conversationId: string | null;
  endpointName: string | null;
  error: string | null;

  // Phase 2 (UC HTTP + MCP chat). Rendered inline with messages so the
  // chat log always reflects which tool ran, its inputs, and whether
  // the call errored.
  timelineEvents: ChatTimelineEvent[];
  pendingToolChoice: McpToolDescriptor[] | null;

  // Phase 4. The id is the *assistant* message id. ``charts`` is keyed
  // by message id with a list of artifacts (Genie can return multiple
  // ``query`` attachments per turn; the UI stacks them). Each new SSE
  // ``chart`` event slots its artifact into the correct position by
  // ``idx`` so ordering stays stable even if attachments resolve out
  // of order. ``streamingAssistantId`` remembers the id assigned by
  // the first chart/suggestions/done event so we can stamp it on the
  // synthesized message in ``finishStream``.
  charts: Record<string, ChartArtifact[]>;
  suggestions: Record<string, SuggestionsPayload>;
  streamingAssistantId: string | null;

  startStream: () => void;
  appendToken: (token: string) => void;
  setCurrentStatus: (status: string) => void;
  appendToolCall: (name: string, input: Record<string, unknown>) => void;
  appendToolResult: (name: string, isError: boolean) => void;
  setPendingToolChoice: (tools: McpToolDescriptor[] | null) => void;
  clearTimeline: () => void;
  finishStream: (conversationId: string) => void;
  setMessages: (messages: Message[]) => void;
  addUserMessage: (content: string) => void;
  setEndpoint: (name: string) => void;
  setConversationId: (id: string | null) => void;
  setError: (error: string | null) => void;
  // Phase 4 actions. ``setStreamingAssistantId`` is set the first time we
  // see the placeholder message id during a stream; everything else just
  // drops payloads into the keyed maps.
  setStreamingAssistantId: (id: string | null) => void;
  // Insert or replace an artifact at ``artifact.idx`` for its message.
  // A later artifact with the same idx replaces the earlier one; a new
  // idx slots in and pushes nothing. Used for both the live SSE path
  // and the reload path (after ``listMessageCharts``).
  setChart: (artifact: ChartArtifact) => void;
  // Wholesale replacement of the chart list for a message, used by the
  // reload path so the order from the server (``idx ASC, created_at ASC``)
  // is preserved exactly without merge juggling.
  setChartsForMessage: (messageId: string, artifacts: ChartArtifact[]) => void;
  setSuggestions: (payload: SuggestionsPayload) => void;
  removeSuggestionsFor: (messageId: string) => void;
  reset: () => void;
}

function nowIso(): string {
  return new Date().toISOString();
}

function nextId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

export const useChatStore = create<ChatState>((set) => ({
  isStreaming: false,
  currentStreamContent: "",
  currentStatus: "",
  messages: [],
  conversationId: null,
  endpointName: null,
  error: null,
  timelineEvents: [],
  pendingToolChoice: null,
  charts: {},
  suggestions: {},
  streamingAssistantId: null,

  startStream: () =>
    set({
      isStreaming: true,
      currentStreamContent: "",
      currentStatus: "",
      error: null,
      pendingToolChoice: null,
      streamingAssistantId: null,
    }),

  appendToken: (token) =>
    set((s) => ({ currentStreamContent: s.currentStreamContent + token })),

  setCurrentStatus: (status) => set({ currentStatus: status }),

  appendToolCall: (name, input) =>
    set((s) => ({
      timelineEvents: [
        ...s.timelineEvents,
        {
          id: nextId("tc"),
          kind: "tool_call",
          name,
          input,
          created_at: nowIso(),
        },
      ],
    })),

  appendToolResult: (name, isError) =>
    set((s) => ({
      timelineEvents: [
        ...s.timelineEvents,
        {
          id: nextId("tr"),
          kind: "tool_result",
          name,
          is_error: isError,
          created_at: nowIso(),
        },
      ],
    })),

  setPendingToolChoice: (tools) => set({ pendingToolChoice: tools }),

  clearTimeline: () =>
    set({ timelineEvents: [], pendingToolChoice: null }),

  finishStream: (conversationId) =>
    set((s) => {
      if (!s.currentStreamContent) {
        return {
          isStreaming: false,
          currentStatus: "",
          conversationId,
          streamingAssistantId: null,
        };
      }
      // Prefer the backend-assigned id when we have one (a chart /
      // suggestions / done event will have stamped it via
      // setStreamingAssistantId) so the synthesized assistant message
      // matches the same id used by the keyed chart/suggestions slices.
      // Falls back to a synthetic id for the legacy / non-Phase-4 path.
      const assistantId = s.streamingAssistantId ?? `stream-${Date.now()}`;
      const chartsHit = s.charts[assistantId] ?? [];
      const suggestionsHit = s.suggestions[assistantId];
      const assistantMsg: Message = {
        id: assistantId,
        role: "assistant",
        content: s.currentStreamContent,
        created_at: nowIso(),
        chart_id: chartsHit.length > 0 ? chartsHit[0].chart_id : null,
        chart_count: chartsHit.length,
        has_suggestions: Boolean(suggestionsHit?.suggestions?.length),
      };
      return {
        isStreaming: false,
        currentStreamContent: "",
        currentStatus: "",
        conversationId,
        messages: [...s.messages, assistantMsg],
        streamingAssistantId: null,
      };
    }),

  setMessages: (messages) => set({ messages }),

  addUserMessage: (content) =>
    set((s) => ({
      messages: [
        ...s.messages,
        {
          id: `user-${Date.now()}`,
          role: "user" as const,
          content,
          created_at: nowIso(),
        },
      ],
    })),

  setEndpoint: (name) => set({ endpointName: name }),
  setConversationId: (id) => set({ conversationId: id }),
  setError: (error) => set({ error, isStreaming: false }),

  setStreamingAssistantId: (id) => set({ streamingAssistantId: id }),

  setChart: (artifact) =>
    set((s) => {
      const prev = s.charts[artifact.message_id] ?? [];
      const idx = artifact.idx ?? 0;
      // Replace in place when an artifact with the same idx already
      // exists (e.g. a late full-rows rehydrate replacing the live
      // row-less placeholder); otherwise append in idx-ascending
      // order. Sorting keeps the final list deterministic even when
      // attachments resolve out of order mid-stream.
      const seen = prev.some((c) => (c.idx ?? 0) === idx);
      const next = seen
        ? prev.map((c) => ((c.idx ?? 0) === idx ? artifact : c))
        : [...prev, artifact];
      next.sort((a, b) => (a.idx ?? 0) - (b.idx ?? 0));
      return {
        charts: { ...s.charts, [artifact.message_id]: next },
        // The first chart event during a turn pins down the assistant
        // message id even before ``done`` arrives, so the synthesized
        // assistant message in finishStream picks up the same id.
        streamingAssistantId: s.streamingAssistantId ?? artifact.message_id,
      };
    }),

  setChartsForMessage: (messageId, artifacts) =>
    set((s) => ({
      charts: { ...s.charts, [messageId]: [...artifacts] },
    })),

  setSuggestions: (payload) =>
    set((s) => ({
      suggestions: { ...s.suggestions, [payload.message_id]: payload },
      streamingAssistantId: s.streamingAssistantId ?? payload.message_id,
    })),

  removeSuggestionsFor: (messageId) =>
    set((s) => {
      if (!(messageId in s.suggestions)) return {};
      const next = { ...s.suggestions };
      delete next[messageId];
      return { suggestions: next };
    }),

  reset: () =>
    set({
      isStreaming: false,
      currentStreamContent: "",
      currentStatus: "",
      messages: [],
      conversationId: null,
      endpointName: null,
      error: null,
      timelineEvents: [],
      pendingToolChoice: null,
      charts: {},
      suggestions: {},
      streamingAssistantId: null,
    }),
}));

// -- Selector helpers --
//
// Components only need the artifact for the message they are rendering, so
// we expose tiny memoized selectors instead of having every bubble subscribe
// to the entire ``charts`` / ``suggestions`` map. Zustand will skip the
// re-render when the returned reference is stable.

// NOTE: returns the *primary* (idx=0) chart for back-compat with the
// single-chart render path. Multi-chart callers should use
// ``useChartsFor`` instead so they pick up the full stacked list.
export function useChartFor(messageId: string | null | undefined): ChartArtifact | null {
  return useChatStore((s) => {
    if (!messageId) return null;
    const list = s.charts[messageId];
    return list && list.length > 0 ? list[0] : null;
  });
}

export function useChartsFor(
  messageId: string | null | undefined,
): ChartArtifact[] {
  return useChatStore((s) =>
    messageId ? s.charts[messageId] ?? EMPTY_CHART_LIST : EMPTY_CHART_LIST,
  );
}

// Stable empty reference so Zustand selectors don't churn when a message
// has no charts attached. Prevents a re-render on every unrelated update.
const EMPTY_CHART_LIST: ChartArtifact[] = [];

export function useSuggestionsFor(
  messageId: string | null | undefined,
): SuggestionsPayload | null {
  return useChatStore((s) =>
    messageId ? s.suggestions[messageId] ?? null : null,
  );
}

export type { SuggestionSource };
