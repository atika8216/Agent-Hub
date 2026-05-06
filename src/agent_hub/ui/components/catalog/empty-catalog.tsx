interface EmptyCatalogProps {
  hasSearch?: boolean;
}

export function EmptyCatalog({ hasSearch = false }: EmptyCatalogProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="mb-4 rounded-full bg-surface-overlay p-4">
        <svg
          className="h-8 w-8 text-text-muted"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <rect x="3" y="3" width="7" height="7" rx="1" />
          <rect x="14" y="3" width="7" height="7" rx="1" />
          <rect x="3" y="14" width="7" height="7" rx="1" />
          <rect x="14" y="14" width="7" height="7" rx="1" />
        </svg>
      </div>
      <h3
        className={[
          "mb-1 text-[1.0625rem] font-semibold tracking-[-0.01em]",
          "font-[family-name:var(--font-display)] text-text-primary",
        ].join(" ")}
      >
        {hasSearch ? "No agents match your search" : "No agents discovered yet"}
      </h3>
      <p className="max-w-sm text-[0.9375rem] leading-[1.5] text-text-secondary">
        {hasSearch
          ? "Try adjusting your search or filter criteria."
          : "Ask an admin to run agent discovery from the Admin panel to populate the catalog."}
      </p>
    </div>
  );
}
