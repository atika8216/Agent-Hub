import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  listConversationsKey,
  useDeleteConversation as useDeleteConversationApi,
  useGetConversation,
  useListConversations,
} from "@/lib/api";

export function useConversations() {
  const query = useListConversations();
  return {
    ...query,
    data: query.data?.data,
  };
}

export function useConversation(conversationId: string | undefined) {
  const query = useGetConversation({
    params: { conversation_id: conversationId ?? "" },
    query: { enabled: !!conversationId },
  });
  return {
    ...query,
    data: query.data?.data,
  };
}

export function useDeleteConversation() {
  const queryClient = useQueryClient();
  const mutation = useDeleteConversationApi({
    mutation: {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: listConversationsKey() });
        toast.success("Conversation deleted");
      },
      onError: (err) => {
        toast.error("Failed to delete conversation", {
          description: err instanceof Error ? err.message : String(err),
        });
      },
    },
  });
  return {
    ...mutation,
    mutate: (
      conversationId: string,
      options?: Parameters<typeof mutation.mutate>[1],
    ) =>
      mutation.mutate(
        { params: { conversation_id: conversationId } },
        options,
      ),
    mutateAsync: (
      conversationId: string,
      options?: Parameters<typeof mutation.mutateAsync>[1],
    ) =>
      mutation.mutateAsync(
        { params: { conversation_id: conversationId } },
        options,
      ),
  };
}
