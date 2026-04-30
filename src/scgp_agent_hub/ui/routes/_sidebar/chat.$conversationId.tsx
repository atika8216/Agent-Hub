import { useCallback, useEffect, useMemo, useRef } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";

import { useAdminSettings } from "@/hooks/use-admin";
import type { Message } from "@/lib/types";
import { useChat } from "@/hooks/use-chat";
import {
  useConversation,
  useConversations,
  useDeleteConversation,
} from "@/hooks/use-conversations";
import { useScrollAnchor } from "@/hooks/use-scroll-anchor";
import { AgentHeader } from "@/components/chat/agent-header";
import { ChatInput } from "@/components/chat/chat-input";
import { ConversationSidebar } from "@/components/chat/conversation-sidebar";
import { ErrorBubble } from "@/components/chat/error-bubble";
import { MessageBubble } from "@/components/chat/message-bubble";
import { PinnedQuestionsBar } from "@/components/chat/pinned-questions-bar";
import { ScrollToBottomFab } from "@/components/chat/scroll-to-bottom-fab";
import { StreamingMessage } from "@/components/chat/streaming-message";
import { SuggestionChips } from "@/components/chat/suggestion-chips";
import { ToolCallBlock } from "@/components/chat/tool-call-block";
import { ToolPicker } from "@/components/chat/tool-picker";

export const Route = createFileRoute("/_sidebar/chat/$conversationId")({
  component: ChatConversationPage,
});

