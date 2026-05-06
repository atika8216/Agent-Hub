import {
  useCheckAgentAccess,
  useDiscoverAgents as useDiscoverAgentsApi,
  useGetAgent,
  useListAgents,
  useListGenieSpaces,
  listAdminCatalogKey,
  listAgentsKey,
} from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

export function useAgents(search?: string, type?: string) {
  const params = {
    search: search ?? null,
    type: type ?? null,
  };
  const query = useListAgents({ params });
  return {
    ...query,
    data: query.data?.data,
  };
}

export function useAgent(endpointName: string | undefined) {
  const query = useGetAgent({
    params: { endpoint_name: endpointName ?? "" },
    query: { enabled: !!endpointName },
  });
  return {
    ...query,
    data: query.data?.data,
  };
}

export function useAgentAccess(endpointName: string | undefined) {
  const query = useCheckAgentAccess({
    params: { endpoint_name: endpointName ?? "" },
    query: { enabled: !!endpointName, staleTime: 60_000 },
  });
  return {
    ...query,
    data: query.data?.data,
  };
}

export function useDiscoverAgents() {
  const queryClient = useQueryClient();
  return useDiscoverAgentsApi({
    mutation: {
      onSuccess: (response) => {
        queryClient.invalidateQueries({ queryKey: listAgentsKey() });
        queryClient.invalidateQueries({ queryKey: listAdminCatalogKey() });
        const summary = response?.data;
        const added = summary?.new ?? 0;
        const updated = summary?.updated ?? 0;
        const skipped = summary?.skipped ?? 0;
        toast.success("Discovery complete", {
          description: `${added} added · ${updated} updated · ${skipped} skipped`,
        });
      },
      onError: (err) => {
        toast.error("Discovery failed", {
          description: err instanceof Error ? err.message : String(err),
        });
      },
    },
  });
}

/**
 * Read-through list of Genie Spaces the caller can see, surfaced as
 * first-class catalog entries alongside MAS / KA / Agent endpoints.
 */
export function useGenieSpaces() {
  const query = useListGenieSpaces({
    query: { staleTime: 60_000 },
  });
  return {
    ...query,
    data: query.data?.data,
  };
}
