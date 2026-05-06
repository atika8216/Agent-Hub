import { useEffect, useState } from "react";
import { Loader2, Save, Tags } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";

import {
  getUCTagConfigKey,
  useGetUCTagConfig,
  useUpdateUCTagConfig,
  type UCTagConfig,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

/**
 * Admin editor for the UC tag mapping that opts a Unity Catalog function or
 * connection into the agent catalog (Phase 1 of the master roadmap). Admins
 * tag UC objects in Databricks with ``ALTER FUNCTION ... SET TAGS`` and this
 * card decides which tag key/value we look for.
 */
export function TagConfigCard() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError } = useGetUCTagConfig();
  const updateConfig = useUpdateUCTagConfig({
    mutation: {
      onSuccess: () => {
        toast.success("Tag configuration saved");
        queryClient.invalidateQueries({ queryKey: getUCTagConfigKey() });
      },
      onError: (err) => {
        toast.error("Save failed", { description: err.message });
      },
    },
  });

  const [form, setForm] = useState<UCTagConfig>({
    agent_tag_key: "",
    agent_tag_value: "",
    agent_kind_tag_key: "",
  });

  useEffect(() => {
    if (data?.data) {
      setForm(data.data);
    }
  }, [data?.data]);

  const isDirty =
    !!data?.data &&
    (form.agent_tag_key !== data.data.agent_tag_key ||
      form.agent_tag_value !== data.data.agent_tag_value ||
      form.agent_kind_tag_key !== data.data.agent_kind_tag_key);

  const handleSave = () => {
    updateConfig.mutate({
      agent_tag_key: form.agent_tag_key.trim(),
      agent_tag_value: form.agent_tag_value.trim(),
      agent_kind_tag_key: form.agent_kind_tag_key.trim(),
    });
  };

  return (
    <Card>
      <CardContent className="space-y-4 p-5">
        <div className="flex items-start gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary/10 text-primary">
            <Tags className="h-[18px] w-[18px]" />
          </div>
          <div className="flex-1">
            <h2
              className={[
                "text-[1.0625rem] font-semibold tracking-[-0.01em]",
                "font-[family-name:var(--font-display)] text-text-primary",
              ].join(" ")}
            >
              UC tag discovery
            </h2>
            <p className="mt-0.5 text-[0.8125rem] leading-[1.5] text-text-muted">
              Tag a Unity Catalog function or connection with this key/value
              pair (
              <code className="font-[family-name:var(--font-mono)] text-[0.75rem]">
                ALTER FUNCTION ... SET TAGS
              </code>
              ) to opt it into the catalog as an HTTP or MCP agent. A second
              tag key selects the invocation path (
              <code className="font-[family-name:var(--font-mono)] text-[0.75rem]">
                http
              </code>{" "}
              or{" "}
              <code className="font-[family-name:var(--font-mono)] text-[0.75rem]">
                mcp
              </code>
              ).
            </p>
          </div>
        </div>

        {isLoading ? (
          <div className="flex h-20 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-text-muted" />
          </div>
        ) : isError ? (
          <div className="rounded-[var(--radius-sm)] border border-error/30 bg-error/5 p-3 text-xs text-error">
            Failed to load tag configuration.
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <FormField
              label="Agent tag key"
              description="Matches every tag with this key."
              value={form.agent_tag_key}
              onChange={(v) => setForm({ ...form, agent_tag_key: v })}
              placeholder="agent_hub_role"
            />
            <FormField
              label="Agent tag value"
              description="Expected value (case-insensitive)."
              value={form.agent_tag_value}
              onChange={(v) => setForm({ ...form, agent_tag_value: v })}
              placeholder="agent"
            />
            <FormField
              label="Kind tag key"
              description="Value http or mcp; unknown → http."
              value={form.agent_kind_tag_key}
              onChange={(v) => setForm({ ...form, agent_kind_tag_key: v })}
              placeholder="agent_hub_kind"
            />
          </div>
        )}

        <div className="flex justify-end">
          <Button
            size="sm"
            onClick={handleSave}
            disabled={!isDirty || updateConfig.isPending || isLoading}
          >
            {updateConfig.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            Save configuration
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function FormField({
  label,
  description,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  description: string;
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  return (
    <label className="block">
      <div className="mb-1 text-[0.75rem] font-medium text-text-primary">
        {label}
      </div>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={[
          "block h-9 w-full rounded-[var(--radius-md)]",
          "border border-border bg-surface-elevated",
          "px-3 font-[family-name:var(--font-mono)] text-[0.75rem]",
          "text-text-primary placeholder:text-text-muted",
          "transition-[border-color,box-shadow] duration-150",
          "focus:border-info focus:outline-none focus:ring-2 focus:ring-info/30",
        ].join(" ")}
      />
      <div className="mt-1 text-[0.6875rem] text-text-muted">{description}</div>
    </label>
  );
}
