import { useCallback, useEffect, useRef } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";

import { useAgent } from "@/hooks/use-agents";
import { useAdminSettings } from "@/hooks/use-admin";
import { useChat } from "@/hooks/use-chat";
import { agentTypeFromEndpointName } from "@/lib/agent-type";
import {
  useConversations,
  useDeleteConversation,
} from "@/hooks/use-conversations";
import { useScrollAnchor } from "@/hooks/use-scroll-anchor";
import { AgentHeader } from "@/components/chat/agent-header";
import { ChatInput } from "@/components/chat/chat-input";
import { ConversationSidebar } from "@/components/chat/conversation-sidebar";
import { EmptyConversationState } from "@/components/chat/empty-conversation-state";
import { ErrorBubble } from "@/components/chat/error-bubble";
import { MessageBubble } from "@/components/chat/message-bubble";
import { ScrollToBottomFab } from "@/components/chat/scroll-to-bottom-fab";
import { StreamingMessage } from "@/components/chat/streaming-message";
import { ToolCallBlock } from "@/components/chat/tool-call-block";
import { ToolPicker } from "@/components/chat/tool-picker";

interface ChatNewSearch {
  agent?: string;
}

export const Route = createFileRoute("/_sidebar/chat/new")({
  component: NewChatPage,
  validateSearch: (search: Record<string, unknown>): ChatNewSearch => {
    const raw = search.agent;
    return { agent: typeof raw === "string" && raw ? raw : undefined };
  },
});

