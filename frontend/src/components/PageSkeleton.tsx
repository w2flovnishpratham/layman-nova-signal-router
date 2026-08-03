import { Skeleton } from '@/components/ui/skeleton'

type SkeletonVariant = 'cards' | 'dashboard' | 'form' | 'table' | 'terminal' | 'calendar' | 'split-form' | 'two-column' | 'list' | 'list-detail'

export function PageSkeleton({
  label = 'Loading page',
  variant = 'cards',
  compact = false,
}: {
  label?: string
  variant?: SkeletonVariant
  compact?: boolean
}) {
  if (variant === 'calendar') {
    // Matches ReportsPage: filter row -> 5 stat cards -> table panel + calendar aside.
    return (
      <div className={`nova-page-skeleton is-calendar${compact ? ' is-compact' : ''}`} role="status" aria-label={label}>
        <span className="sr-only">{label}</span>
        <div className="nova-skeleton-summary" aria-hidden="true">
          {Array.from({ length: 5 }, (_, index) => <Skeleton key={index} className="nova-skeleton-summary-card" />)}
        </div>
        <div className="nova-skeleton-report-layout" aria-hidden="true">
          <div className="nova-skeleton-rows">
            {Array.from({ length: compact ? 3 : 6 }, (_, index) => <Skeleton key={index} />)}
          </div>
          <div className="nova-skeleton-calendar-grid">
            {Array.from({ length: 35 }, (_, index) => <Skeleton key={index} className="nova-skeleton-calendar-cell" />)}
          </div>
        </div>
      </div>
    )
  }

  if (variant === 'split-form') {
    // Matches RiskPage: preset pill row -> two-column editor (field groups) + aside.
    return (
      <div className={`nova-page-skeleton is-split-form${compact ? ' is-compact' : ''}`} role="status" aria-label={label}>
        <span className="sr-only">{label}</span>
        <div className="nova-skeleton-pill-row" aria-hidden="true">
          {Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="nova-skeleton-pill" />)}
        </div>
        <div className="nova-skeleton-body" aria-hidden="true">
          <div className="nova-skeleton-field-groups">
            {Array.from({ length: 2 }, (_, groupIndex) => (
              <div key={groupIndex} className="nova-skeleton-field-group">
                {Array.from({ length: 3 }, (_, index) => <Skeleton key={index} />)}
              </div>
            ))}
          </div>
          <div className="nova-skeleton-side">
            <Skeleton />
            <Skeleton />
            <Skeleton />
          </div>
        </div>
      </div>
    )
  }

  if (variant === 'two-column') {
    // Matches SettingsPage: two symmetric columns, each a stack of card sections.
    return (
      <div className={`nova-page-skeleton is-two-column${compact ? ' is-compact' : ''}`} role="status" aria-label={label}>
        <span className="sr-only">{label}</span>
        <div className="nova-skeleton-two-column" aria-hidden="true">
          <div className="nova-skeleton-card-stack">
            {Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="nova-skeleton-settings-card" />)}
          </div>
          <div className="nova-skeleton-card-stack">
            {Array.from({ length: 3 }, (_, index) => <Skeleton key={index} className="nova-skeleton-settings-card" />)}
          </div>
        </div>
      </div>
    )
  }

  if (variant === 'list') {
    // Matches AutomationsPage: a single card holding a stack of editable rows.
    return (
      <div className={`nova-page-skeleton is-list${compact ? ' is-compact' : ''}`} role="status" aria-label={label}>
        <span className="sr-only">{label}</span>
        <div className="nova-skeleton-list-card" aria-hidden="true">
          {Array.from({ length: compact ? 3 : 6 }, (_, index) => <Skeleton key={index} />)}
        </div>
      </div>
    )
  }

  if (variant === 'list-detail') {
    // Matches PersonalStrategiesPage / ImportedPinePage browse mode: a narrow
    // list of entries beside a wider detail panel.
    return (
      <div className={`nova-page-skeleton is-list-detail${compact ? ' is-compact' : ''}`} role="status" aria-label={label}>
        <span className="sr-only">{label}</span>
        <div className="nova-skeleton-list-detail" aria-hidden="true">
          <div className="nova-skeleton-list-items">
            {Array.from({ length: 5 }, (_, index) => <Skeleton key={index} className="nova-skeleton-list-item" />)}
          </div>
          <div className="nova-skeleton-field-group">
            {Array.from({ length: 4 }, (_, index) => <Skeleton key={index} />)}
          </div>
        </div>
      </div>
    )
  }

  if (compact) {
    // Compact usages (embedded in a tab panel or card) show only the content
    // that panel actually has once loaded — no summary cards, no side rail.
    return (
      <div className={`nova-page-skeleton is-${variant} is-compact`} role="status" aria-label={label}>
        <span className="sr-only">{label}</span>
        {variant === 'table' || variant === 'terminal' ? (
          <div className="nova-skeleton-rows" aria-hidden="true">
            {Array.from({ length: 3 }, (_, index) => <Skeleton key={index} />)}
          </div>
        ) : (
          <Skeleton className="nova-skeleton-primary" aria-hidden="true" />
        )}
      </div>
    )
  }

  return (
    <div className={`nova-page-skeleton is-${variant}`} role="status" aria-label={label}>
      <span className="sr-only">{label}</span>
      <div className="nova-skeleton-summary" aria-hidden="true">
        {Array.from({ length: variant === 'dashboard' ? 4 : 3 }, (_, index) => (
          <Skeleton key={index} className="nova-skeleton-summary-card" />
        ))}
      </div>
      {variant === 'table' ? null : (
        <div className="nova-skeleton-body" aria-hidden="true">
          <Skeleton className="nova-skeleton-primary" />
          <div className="nova-skeleton-side">
            <Skeleton />
            <Skeleton />
          </div>
        </div>
      )}
      {variant === 'table' || variant === 'terminal' ? (
        <div className="nova-skeleton-rows" aria-hidden="true">
          {Array.from({ length: 6 }, (_, index) => <Skeleton key={index} />)}
        </div>
      ) : null}
    </div>
  )
}
