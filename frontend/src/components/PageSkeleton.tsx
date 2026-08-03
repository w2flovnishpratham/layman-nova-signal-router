import { Skeleton } from '@/components/ui/skeleton'

type SkeletonVariant = 'cards' | 'dashboard' | 'form' | 'table' | 'terminal'

export function PageSkeleton({
  label = 'Loading page',
  variant = 'cards',
  compact = false,
}: {
  label?: string
  variant?: SkeletonVariant
  compact?: boolean
}) {
  return (
    <div className={`nova-page-skeleton is-${variant}${compact ? ' is-compact' : ''}`} role="status" aria-label={label}>
      <span className="sr-only">{label}</span>
      <div className="nova-skeleton-summary" aria-hidden="true">
        {Array.from({ length: variant === 'dashboard' ? 4 : 3 }, (_, index) => (
          <Skeleton key={index} className="nova-skeleton-summary-card" />
        ))}
      </div>
      <div className="nova-skeleton-body" aria-hidden="true">
        <Skeleton className="nova-skeleton-primary" />
        <div className="nova-skeleton-side">
          <Skeleton />
          <Skeleton />
        </div>
      </div>
      {variant === 'table' || variant === 'terminal' ? (
        <div className="nova-skeleton-rows" aria-hidden="true">
          {Array.from({ length: compact ? 3 : 6 }, (_, index) => <Skeleton key={index} />)}
        </div>
      ) : null}
    </div>
  )
}