function ChatConversationPage() {
  const { conversationId } = Route.useParams();
  const navigate = useNavigate();
  const scrollRef = useRef<HTMLDivElement>(null);

  const { data: convDetail, isLoading: convLoading } = useConversation(conversationId);
  const { data: convList, isLoading: listLoading } = useConversations();
  const { memoryMode } = useAdminSettings();
  const deleteMutation = useDeleteConversation();
  const chat = useChat();

  useEffect(() => {
    if (!convDetail) return;

    // Don't trample an in-flight stream. When the user sends a message
    // from /chat/new, we replaceState the URL to /chat/$id mid-stream.
    // This component then mounts and convDetail loads -- but the DB
    // doesn't yet have the assistant reply, so re-seeding messages from
    // convDetail would erase the optimistic user bubble + every token
    // the store has accumulated so far. The store is the source of
    // truth while a stream is live for THIS conversation.
    const isOwnLiveStream =
      chat.isStreaming && chat.conversationId === conversationId;
    if (isOwnLiveStream) {
      chat.setEndpoint(convDetail.endpoint_name);
      return;
    }

    const normalized: Message[] = (convDetail.messages ?? []).map((m) => ({
      id: m.id,
      role: m.role === "assistant" ? "assistant" : "user",
      content: m.content,
      metadata: m.metadata,
      created_at: m.created_at,
      // Phase 4: surface chart_id / has_suggestions on reload so the
      // assistant bubble can lazy-fetch the artifact and the suggestion
      // chips can rehydrate without a per-message ping.
      chart_id: m.chart_id ?? null,
      has_suggestions: Boolean(m.has_suggestions),
    }));
    chat.setMessages(normalized);
    chat.setEndpoint(convDetail.endpoint_name);
    chat.setConversationId(conversationId);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when convDetail loads
  }, [convDetail, conversationId]);

  // Pin to bottom only while the user is already there; stop the
  // moment they scroll up to read earlier content. See useScrollAnchor
  // for the rAF-based implementation that replaces the older
  // ``scrollTo({ behavior: "smooth" })`` per-token cascade.
  useScrollAnchor(scrollRef, [chat.messages.length, chat.currentStreamContent]);

  const handleSend = useCallback(
    (message: string) => {
      if (!convDetail) return;
      chat.sendMessage(convDetail.endpoint_name, message, conversationId);
    },
    [convDetail, conversationId, chat],
  );

  const handleChooseTool = useCallback(
    (toolName: string) => {
      if (!convDetail) return;
      chat.setPendingToolChoice(null);
      const lastUser = [...chat.messages].reverse().find((m) => m.role === "user");
      if (lastUser) {
        chat.sendMessage(
          convDetail.endpoint_name,
          lastUser.content,
          conversationId,
          toolName,
        );
      }
    },
    [convDetail, conversationId, chat],
  );

  const handleSelectConversation = useCallback(
    (id: string) => {
      navigate({ to: "/chat/$conversationId", params: { conversationId: id } });
    },
    [navigate],
  );

  const handleNewChat = useCallback(() => {
    const ep = convDetail?.endpoint_name;
    if (ep) {
      // SPA nav preserves the QueryClient cache; the older
      // window.location.href approach was forcing a full browser
      // reload, which wiped every warm query (conversation list,
      // agents list, admin settings) on the way out.
      chat.reset();
      navigate({ to: "/chat/new", search: { agent: ep } });
    } else {
      navigate({ to: "/catalog" });
    }
  }, [convDetail, chat, navigate]);

  const handleDelete = useCallback(
    (id: string) => {
      deleteMutation.mutate(id, {
        onSuccess: () => {
          if (id === conversationId) {
            navigate({ to: "/catalog" });
          }
        },
      });
    },
    [deleteMutation, conversationId, navigate],
  );

  const displayName = convDetail?.display_name ?? convDetail?.endpoint_name ?? "";
  const endpointName = convDetail?.endpoint_name ?? "";

  // Phase 4: locate the most recent assistant message id so the
  // suggestion chips know which message to fetch / display chips for.
  // We fall back to the last user message id when the assistant is
  // mid-stream so the bar can hide cleanly until the new chips arrive.
  const lastAssistantId = useMemo(() => {
    for (let i = chat.messages.length - 1; i >= 0; i -= 1) {
      const m = chat.messages[i];
      if (m.role === "assistant") return m.id;
    }
    return null;
  }, [chat.messages]);
  const lastAssistantHasSuggestions = useMemo(() => {
    for (let i = chat.messages.length - 1; i >= 0; i -= 1) {
      const m = chat.messages[i];
      if (m.role === "assistant") return Boolean(m.has_suggestions);
    }
    return false;
  }, [chat.messages]);

  return (
    <div className="flex h-full min-w-0">
      <ConversationSidebar
        conversations={convList?.conversations ?? []}
        activeId={conversationId}
        onSelect={handleSelectConversation}
        onNew={handleNewChat}
        onDelete={handleDelete}
        isLoading={listLoading}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {!convLoading && convDetail && (
          <AgentHeader
            displayName={displayName}
            endpointName={endpointName}
            memoryMode={memoryMode}
          />
        )}

        <div ref={scrollRef} className="flex min-w-0 flex-1 flex-col overflow-y-auto px-4 py-6">
          {convLoading ? (
            <div className="flex flex-1 items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-text-muted" />
            </div>
          ) : (
            <div className="mx-auto w-full min-w-0 max-w-3xl space-y-4">
              {chat.messages.map((msg, idx) => {
                /*
                 * Hand the just-finalized assistant bubble the same
                 * ``layoutId`` the StreamingMessage was using, so motion
                 * morphs the streaming surface into the final bubble
                 * without a swap-flash. We only do this when no stream
                 * is currently active -- otherwise two nodes would
                 * claim the same layoutId and motion would clobber the
                 * inbound StreamingMessage.
                 */
                const isLastAssistant =
                  msg.role === "assistant" &&
                  idx === chat.messages.length - 1;
                const layoutId =
                  !chat.isStreaming && isLastAssistant
                    ? `stream-${conversationId}`
                    : undefined;
                return (
                  <MessageBubble
                    key={msg.id}
                    message={msg}
                    layoutId={layoutId}
                  />
                );
              })}
              {chat.timelineEvents
                .filter((ev) => ev.kind === "tool_call")
                .map((ev) => {
                  const call = ev as Extract<
                    typeof ev,
                    { kind: "tool_call" }
                  >;
                  const result = chat.timelineEvents.find(
                    (r) =>
                      r.kind === "tool_result" &&
                      r.name === call.name &&
                      r.created_at >= call.created_at,
                  ) as
                    | Extract<typeof ev, { kind: "tool_result" }>
                    | undefined;
                  return (
                    <ToolCallBlock
                      key={call.id}
                      callEvent={call}
                      resultEvent={result}
                    />
                  );
                })}
              {chat.pendingToolChoice && (
                <ToolPicker
                  tools={chat.pendingToolChoice}
                  onChoose={handleChooseTool}
                  onCancel={() => chat.setPendingToolChoice(null)}
                />
              )}
              {chat.isStreaming && (
                <StreamingMessage
                  content={chat.currentStreamContent}
                  conversationId={conversationId}
                  statusLabel={chat.currentStatus}
                />
              )}
            </div>
          )}

          {chat.error && (
            <div className="mx-auto mt-4 w-full max-w-3xl">
              <ErrorBubble
                message={chat.error}
                onRetry={(() => {
                  const lastUser = [...chat.messages]
                    .reverse()
                    .find((m) => m.role === "user");
                  if (!convDetail || !lastUser) return undefined;
                  return () => {
                    chat.setError(null);
                    chat.sendMessage(
                      convDetail.endpoint_name,
                      lastUser.content,
                      conversationId,
                    );
                  };
                })()}
                retryDisabled={chat.isStreaming}
              />
            </div>
          )}
        </div>

        {/*
         * De-weighted composer tray: no hard top border, just a
         * background-gradient fade so the last message "melts" into the
         * composer (matches the reference Databricks chat-ui). The
         * ScrollToBottomFab is absolute-positioned inside this wrapper
         * so it sits ~20px above the composer without needing any
         * additional layout math. ``relative`` on the outer div is the
         * FAB's positioning context.
         */}
        <div
          className={[
            "relative px-4 pb-3 pt-6",
            "bg-gradient-to-t from-background via-background to-background/0",
          ].join(" ")}
        >
          <div className="mx-auto flex w-full min-w-0 max-w-3xl flex-col gap-2">
            <ScrollToBottomFab
              scrollRef={scrollRef}
              resetKey={`${chat.messages.length}:${chat.isStreaming}`}
            />
            {endpointName && (
              <PinnedQuestionsBar
                endpointName={endpointName}
                onPick={handleSend}
                disabled={chat.isStreaming || convLoading}
              />
            )}
            <SuggestionChips
              messageId={lastAssistantId}
              hasReloadSuggestions={lastAssistantHasSuggestions}
              onPick={handleSend}
              disabled={chat.isStreaming || convLoading}
            />
            <ChatInput
              onSend={handleSend}
              onStop={chat.stop}
              isStreaming={chat.isStreaming}
              disabled={chat.isStreaming || convLoading}
              placeholder={displayName ? `Ask ${displayName}...` : "Type a message..."}
            />
            <p className="mt-1 text-center text-[0.6875rem] text-text-muted">
              Always review the accuracy of responses.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