function NewChatPage() {
  const navigate = useNavigate();
  const scrollRef = useRef<HTMLDivElement>(null);
  const { agent: agentSearch } = Route.useSearch();
  const agentParam = agentSearch ?? null;

  const { data: agent, isLoading: agentLoading } = useAgent(agentParam ?? undefined);
  const { data: convList, isLoading: listLoading } = useConversations();
  const { memoryMode } = useAdminSettings();
  const deleteMutation = useDeleteConversation();
  const chat = useChat();

  useEffect(() => {
    chat.reset();
    if (agentParam) {
      chat.setEndpoint(agentParam);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on mount
  }, [agentParam]);

  // Flip the URL to /chat/$id without unmounting this route. Calling
  // navigate() here would tear down the component mid-stream, abort the
  // SSE fetch (the AbortController is owned by useChat), and the next
  // page would re-seed messages from the DB (which doesn't yet have the
  // assistant reply). Using replaceState keeps the active stream + store
  // intact while still giving the user a stable, shareable URL and a
  // browser-back that returns to /catalog rather than the empty new chat.
  //
  // The ``skipFirstFire`` ref is load-bearing now that we SPA-navigate
  // into this route. The zustand ``useChat`` store persists across
  // mounts, so the very first render can observe a ``conversationId``
  // left behind by a previously-open chat. Without the guard, this
  // effect would immediately replaceState the URL to the stale id and
  // the empty-state render would dump the user back into whatever
  // conversation they last touched instead of a fresh new-chat screen.
  // We only start rewriting the URL after the first effect firing,
  // i.e. once ``chat.reset()`` above has flushed the stale store and
  // subsequent conversationId changes are produced by *this* mount.
  const skipFirstFire = useRef(true);
  useEffect(() => {
    if (skipFirstFire.current) {
      skipFirstFire.current = false;
      return;
    }
    if (chat.conversationId) {
      const target = `/chat/${chat.conversationId}`;
      if (window.location.pathname !== target) {
        window.history.replaceState({}, "", target);
      }
    }
  }, [chat.conversationId]);

  // Same rAF-pinned anchor as chat.$conversationId.tsx; the pin is
  // released the moment the user scrolls up.
  useScrollAnchor(scrollRef, [chat.messages.length, chat.currentStreamContent]);

  const handleSend = useCallback(
    (message: string) => {
      if (!agentParam) return;
      chat.sendMessage(agentParam, message);
    },
    [agentParam, chat],
  );

  const handleChooseTool = useCallback(
    (toolName: string) => {
      if (!agentParam) return;
      chat.setPendingToolChoice(null);
      // Resend the last user message with the chosen tool. The backend will
      // persist the selection against the conversation and reuse it for
      // subsequent turns.
      const lastUser = [...chat.messages].reverse().find((m) => m.role === "user");
      if (lastUser) {
        chat.sendMessage(agentParam, lastUser.content, chat.conversationId, toolName);
      }
    },
    [agentParam, chat],
  );

  const handleSelectConversation = useCallback(
    (id: string) => {
      navigate({ to: "/chat/$conversationId", params: { conversationId: id } });
    },
    [navigate],
  );

  const handleNewChat = useCallback(() => {
    if (agentParam) {
      // Reset the local chat store, then SPA-navigate back to
      // /chat/new with the same agent. Using navigate() (instead of
      // window.location.href) preserves the QueryClient cache, so the
      // catalog / conversation list / agent detail pages stay warm.
      chat.reset();
      navigate({
        to: "/chat/new",
        search: { agent: agentParam },
      });
    } else {
      navigate({ to: "/catalog" });
    }
  }, [agentParam, chat, navigate]);

  const handleDelete = useCallback(
    (id: string) => {
      deleteMutation.mutate(id);
    },
    [deleteMutation],
  );

  // /chat/new without an ``?agent=`` query string is a dead-end -- the
  // sidebar used to land users here when they had no active agent. We
  // now treat that entry point as the conversation list and send them
  // to /chat so they can pick up a recent conversation (or hop into the
  // catalog from the empty state). ``replace: true`` keeps the browser
  // back button sane.
  useEffect(() => {
    if (!agentParam) {
      navigate({ to: "/chat", replace: true });
    }
  }, [agentParam, navigate]);

  if (!agentParam) {
    return null;
  }

  const displayName = agent?.display_name ?? agentParam;
  // Prefer the backend agent_type when present; otherwise derive from the
  // endpoint-name prefix so Genie/UC/MCP sessions don't flash "Supervisor
  // Agent" while the detail query is in flight (or when the agent lookup
  // is skipped entirely because the catalog hasn't discovered this row
  // yet). Falls back to MAS only when the prefix is unrecognized.
  const agentType =
    agent?.agent_type ?? agentTypeFromEndpointName(agentParam) ?? "MAS";

  const isEmpty = chat.messages.length === 0 && !chat.isStreaming;

  return (
    <div className="flex h-full min-w-0">
      <ConversationSidebar
        conversations={convList?.conversations ?? []}
        activeId={chat.conversationId ?? undefined}
        onSelect={handleSelectConversation}
        onNew={handleNewChat}
        onDelete={handleDelete}
        isLoading={listLoading}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {!agentLoading && (
          <AgentHeader
            displayName={displayName}
            endpointName={agentParam}
            agentType={agentType}
            hasAccess={agent?.has_access}
            memoryMode={memoryMode}
          />
        )}

        <div ref={scrollRef} className="flex min-w-0 flex-1 flex-col overflow-y-auto px-4 py-6">
          {isEmpty ? (
            <EmptyConversationState
              displayName={displayName}
              agentType={agentType}
              description={agent?.description ?? null}
              endpointName={agentParam}
              onSelect={handleSend}
              onStop={chat.stop}
              isStreaming={chat.isStreaming}
              disabled={chat.isStreaming}
            />
          ) : (
            <div className="mx-auto w-full min-w-0 max-w-3xl space-y-4">
              {chat.messages.map((msg, idx) => {
                /*
                 * Mirrors the conversation page: only the most recent
                 * assistant bubble inherits the streaming layoutId,
                 * and only when no stream is in flight. See
                 * ``chat.$conversationId.tsx`` for the rationale.
                 */
                const isLastAssistant =
                  msg.role === "assistant" &&
                  idx === chat.messages.length - 1;
                const layoutId =
                  !chat.isStreaming && isLastAssistant && chat.conversationId
                    ? `stream-${chat.conversationId}`
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
                  conversationId={chat.conversationId}
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
                  if (!agentParam || !lastUser) return undefined;
                  return () => {
                    chat.setError(null);
                    chat.sendMessage(
                      agentParam,
                      lastUser.content,
                      chat.conversationId,
                    );
                  };
                })()}
                retryDisabled={chat.isStreaming}
              />
            </div>
          )}
        </div>

        {!isEmpty && (
          // Matches the conversation-view tray: gradient-pad rather
          // than a hard border so the messages fade into the composer,
          // with the scroll-to-bottom FAB anchored just above it and
          // the "Always review..." footer below.
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
              <ChatInput
                onSend={handleSend}
                onStop={chat.stop}
                isStreaming={chat.isStreaming}
                disabled={chat.isStreaming}
                placeholder={`Ask ${displayName}...`}
              />
              <p className="mt-1 text-center text-[0.6875rem] text-text-muted">
                Always review the accuracy of responses.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
