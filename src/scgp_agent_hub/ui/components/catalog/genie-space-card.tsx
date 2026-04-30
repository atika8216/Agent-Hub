import { Link } from "@tanstack/react-router";
import { ChevronRight, ExternalLink } from "lucide-react";

import type { GenieSpace } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { agentTypeLabel, agentTypeVariant } from "@/lib/agent-type";
import { AccessBadge } from "./access-badge";

interface GenieSpaceCardProps {
  space: GenieSpace;
  /**
   * Databricks workspace host used to build the optional "open in Databricks"
   * deep link. When omitted, the card just routes to the in-app detail page.
   */
  workspaceHost?: string;
}

// Synthetic endpoint identifier used by the catalog/chat backend to disambiguate
// Genie spaces from real serving endpoints. Keep in sync with
// catalog_service._GENIE_ENDPOINT_PREFIX on the backend.
const GENIE_PREFIX = "genie:";

export function GenieSpaceCard({ space, workspaceHost }: GenieSpaceCardProps) {
  const externalHref = workspaceHost
    ? `${workspaceHost.replace(/\/+$/, "")}/genie/rooms/${space.space_id}`
    : undefined;

  const agentId = `${GENIE_PREFIX}${space.space_id}`;

  return (
    <Link
      to="/catalog/$agentId"
      params={{ agentId }}
      className={[
        "group block outline-none rounded-[var(--radius-lg)]",
        "focus-visible:ring-2 focus-visible:ring-info focus-visible:ring-offset-2",
        "focus-visible:ring-offset-background",
      ].join(" ")}
    >
      <Card
        className={[
          "h-full",
          "transition-[transform,border-color,box-shadow] duration-200",
          "group-hover:-translate-y-[1px]",
          "group-hover:border-border-strong",
          "group-hover:shadow-[0_4px_16px_0_oklch(0_0_0/0.06)]",
          "dark:group-hover:shadow-[inset_0_1px_0_oklch(1_0_0/0.06)]",
        ].join(" ")}
      >
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <CardTitle className="line-clamp-1">{space.title}</CardTitle>
            <div className="flex shrink-0 items-center gap-1.5">
              <AccessBadge hasAccess={space.has_access ?? true} />
              <ChevronRight
                className="h-4 w-4 text-text-muted transition-transform duration-200 group-hover:translate-x-[2px]"
                aria-hidden="true"
              />
            </div>
          </div>
          <p className="line-clamp-2 min-h-[2.5rem] text-[0.875rem] leading-[1.45] text-text-secondary">
            {space.description || "Genie Space for natural-language analytics"}
          </p>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Badge variant={agentTypeVariant("GENIE_SPACE")} shape="pill">
              {agentTypeLabel("GENIE_SPACE")}
            </Badge>
            {externalHref && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  e.preventDefault();
                  window.open(externalHref, "_blank", "noopener,noreferrer");
                }}
                className={[
                  "inline-flex h-6 w-6 items-center justify-center",
                  "rounded-full bg-surface-overlay text-text-muted",
                  "transition-colors hover:text-text-primary",
                ].join(" ")}
                aria-label="Open in Databricks"
                title="Open in Databricks"
              >
                <ExternalLink className="h-3 w-3" />
              </button>
            )}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
