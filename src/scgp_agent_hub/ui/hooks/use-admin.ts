import { useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  type CatalogEntryUpdate,
  getAdminSettingsKey,
  listAdminCatalogKey,
  listAgentsKey,
  useGetAdminSettings,
  useListAdminCatalog,
  useUpdateAdminCatalogEntry,
  useUpdateAdminSetting,
} from "@/lib/api";
import type { MemoryMode } from "@/components/chat/agent-header";

const VALID_MODES: ReadonlyArray<MemoryMode> = [
  "off",
  "short_term",
  "long_term",
  "both",
];

function parseMode(value: unknown): MemoryMode {
  if (typeof value !== "string") return "short_term";
  const trimmed = value.trim().toLowerCase() as MemoryMode;
  return VALID_MODES.includes(trimmed) ? trimmed : "short_term";
}

export function useAdminSettings() {
  const query = useGetAdminSettings();
  const settings = query.data?.data?.settings ?? {};
  const memoryMode = useMemo<MemoryMode>(
    () => parseMode(settings["memory_mode"]),
    [settings],
  );
  return {
    ...query,
    settings,
    memoryMode,
  };
}

export function useMemoryMode(): MemoryMode | undefined {
  const { data, isLoading } = useGetAdminSettings();
  if (isLoading || !data) return undefined;
  return parseMode(data.data.settings["memory_mode"]);
}

export function useUpdateMemoryMode() {
  const queryClient = useQueryClient();
  const mutation = useUpdateAdminSetting({
    mutation: {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: getAdminSettingsKey() });
      },
    },
  });
  return {
    ...mutation,
    setMode: (mode: MemoryMode) =>
      mutation.mutate({ params: { key: "memory_mode" }, data: { value: mode } }),
  };
}

export function useAdminCatalog() {
  return useListAdminCatalog();
}

export function useUpdateCatalogEntry() {
  const queryClient = useQueryClient();
  const mutation = useUpdateAdminCatalogEntry({
    mutation: {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: listAdminCatalogKey() });
        // listAgents is the expensive user-facing catalog with per-agent
        // OBO access probes -- mark it stale so the next /catalog mount
        // refreshes, but don't block the admin's toggle interaction on
        // a full refetch.
        queryClient.invalidateQueries({
          queryKey: listAgentsKey(),
          refetchType: "none",
        });
      },
      onError: (err) => {
        toast.error("Failed to update agent visibility", {
          description: err instanceof Error ? err.message : String(err),
        });
      },
    },
  });
  return {
    ...mutation,
    updateEntry: (endpoint_name: string, data: CatalogEntryUpdate) =>
      mutation.mutate({ params: { endpoint_name }, data }),
  };
}
