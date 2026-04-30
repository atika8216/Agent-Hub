import { memo } from "react";
import { MessageSquarePlus } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { Button } from "@/components/ui/button";
import { iosTween } from "@/lib/motion";
import type { Conversation } from "@/lib/types";

import { ConversationRow } from "./conversation-row";

interface ConversationSidebarProps {
  conversations: Conversation[];
  activeId?: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  isLoading?: boolean;
}

export const ConversationSidebar = memo(function ConversationSidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  isLoading,
}: ConversationSidebarProps) {
  return (
    <div className="hidden h-full shrink-0 flex-col border-r border-border bg-surface md:flex md:w-[240px] lg:w-[280px]">
      <div className="p-3">
        <Button
          variant="secondary"
          className="w-full justify-start gap-2"
          onClick={onNew}
        >
          <MessageSquarePlus className="h-4 w-4" />
          New Chat
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="space-y-2 p-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-14 animate-pulse rounded-lg bg-surface-elevated" />
            ))}
          </div>
        ) : conversations.length === 0 ? (
          <p className="px-4 py-8 text-center text-xs text-text-muted">
            No conversations yet
          </p>
        ) : (
          /*
           * Wrapping the row list in AnimatePresence + motion.li
           * gives us iMessage-style behavior: deletes collapse the
           * row's height + opacity rather than blink out, and a new
           * conversation slides in from above instead of popping
           * into place. ``layout`` keeps neighbours animating into
           * the freed space.
           */
          <ul className="space-y-0.5 px-2 py-1">
            <AnimatePresence initial={false}>
              {conversations.map((conv) => (
                <motion.li
                  key={conv.id}
                  layout
                  initial={{ opacity: 0, y: -6 }}
                  animate={{ opacity: 1, y: 0, transition: iosTween }}
                  exit={{
                    opacity: 0,
                    height: 0,
                    transition: iosTween,
                  }}
                  className="overflow-hidden"
                >
                  <ConversationRow
                    conversation={conv}
                    isActive={conv.id === activeId}
                    density="compact"
                    onSelect={onSelect}
                    onDelete={onDelete}
                  />
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        )}
      </div>
    </div>
  );
});
