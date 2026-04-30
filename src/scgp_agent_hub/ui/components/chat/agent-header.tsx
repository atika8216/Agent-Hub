import { memo } from "react";
import { Bot, Brain, BrainCircuit, Layers, ShieldCheck, ZapOff } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  agentTypeFromEndpointName,
  agentTypeLabel,
  agentTypeVariant,
} from "@/lib/agent-type";

export type MemoryMode = "off" | "short_term" | "long_term" | "both";

interface AgentHeaderProps {
  displayName: string;
  endpointName: string;
  agentType?: string;
  hasAccess?: boolean;
  memoryMode?: MemoryMode;
}

const MEMORY_META: Record<
  MemoryMode,
  {
    label: string;
    Icon: typeof Brain;
    variant: "default" | "info" | "success" | "warning";
    tooltip: string;
  }
> = {
  off: {
    label: "Memory off",
    Icon: ZapOff,
    variant: "default",
    tooltip:
      "The agent has no memory of prior turns. Each request is sent in isolation.",
  },
  short_term: {
    label: "Short-term memory",
    Icon: Brain,
    variant: "info",
    tooltip:
      "The agent sees the recent messages from this conversation when generating its response.",
  },
  long_term: {
    label: "Long-term memory",
    Icon: BrainCircuit,
    variant: "success",
    tooltip:
      "Insights from past conversations with you are passed to the agent. New insights are extracted after each turn.",
  },
  both: {
    label: "Full memory",
    Icon: Layers,
    variant: "success",
    tooltip:
      "Recent conversation history plus durable insights from past sessions are sent to the agent.",
  },
};

export const AgentHeader = memo(function AgentHeader({
  displayName,
  endpointName,
  agentType,
  hasAccess = true,
  memoryMode,
}: AgentHeaderProps) {
  const meta = memoryMode ? MEMORY_META[memoryMode] : null;
  const MemoryIcon = meta?.Icon;

  // Prefer the explicit agent_type prop; otherwise derive from the endpoint
  // prefix so Genie / UC HTTP / MCP chats never render with a stale "MAS"
  // badge while catalog metadata is in flight. This fixes the prior
  // "Supervisor Agent" glitch on Genie chat headers.
  const resolvedType =
    agentType ?? agentTypeFromEndpointName(endpointName) ?? "MAS";

  return (
    <div
      className={[
        "flex items-center gap-3 px-5 py-3",
        "border-b border-border",
        // Translucent surface gives the iOS "large title" feel without
        // needing a full sticky large-title treatment.
        "bg-surface/80 backdrop-blur",
      ].join(" ")}
    >
      <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Bot className="h-[18px] w-[18px]" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h2
            className={[
              "truncate text-[1.0625rem] font-semibold tracking-[-0.01em]",
              "font-[family-name:var(--font-display)] text-text-primary",
            ].join(" ")}
          >
            {displayName}
          </h2>
          <Badge
            variant={agentTypeVariant(resolvedType)}
            shape="pill"
            className="shrink-0"
          >
            {agentTypeLabel(resolvedType)}
          </Badge>
          {hasAccess && (
            <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-success" />
          )}
          {meta && MemoryIcon && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge
                  variant={meta.variant}
                  shape="pill"
                  className="shrink-0 cursor-help gap-1"
                >
                  <MemoryIcon className="h-3 w-3" />
                  {meta.label}
                </Badge>
              </TooltipTrigger>
              <TooltipContent>{meta.tooltip}</TooltipContent>
            </Tooltip>
          )}
        </div>
        <p className="truncate text-[0.75rem] text-text-muted">{endpointName}</p>
      </div>
    </div>
  );
});
