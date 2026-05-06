import { createFileRoute, Link } from "@tanstack/react-router";
import { ChevronRight, MessageSquare } from "lucide-react";
import { motion } from "motion/react";

import { useAgent, useAgentAccess } from "@/hooks/use-agents";
import type { AgentDetail } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AccessBadge } from "@/components/catalog/access-badge";
import { SubAgentRow } from "@/components/catalog/sub-agent-row";
import { AgentDetailSkeleton } from "@/components/catalog/agent-skeleton";
import { agentTypeLabel, agentTypeVariant } from "@/lib/agent-type";
import { agentGlyph } from "@/lib/agent-glyph";

export const Route = createFileRoute("/_sidebar/catalog/$agentId")({
  component: AgentDetailPage,
});

function AgentDetailPage() {
  const { agentId } = Route.useParams();
  const { data: agent, isLoading, isError } = useAgent(agentId);
  const { data: access } = useAgentAccess(agentId);

  if (isLoading) {
    return (
      <div className="mx-auto w-full max-w-3xl p-6 md:p-8">
        <AgentDetailSkeleton />
      </div>
    );
  }

  if (isError || !agent) {
    return (
      <div className="mx-auto w-full max-w-3xl p-6 md:p-8">
        <Breadcrumb />
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <h2 className="mb-2 text-[1.0625rem] font-semibold text-text-primary">
            Agent not found
          </h2>
          <p className="mb-4 text-[0.9375rem] text-text-secondary">
            The agent &ldquo;{agentId}&rdquo; could not be found in the catalog.
          </p>
          <Button variant="secondary" asChild>
            <Link to="/catalog">Back to Catalog</Link>
          </Button>
        </div>
      </div>
    );
  }

  const hasAccess: boolean = access?.has_access ?? agent.has_access ?? false;

  return (
    <div className="mx-auto w-full max-w-3xl space-y-8 p-6 md:p-8">
      <Breadcrumb agentName={agent.display_name} />

      <Hero agent={agent} hasAccess={hasAccess} />

      <ComponentsSection
        agentType={agent.agent_type}
        subAgents={agent.sub_agents}
        accessMap={access?.sub_agent_access}
        ownerEmail={agent.owner_email}
      />

      <TechnicalDetails agent={agent} permission={access?.permission_level} />
    </div>
  );
}

/*
 * Hero treatment: the page opens with an agent avatar, large display name,
 * single subtitle row (owner + type chip), long-form description, and a
 * prominent primary CTA. The duplicate "Type" row that used to live in the
 * metadata list has been removed -- the hero chip already states it, and the
 * endpoint has been folded into the "Show technical details" disclosure below
 * the components section so the page reads top-down like an iOS detail
 * surface, not a key/value form.
 */
function Hero({
  agent,
  hasAccess,
}: {
  agent: AgentDetail;
  hasAccess: boolean;
}) {
  const glyph = agentGlyph(agent.agent_type);
  const GlyphIcon = glyph.icon;
  const ownerHandle = agent.owner_email?.split("@")[0];

  return (
    /*
     * Shared ``layoutId`` with the matching ``AgentCard`` in the
     * catalog grid. Motion interpolates the bounding box across the
     * route swap, so clicking the tile makes the card physically grow
     * into this hero block instead of cutting between routes.
     */
    <motion.section
      layoutId={`agent-card-${agent.endpoint_name}`}
      className={[
        "rounded-[var(--radius-lg)] border border-border",
        "bg-surface-elevated p-5 md:p-6",
        "shadow-[0_1px_2px_0_oklch(0_0_0/0.04)]",
        "dark:shadow-[inset_0_1px_0_oklch(1_0_0/0.04)]",
        "stagger-child",
      ].join(" ")}
      style={{ ["--i" as string]: 0 }}
    >
      <div className="flex items-start gap-4">
        <div
          aria-hidden="true"
          className={[
            "flex h-14 w-14 shrink-0 items-center justify-center",
            "rounded-[var(--radius-lg)]",
          ].join(" ")}
          style={{ background: glyph.tint, color: glyph.fg }}
        >
          <GlyphIcon className="h-7 w-7" />
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <h1
            className={[
              "text-[1.875rem] md:text-[2rem] font-bold leading-[1.1] tracking-[-0.025em]",
              "font-[family-name:var(--font-display)] text-text-primary",
            ].join(" ")}
          >
            {agent.display_name}
          </h1>
          <div className="flex flex-wrap items-center gap-2 text-[0.8125rem] text-text-secondary">
            <Badge variant={agentTypeVariant(agent.agent_type)} shape="pill">
              {agentTypeLabel(agent.agent_type)}
            </Badge>
            {ownerHandle && (
              <>
                <span aria-hidden="true" className="text-text-muted">
                  ·
                </span>
                <span className="truncate">Owner: {ownerHandle}</span>
              </>
            )}
            <span aria-hidden="true" className="text-text-muted">
              ·
            </span>
            <AccessBadge hasAccess={hasAccess} showLabel />
          </div>
        </div>
      </div>

      <p className="mt-4 text-[0.9375rem] leading-[1.55] text-text-secondary">
        {agent.description || "No description available"}
      </p>

      <div className="mt-5 flex flex-col gap-2 sm:flex-row sm:items-center">
        <Button
          size="lg"
          disabled={!hasAccess}
          asChild={hasAccess}
          className="w-full sm:w-auto"
          title={
            hasAccess
              ? "Start a new chat with this agent"
              : "You need access to chat with this agent"
          }
        >
          {hasAccess ? (
            <Link
              to="/chat/new"
              search={{ agent: agent.endpoint_name }}
            >
              <MessageSquare className="h-[18px] w-[18px]" aria-hidden="true" />
              Start Chat
            </Link>
          ) : (
            <span>
              <MessageSquare className="h-[18px] w-[18px]" aria-hidden="true" />
              Start Chat
            </span>
          )}
        </Button>
        {!hasAccess && agent.owner_email && (
          <Button variant="secondary" size="lg" asChild className="w-full sm:w-auto">
            <a
              href={`mailto:${agent.owner_email}?subject=Access request: ${agent.display_name}`}
            >
              Request Access
            </a>
          </Button>
        )}
      </div>
    </motion.section>
  );
}

