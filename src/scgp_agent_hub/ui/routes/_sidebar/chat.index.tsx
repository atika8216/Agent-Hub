import { useCallback } from "react";
import {
  Link,
  createFileRoute,
  useNavigate,
} from "@tanstack/react-router";
import { MessageSquare, MessageSquarePlus } from "lucide-react";
import { motion } from "motion/react";

import { Button } from "@/components/ui/button";
import { ConversationRow } from "@/components/chat/conversation-row";
import {
  useConversations,
  useDeleteConversation,
} from "@/hooks/use-conversations";
import { iosTween } from "@/lib/motion";

export const Route = createFileRoute("/_sidebar/chat/")({
  component: ChatIndexPage,
});

function ChatIndexPage() {
  const navigate = useNavigate();
  const { data, isLoading } = useConversations();
  const deleteMutation = useDeleteConversation();

  const conversations = data?.conversations ?? [];
  const count = conversations.length;

  const handleSelect = useCallback(
    (id: string) => {
      navigate({
        to: "/chat/$conversationId",
        params: { conversationId: id },
      });
    },
    [navigate],
  );

  const handleDelete = useCallback(
    (id: string) => {
      deleteMutation.mutate(id);
    },
    [deleteMutation],
  );

  return (
    <div className="mx-auto w-full max-w-3xl space-y-5 p-6 md:p-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <h1
            className={[
              "text-[1.75rem] font-bold leading-[1.15] tracking-[-0.025em]",
              "font-[family-name:var(--font-display)] text-text-primary",
            ].join(" ")}
          >
            Your conversations
          </h1>
          <p className="text-[0.9375rem] text-text-secondary">
            {isLoading
              ? "Loading…"
              : count === 0
                ? "Pick an agent from the catalog to start your first chat."
                : count === 1
                  ? "1 conversation"
                  : `${count} conversations`}
          </p>
        </div>
        <Button asChild size="default" className="gap-2 sm:shrink-0">
          <Link to="/catalog">
            <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
            New chat
          </Link>
        </Button>
      </header>

      {isLoading ? (
        <ConversationListSkeleton />
      ) : count === 0 ? (
        <EmptyConversations />
      ) : (
        // No ``AnimatePresence`` wrapper around this list: the
        // optimistic ``useDeleteConversation`` mutation removes the
        // row from the cached list synchronously, so row removals are
        // instant. Wrapping 30+ rows in ``AnimatePresence`` with
        // ``layout`` + ``exit`` props was also a repro path for the
        // outer-shell exit callback stalling when the user navigated
        // mid-animation. Entrance fade is preserved per-row via plain
        // ``motion.li``.
        <ul className="space-y-2">
          {conversations.map((conv) => (
            <motion.li
              key={conv.id}
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0, transition: iosTween }}
            >
              <ConversationRow
                conversation={conv}
                density="comfortable"
                onSelect={handleSelect}
                onDelete={handleDelete}
              />
            </motion.li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ConversationListSkeleton() {
  return (
    <ul className="space-y-2" aria-hidden="true">
      {Array.from({ length: 4 }).map((_, i) => (
        <li
          key={i}
          className="h-[72px] animate-pulse rounded-[var(--radius-md)] border border-border bg-surface-elevated"
        />
      ))}
    </ul>
  );
}

function EmptyConversations() {
  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-[var(--radius-md)] border border-dashed border-border bg-surface px-6 py-14 text-center">
      <span
        aria-hidden="true"
        className="flex h-12 w-12 items-center justify-center rounded-full bg-surface-elevated text-text-muted"
      >
        <MessageSquare className="h-6 w-6" />
      </span>
      <div className="space-y-1">
        <p className="text-[1rem] font-semibold text-text-primary">
          No conversations yet
        </p>
        <p className="text-[0.875rem] text-text-secondary">
          Start a chat from the agent catalog — your history will show up here.
        </p>
      </div>
      <Button asChild className="gap-2">
        <Link to="/catalog">
          <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
          Browse agents
        </Link>
      </Button>
    </div>
  );
}
