import { useState } from "react";
import {
  Loader2,
  PackagePlus,
  Plug,
  Plus,
  ServerCrash,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";

import {
  listAdminCatalogKey,
  listAgentsKey,
  listManualUCEndpointsKey,
  useListManualUCEndpoints,
  useRegisterManualUCEndpoint,
  useUnregisterManualUCEndpoint,
  type CatalogEntryOut,
  type ManualUCKindApi,
  type ManualUCObjectTypeApi,
} from "@/lib/api";
import { agentTypeLabel, agentTypeVariant } from "@/lib/agent-type";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

/**
 * Manual UC endpoint registration -- Option C fallback for workspaces where
 * ``system.information_schema.function_tags`` / ``connection_tags`` aren't
 * available. Writes ``uc:<full>`` / ``mcp:<full>`` rows directly into
 * ``catalog_config`` via ``/admin/uc-endpoints`` and exposes them as
 * deletable chips so admins can undo a mistake in one click.
 *
 * Shape-compatible with the tag-discovery path: the backend flags the
 * row with ``metadata_json.manual = true`` and uses the same
 * ``invoke_shape`` strings the chat dispatcher already understands.
 */
export function ManualEndpointsCard() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, refetch } = useListManualUCEndpoints();
  const entries: CatalogEntryOut[] = data?.data ?? [];

  // Invalidation strategy:
  //
  // - listManualUCEndpoints + listAdminCatalog are small and visible on
  //   this page, so we mark them stale AND refetch immediately (default
  //   invalidateQueries behavior).
  // - listAgents drives the user /catalog route and runs an OBO access
  //   probe per agent (slow, especially on MAS-heavy workspaces). We
  //   only want to mark it stale so the next /catalog mount picks up
  //   the new row -- refetching now while the admin is still on
  //   /admin/catalog would lock the page on a non-visible network call
  //   and the user feels it as "back nav is slow".
  const register = useRegisterManualUCEndpoint({
    mutation: {
      onSuccess: (result) => {
        toast.success("Endpoint registered", {
          description: result.data.display_name ?? result.data.endpoint_name,
        });
        queryClient.invalidateQueries({ queryKey: listManualUCEndpointsKey() });
        queryClient.invalidateQueries({ queryKey: listAdminCatalogKey() });
        queryClient.invalidateQueries({
          queryKey: listAgentsKey(),
          refetchType: "none",
        });
        resetForm();
      },
      onError: (err) => {
        toast.error("Registration failed", {
          description: formatApiError(err),
        });
      },
    },
  });

  const unregister = useUnregisterManualUCEndpoint({
    mutation: {
      onSuccess: () => {
        toast.success("Endpoint removed");
        queryClient.invalidateQueries({ queryKey: listManualUCEndpointsKey() });
        queryClient.invalidateQueries({ queryKey: listAdminCatalogKey() });
        queryClient.invalidateQueries({
          queryKey: listAgentsKey(),
          refetchType: "none",
        });
      },
      onError: (err) => {
        toast.error("Delete failed", { description: formatApiError(err) });
      },
    },
  });

  // Form state. We don't use react-hook-form / zod to keep this card
  // self-contained -- the surface is five fields and the validation
  // rules are enforced on the server anyway.
  const [ucFullName, setUcFullName] = useState("");
  const [objectType, setObjectType] = useState<ManualUCObjectTypeApi>("function");
  const [kind, setKind] = useState<ManualUCKindApi>("http");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");

  const resetForm = () => {
    setUcFullName("");
    setObjectType("function");
    setKind("http");
    setDisplayName("");
    setDescription("");
  };

  const expectedSegments = objectType === "function" ? 3 : 2;
  const trimmedFull = ucFullName.trim();
  const segmentCount = trimmedFull
    ? trimmedFull.split(".").filter((p) => p.length > 0).length
    : 0;
  // Client-side gate -- catches 90% of typos before the POST. The real
  // validation lives in ``admin_service._validate_full_name``.
  const formValid =
    trimmedFull.length > 0 && segmentCount === expectedSegments;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!formValid) return;
    register.mutate({
      uc_full_name: trimmedFull,
      object_type: objectType,
      kind,
      display_name: displayName.trim() || undefined,
      description: description.trim() || undefined,
    });
  };

  const handleDelete = (entry: CatalogEntryOut) => {
    if (
      !window.confirm(
        `Remove "${entry.display_name ?? entry.endpoint_name}" from the catalog?`,
      )
    ) {
      return;
    }
    unregister.mutate({ endpoint_name: entry.endpoint_name });
  };

  return (
    <Card>
      <CardContent className="space-y-5 p-5">
        <div className="flex items-start gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary/10 text-primary">
            <Plug className="h-[18px] w-[18px]" />
          </div>
          <div className="flex-1">
            <h2
              className={[
                "text-[1.0625rem] font-semibold tracking-[-0.01em]",
                "font-[family-name:var(--font-display)] text-text-primary",
              ].join(" ")}
            >
              Manual endpoint registration
            </h2>
            <p className="mt-0.5 text-[0.8125rem] leading-[1.5] text-text-muted">
              Register a Unity Catalog function or connection directly when
              tag-discovery isn't available in your workspace. Functions take
              three segments (
              <code className="font-[family-name:var(--font-mono)] text-[0.75rem]">
                catalog.schema.function
              </code>
              ); connections take two (
              <code className="font-[family-name:var(--font-mono)] text-[0.75rem]">
                catalog.connection
              </code>
              ).
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <Field
              label="UC full name"
              description={
                objectType === "function"
                  ? "Three segments (catalog.schema.function)"
                  : "Two segments (catalog.connection)"
              }
            >
              <Input
                type="text"
                value={ucFullName}
                onChange={(e) => setUcFullName(e.target.value)}
                placeholder={
                  objectType === "function"
                    ? "main.default.ask_support"
                    : "main.my_mcp_connection"
                }
                className="font-[family-name:var(--font-mono)] text-[0.8125rem]"
                autoComplete="off"
                spellCheck={false}
              />
            </Field>
            <Field
              label="Display name"
              description="Shown on the catalog tile. Leave blank to auto-generate."
            >
              <Input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Auto-generated from the leaf name"
                maxLength={100}
              />
            </Field>
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <Field
              label="Object type"
              description="Governs segment count and the UC invocation shape."
            >
              <RadioGroup
                value={objectType}
                onChange={(v) => setObjectType(v as ManualUCObjectTypeApi)}
                options={[
                  { value: "function", label: "Function" },
                  { value: "connection", label: "Connection" },
                ]}
              />
            </Field>
            <Field
              label="Kind"
              description="HTTP routes through SQL Statements; MCP uses the MCP invoker."
            >
              <RadioGroup
                value={kind}
                onChange={(v) => setKind(v as ManualUCKindApi)}
                options={[
                  { value: "http", label: "HTTP" },
                  { value: "mcp", label: "MCP" },
                ]}
              />
            </Field>
          </div>

          <Field
            label="Description"
            description="Optional. Rendered below the display name on the catalog tile."
          >
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What does this endpoint do? When should someone call it?"
              maxLength={500}
              rows={2}
              className={[
                "block w-full rounded-[var(--radius-sm)]",
                "border border-border bg-surface-elevated",
                "px-3 py-2 text-[0.9375rem] leading-[1.35] text-text-primary",
                "placeholder:text-text-muted",
                "transition-[border-color,box-shadow] duration-150",
                "focus:border-info focus:outline-none focus:ring-2 focus:ring-info/30",
                "disabled:pointer-events-none disabled:opacity-50",
                "resize-none",
              ].join(" ")}
            />
          </Field>

          <div className="flex items-center justify-between">
            <div className="text-[0.75rem] text-text-muted">
              {trimmedFull && !formValid
                ? `Expected ${expectedSegments} dot-separated segments, got ${segmentCount}.`
                : " "}
            </div>
            <Button
              type="submit"
              size="sm"
              disabled={!formValid || register.isPending}
            >
              {register.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
              Register endpoint
            </Button>
          </div>
        </form>

        <div className="border-t border-border pt-4">
          <h3 className="mb-2 text-[0.875rem] font-semibold text-text-primary">
            Registered manually
            {entries.length > 0 && (
              <span className="ml-1.5 text-[0.75rem] font-normal text-text-muted">
                ({entries.length})
              </span>
            )}
          </h3>
          {isLoading ? (
            <div className="flex h-16 items-center justify-center">
              <Loader2 className="h-4 w-4 animate-spin text-text-muted" />
            </div>
          ) : isError ? (
            <div className="flex items-center gap-2 rounded-[var(--radius-sm)] border border-error/30 bg-error/5 p-3 text-xs text-error">
              <ServerCrash className="h-4 w-4" />
              <span className="flex-1">
                Failed to load manually registered endpoints.
              </span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => refetch()}
              >
                Retry
              </Button>
            </div>
          ) : entries.length === 0 ? (
            <div className="flex items-center gap-2 rounded-[var(--radius-sm)] border border-dashed border-border bg-surface p-4 text-xs text-text-muted">
              <PackagePlus className="h-4 w-4" />
              No manually registered endpoints yet. Use the form above to
              register your first UC function or MCP connection.
            </div>
          ) : (
            <ul className="divide-y divide-border rounded-[var(--radius-sm)] border border-border">
              {entries.map((entry) => (
                <li
                  key={entry.endpoint_name}
                  className="flex items-center gap-3 px-3 py-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[0.875rem] font-medium text-text-primary">
                        {entry.display_name || entry.endpoint_name}
                      </span>
                      <Badge
                        variant={agentTypeVariant(entry.agent_type)}
                        shape="pill"
                      >
                        {agentTypeLabel(entry.agent_type)}
                      </Badge>
                    </div>
                    <div className="mt-0.5 truncate font-[family-name:var(--font-mono)] text-[0.6875rem] text-text-muted">
                      {entry.endpoint_name}
                    </div>
                  </div>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handleDelete(entry)}
                    disabled={unregister.isPending}
                    aria-label={`Remove ${entry.display_name ?? entry.endpoint_name}`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function Field({
  label,
  description,
  children,
}: {
  label: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <div className="mb-1 text-[0.75rem] font-medium text-text-primary">
        {label}
      </div>
      {children}
      <div className="mt-1 text-[0.6875rem] text-text-muted">{description}</div>
    </label>
  );
}

function RadioGroup<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: Array<{ value: T; label: string }>;
}) {
  /*
   * Segmented-control style radio group. We roll it by hand instead of
   * pulling in radix because the rest of this page uses native inputs and
   * the adjacent tag-config-card. Visually mirrors iOS segmented picker
   * so it sits well beside the plain Inputs above.
   */
  return (
    <div
      role="radiogroup"
      className="inline-flex h-9 items-center rounded-[var(--radius-sm)] border border-border bg-surface-elevated p-0.5"
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            type="button"
            key={opt.value}
            role="radio"
            aria-checked={active}
            onClick={() => onChange(opt.value)}
            className={[
              "flex-1 rounded-[calc(var(--radius-sm)-2px)] px-3 text-[0.8125rem] font-medium",
              "transition-colors duration-150",
              active
                ? "bg-surface text-text-primary shadow-sm"
                : "text-text-muted hover:text-text-primary",
            ].join(" ")}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function formatApiError(err: unknown): string {
  if (err && typeof err === "object") {
    const anyErr = err as { body?: unknown; message?: string };
    if (anyErr.body && typeof anyErr.body === "object") {
      const body = anyErr.body as { detail?: unknown };
      if (typeof body.detail === "string") return body.detail;
    }
    if (typeof anyErr.message === "string") return anyErr.message;
  }
  return "Something went wrong.";
}
