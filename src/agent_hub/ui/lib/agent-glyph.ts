import {
  BookOpen,
  Bot,
  Code2,
  Database,
  Layers,
  type LucideIcon,
  Plug,
  Sparkles,
  Workflow,
} from "lucide-react";

import type { AgentType, SubComponentType } from "./types";

/**
 * Icon + surface tint for an agent tile. Used in the detail-page hero
 * and anywhere we show an "avatar" for an agent row without leaning on
 * an actual user-uploaded image (Agent Bricks doesn't expose one).
 *
 * The tint references CSS variables so it follows light/dark theme
 * switches without a second code path.
 */
export function agentGlyph(type?: AgentType | string): {
  icon: LucideIcon;
  tint: string;
  fg: string;
} {
  const normalized = (type ?? "").toString().toUpperCase();
  switch (normalized) {
    case "MAS":
      return {
        icon: Workflow,
        tint: "color-mix(in oklab, var(--color-badge-mas) 18%, transparent)",
        fg: "var(--color-badge-mas)",
      };
    case "KA":
      return {
        icon: BookOpen,
        tint: "color-mix(in oklab, var(--color-badge-ka) 18%, transparent)",
        fg: "var(--color-badge-ka)",
      };
    case "GENIE_SPACE":
      return {
        icon: Sparkles,
        tint: "color-mix(in oklab, var(--color-badge-genie) 18%, transparent)",
        fg: "var(--color-badge-genie)",
      };
    case "HTTP_CONNECTION":
      return {
        icon: Code2,
        tint: "color-mix(in oklab, var(--color-badge-uc) 18%, transparent)",
        fg: "var(--color-badge-uc)",
      };
    case "MCP_ENDPOINT":
      return {
        icon: Plug,
        tint: "color-mix(in oklab, var(--color-badge-mcp) 18%, transparent)",
        fg: "var(--color-badge-mcp)",
      };
    case "EXTERNAL":
      return {
        icon: Database,
        tint: "color-mix(in oklab, var(--color-badge-external) 18%, transparent)",
        fg: "var(--color-badge-external)",
      };
    case "AGENT":
      return {
        icon: Bot,
        tint: "color-mix(in oklab, var(--color-badge-agent) 18%, transparent)",
        fg: "var(--color-badge-agent)",
      };
    default:
      return {
        icon: Layers,
        tint: "color-mix(in oklab, var(--color-badge-model) 20%, transparent)",
        fg: "var(--color-badge-model)",
      };
  }
}

/**
 * Icon + tint for a sub-component row. Shares the agent-color palette so
 * a KA child on a MAS detail page matches the KA card on /catalog.
 */
export function subComponentGlyph(type: SubComponentType): {
  icon: LucideIcon;
  tint: string;
  fg: string;
} {
  switch (type) {
    case "genie_space":
    case "genie":
      return {
        icon: Sparkles,
        tint: "color-mix(in oklab, var(--color-badge-genie) 18%, transparent)",
        fg: "var(--color-badge-genie)",
      };
    case "uc_function":
      return {
        icon: Code2,
        tint: "color-mix(in oklab, var(--color-badge-uc) 18%, transparent)",
        fg: "var(--color-badge-uc)",
      };
    case "knowledge_assistant":
      return {
        icon: BookOpen,
        tint: "color-mix(in oklab, var(--color-badge-ka) 18%, transparent)",
        fg: "var(--color-badge-ka)",
      };
    case "external_mcp":
      return {
        icon: Plug,
        tint: "color-mix(in oklab, var(--color-badge-mcp) 18%, transparent)",
        fg: "var(--color-badge-mcp)",
      };
    case "vector_search":
      return {
        icon: Layers,
        tint: "color-mix(in oklab, var(--color-badge-vector) 18%, transparent)",
        fg: "var(--color-badge-vector)",
      };
    case "served_model":
    default:
      return {
        icon: Database,
        tint: "color-mix(in oklab, var(--color-badge-model) 20%, transparent)",
        fg: "var(--color-badge-model)",
      };
  }
}
