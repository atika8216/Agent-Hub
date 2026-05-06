import type { AgentType, SubComponentType } from "./types";

type BadgeVariant =
  | "default"
  | "mas"
  | "agent"
  | "ka"
  | "model"
  | "external"
  | "genie"
  | "uc"
  | "mcp"
  | "vector";

export function agentTypeVariant(type?: AgentType | string): BadgeVariant {
  const normalized = (type ?? "").toString().toUpperCase();
  switch (normalized) {
    case "MAS":
      return "mas";
    case "AGENT":
      return "agent";
    case "KA":
      return "ka";
    case "EXTERNAL":
      return "external";
    case "MODEL":
      return "model";
    case "GENIE_SPACE":
      return "genie";
    case "HTTP_CONNECTION":
      return "uc";
    case "MCP_ENDPOINT":
      return "mcp";
    default:
      return "default";
  }
}

/**
 * Human label for an agent type. Uses Databricks UI wording so the catalog
 * matches what users see in the Databricks workspace (Agent Bricks, Serving,
 * Genie).
 */
export function agentTypeLabel(type?: AgentType | string): string {
  const normalized = (type ?? "").toString().toUpperCase();
  switch (normalized) {
    case "MAS":
      return "Supervisor Agent";
    case "AGENT":
      return "Custom Agent Endpoint";
    case "KA":
      return "Knowledge Assistant";
    case "EXTERNAL":
      return "External Model";
    case "MODEL":
      return "Model";
    case "GENIE_SPACE":
      return "Genie Space";
    case "HTTP_CONNECTION":
      return "HTTP Connection";
    case "MCP_ENDPOINT":
      return "MCP Endpoint";
    default:
      return type ?? "Unknown";
  }
}

/**
 * Derive an agent type from an endpoint-name prefix when the backend hasn't
 * populated ``agent_type`` yet (transient discovery-not-run state). This is
 * the defense that keeps the chat header from rendering "Supervisor Agent"
 * on a Genie / UC / MCP chat.
 */
export function agentTypeFromEndpointName(
  endpointName?: string,
): AgentType | undefined {
  if (!endpointName) return undefined;
  if (endpointName.startsWith("genie:")) return "GENIE_SPACE";
  if (endpointName.startsWith("mcp:")) return "MCP_ENDPOINT";
  if (endpointName.startsWith("uc:")) return "HTTP_CONNECTION";
  return undefined;
}

export function subComponentVariant(type: SubComponentType): BadgeVariant {
  switch (type) {
    case "genie_space":
    case "genie":
      return "genie";
    case "uc_function":
      return "uc";
    case "knowledge_assistant":
      return "ka";
    case "external_mcp":
      return "mcp";
    case "served_model":
      return "model";
    case "vector_search":
      return "vector";
    default:
      return "default";
  }
}

export function subComponentLabel(type: SubComponentType): string {
  switch (type) {
    case "genie_space":
    case "genie":
      return "Genie Space";
    case "uc_function":
      return "UC Function";
    case "knowledge_assistant":
      return "Knowledge Assistant";
    case "external_mcp":
      return "External MCP";
    case "served_model":
      return "Served Model";
    case "vector_search":
      return "Vector Search";
    default:
      return type;
  }
}

/**
 * Whether the catalog should surface a sub-component count pill for this agent
 * type. Only MAS (Supervisor Agent) reliably orchestrates typed sub-agents we
 * can introspect; Custom Agent Endpoints and Genie Spaces stand alone.
 */
export function showSubComponentCount(type?: AgentType | string): boolean {
  const normalized = (type ?? "").toString().toUpperCase();
  return normalized === "MAS";
}

/**
 * Short, agent-type-specific empty-state hint. Used on /chat/new so the
 * first screen reflects the nature of the agent (SQL Genie prompt vs. an
 * MCP tool server vs. a UC HTTP connection) instead of a single generic
 * "Send a message" line.
 */
export function emptyStateHint(type?: AgentType | string): string {
  const normalized = (type ?? "").toString().toUpperCase();
  switch (normalized) {
    case "GENIE_SPACE":
      return "Ask a business question in plain English. I'll query the underlying tables and return a chart or table.";
    case "HTTP_CONNECTION":
      return "This agent runs a Unity Catalog HTTP connection. Send a prompt and I'll invoke the function on your behalf.";
    case "MCP_ENDPOINT":
      return "This MCP server exposes one or more tools. I'll pick the best tool for your message, or ask if there's more than one option.";
    case "KA":
      return "Ask a question grounded in the curated knowledge base.";
    case "MAS":
      return "Ask anything. I'll route the question across the right sub-agents.";
    default:
      return "Send a message to start the conversation";
  }
}

/**
 * Three starter prompts per agent type. Used on the empty conversation
 * state when the user has no pinned questions for the agent yet -- gives
 * them a single tap to get a useful first reply instead of staring at a
 * blank input. Copy stays neutral / example-focused so it reads as a
 * hint rather than a commitment.
 */
export function emptyStateStarterPrompts(
  type?: AgentType | string,
): string[] {
  const normalized = (type ?? "").toString().toUpperCase();
  switch (normalized) {
    case "GENIE_SPACE":
      return [
        "What are the top 10 customers by revenue this quarter?",
        "Show me daily order volume over the last 90 days.",
        "Which product categories grew the fastest year over year?",
      ];
    case "HTTP_CONNECTION":
      return [
        "What does this endpoint do?",
        "Run with the default parameters.",
        "List the inputs this function accepts.",
      ];
    case "MCP_ENDPOINT":
      return [
        "What tools are available on this server?",
        "Summarize what you can help me with.",
        "Pick the best tool for my next question.",
      ];
    case "KA":
      return [
        "Summarize the most recent document in this knowledge base.",
        "What are the top three FAQs covered here?",
        "Give me a one-paragraph overview of this assistant's domain.",
      ];
    case "MAS":
      return [
        "What can you help me with?",
        "Summarize your sub-agents and what each one is good at.",
        "Walk me through a typical use case for this agent.",
      ];
    default:
      return [
        "What can you do?",
        "Give me an example question to ask.",
        "Summarize your capabilities.",
      ];
  }
}
