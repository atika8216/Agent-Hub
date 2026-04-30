import { Card, CardContent, CardHeader } from "@/components/ui/card";

function Shimmer({ className }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-[var(--radius-sm)] bg-surface-overlay ${className ?? ""}`}
    />
  );
}

export function AgentCardSkeleton() {
  return (
    <Card className="h-full">
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <Shimmer className="h-5 w-3/4" />
          <Shimmer className="h-2 w-2 rounded-full" />
        </div>
        <Shimmer className="h-4 w-full mt-2" />
        <Shimmer className="h-4 w-2/3" />
      </CardHeader>
      <CardContent>
        <div className="flex gap-2">
          <Shimmer className="h-5 w-12" />
          <Shimmer className="h-5 w-20" />
        </div>
      </CardContent>
    </Card>
  );
}

export function AgentCardSkeletonGrid({ count = 6 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      {Array.from({ length: count }, (_, i) => (
        <AgentCardSkeleton key={i} />
      ))}
    </div>
  );
}

export function AgentDetailSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Shimmer className="h-4 w-32" />
        <Shimmer className="h-8 w-64" />
        <Shimmer className="h-4 w-96" />
      </div>
      <div className="flex gap-3">
        <Shimmer className="h-9 w-32" />
        <Shimmer className="h-9 w-24" />
      </div>
      <div className="space-y-0 border border-border rounded-[var(--radius-md)]">
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="flex items-center gap-3 p-4 border-b border-border last:border-b-0">
            <Shimmer className="h-5 w-16" />
            <Shimmer className="h-4 w-48" />
            <div className="ml-auto">
              <Shimmer className="h-4 w-20" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
