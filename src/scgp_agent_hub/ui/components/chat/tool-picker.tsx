import { memo, useState } from "react";
import { Wrench } from "lucide-react";

import type { McpToolDescriptor } from "@/lib/types";

interface ToolPickerProps {
  tools: McpToolDescriptor[];
  onChoose: (toolName: string) => void;
  onCancel: () => void;
}

export const ToolPicker = memo(function ToolPicker({
  tools,
  onChoose,
  onCancel,
}: ToolPickerProps) {
  const [selected, setSelected] = useState<string | null>(
    tools.length > 0 ? tools[0].name : null,
  );

  return (
    <div className="rounded-2xl border border-border bg-surface-elevated p-4">
      <div className="mb-3 flex items-center gap-2 text-sm text-text-primary">
        <Wrench className="h-4 w-4 text-info" />
        <span className="font-medium">Choose a tool</span>
      </div>
      <p className="mb-3 text-xs text-text-muted">
        This MCP server exposes multiple tools. Select one to continue the conversation.
      </p>
      <div className="mb-4 max-h-64 space-y-1 overflow-auto">
        {tools.map((tool) => (
          <label
            key={tool.name}
            className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
              selected === tool.name
                ? "border-info bg-info/5"
                : "border-border bg-surface hover:border-border-strong"
            }`}
          >
            <input
              type="radio"
              name="tool"
              value={tool.name}
              checked={selected === tool.name}
              onChange={() => setSelected(tool.name)}
              className="mt-1 h-3.5 w-3.5 accent-info"
            />
            <div className="min-w-0 flex-1">
              <div className="font-mono text-xs text-text-primary">{tool.name}</div>
              {tool.description && (
                <div className="mt-0.5 line-clamp-2 text-xs text-text-muted">
                  {tool.description}
                </div>
              )}
            </div>
          </label>
        ))}
      </div>
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg border border-border px-3 py-1.5 text-xs text-text-primary hover:bg-surface"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={!selected}
          onClick={() => selected && onChoose(selected)}
          className="rounded-lg bg-primary px-3 py-1.5 text-xs text-primary-foreground disabled:opacity-40"
        >
          Use tool
        </button>
      </div>
    </div>
  );
});