function ComponentsSection({
  agentType,
  subAgents,
  accessMap,
  ownerEmail,
}: {
  agentType?: string;
  subAgents?: AgentDetail["sub_agents"];
  accessMap?: Record<string, boolean>;
  ownerEmail?: string;
}) {
  const normalized = (agentType ?? "").toString().toUpperCase();
  const hideForPlainModel = normalized === "MODEL" || normalized === "EXTERNAL";
  const components = subAgents ?? [];

  if (hideForPlainModel && components.length === 0) {
    return null;
  }

  return (
    <section
      className="space-y-2 stagger-child"
      style={{ ["--i" as string]: 1 }}
    >
      <div className="flex items-baseline justify-between px-1">
        <h2 className="text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-text-muted">
          Components
        </h2>
        <span className="text-[0.75rem] text-text-muted">
          {components.length}
        </span>
      </div>

      {components.length === 0 ? (
        <div
          className={[
            "rounded-[var(--radius-lg)] border border-dashed border-border",
            "bg-surface-elevated/50 px-4 py-6",
            "text-center text-[0.875rem] text-text-muted",
          ].join(" ")}
        >
          This agent has no declared sub-components. Tools, Genie spaces, and
          knowledge assistants will appear here once the model registers them.
        </div>
      ) : (
        <div
          className={[
            "overflow-hidden rounded-[var(--radius-lg)]",
            "border border-border bg-surface-elevated",
            "shadow-[0_1px_2px_0_oklch(0_0_0/0.04)]",
            "dark:shadow-[inset_0_1px_0_oklch(1_0_0/0.04)]",
          ].join(" ")}
        >
          {components.map((sa, idx) => (
            <SubAgentRow
              key={`${sa.type}:${sa.name}`}
              subAgent={{ ...sa, owner_email: sa.owner_email || ownerEmail }}
              accessMap={accessMap}
              index={idx}
            />
          ))}
        </div>
      )}
    </section>
  );
}

/*
 * Low-priority metadata lives behind a ``<details>`` disclosure so the hero
 * stays the hero. Expanding it reveals the endpoint name and owner email --
 * the pieces platform engineers need when debugging, but that a business user
 * rarely cares about.
 */
function TechnicalDetails({
  agent,
  permission,
}: {
  agent: AgentDetail;
  permission?: string;
}) {
  return (
    <details
      className={[
        "group rounded-[var(--radius-lg)] border border-border",
        "bg-surface-elevated stagger-child",
        "shadow-[0_1px_2px_0_oklch(0_0_0/0.04)]",
        "dark:shadow-[inset_0_1px_0_oklch(1_0_0/0.04)]",
      ].join(" ")}
      style={{ ["--i" as string]: 2 }}
    >
      <summary
        className={[
          "flex cursor-pointer select-none items-center justify-between",
          "px-4 py-3 text-[0.875rem] text-text-secondary",
          "hover:text-text-primary",
          "focus-visible:outline-none focus-visible:ring-2",
          "focus-visible:ring-info focus-visible:ring-offset-2",
          "focus-visible:ring-offset-background",
          "list-none [&::-webkit-details-marker]:hidden",
        ].join(" ")}
      >
        <span className="font-medium">Technical details</span>
        <ChevronRight
          className={[
            "h-4 w-4 text-text-muted transition-transform duration-200",
            "group-open:rotate-90",
          ].join(" ")}
          aria-hidden="true"
        />
      </summary>
      <div className="border-t border-border">
        <DetailRow label="Endpoint">
          <code className="truncate font-[family-name:var(--font-mono)] text-[0.8125rem] text-text-primary">
            {agent.endpoint_name}
          </code>
        </DetailRow>
        {agent.owner_email && (
          <DetailRow label="Owner">
            <span className="truncate text-[0.875rem] text-text-primary">
              {agent.owner_email}
            </span>
          </DetailRow>
        )}
        {permission && (
          <DetailRow label="Permission">
            <span className="text-[0.8125rem] text-text-primary">
              {permission}
            </span>
          </DetailRow>
        )}
      </div>
    </details>
  );
}

function DetailRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={[
        "flex items-center justify-between gap-4 px-4 py-3",
        "border-b border-border last:border-b-0",
      ].join(" ")}
    >
      <span className="shrink-0 text-[0.875rem] text-text-secondary">
        {label}
      </span>
      <div className="flex min-w-0 items-center gap-2 text-right">
        {children}
      </div>
    </div>
  );
}

function Breadcrumb({ agentName }: { agentName?: string }) {
  return (
    <nav
      aria-label="Breadcrumb"
      className="flex items-center gap-1 text-[0.875rem]"
    >
      <Link
        to="/catalog"
        className="text-text-secondary transition-colors hover:text-text-primary"
      >
        Catalog
      </Link>
      {agentName && (
        <>
          <ChevronRight className="h-3.5 w-3.5 text-text-muted" />
          <span className="max-w-[320px] truncate font-medium text-text-primary">
            {agentName}
          </span>
        </>
      )}
    </nav>
  );
}
