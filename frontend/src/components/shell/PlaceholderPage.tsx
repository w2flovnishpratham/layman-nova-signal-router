import { ROUTE_META, type AppRoute } from '../../appRoutes'

// Truthful placeholders: they state what the page will do and which existing
// data it will use. They deliberately contain NO fabricated metrics and NO
// controls that imply a write the backend cannot yet perform.
const SOURCES: Partial<Record<AppRoute, string[]>> = {
  signals: [
    'Webhook and signal events already recorded by the router',
    'Routing outcome per signal (routed, blocked, duplicate) and ingest latency',
  ],
  automations: [
    'Server-side guardrails that already run (daily loss cap and square-off)',
    'Scheduled jobs the backend already executes',
  ],
  webhooks: [
    'Existing per-strategy webhook endpoints and their delivery results',
    'The TradingView alert template the router already accepts',
  ],
  credentials: [
    'The existing broker credential vault status (masked values only)',
    'Pre-market verification results already produced by the health check',
  ],
  risk: [
    'The risk limits the backend already enforces before routing an order',
    'Current session usage against those limits',
  ],
  reports: [
    'Daily session results already stored by the router',
    'Per-strategy performance already computed from closed trades',
  ],
  settings: [
    'Account and session information already exposed by the runtime',
    'Existing preferences (mode default, reduced motion)',
  ],
  'strategies/new': [
    'The existing Pine import and validation pipeline',
    'The current strategy catalog and setup schema',
  ],
}

export function PlaceholderPage({ route }: { route: AppRoute }) {
  const meta = ROUTE_META[route]
  const sources = SOURCES[route] ?? []
  return (
    <div className="nova-placeholder">
      <span className="nova-placeholder-status">Coming in the next build</span>
      <h1 style={{ marginTop: 14 }}>{meta.label}</h1>
      <p>{meta.description}</p>
      <div className="nova-placeholder-card">
        <strong style={{ fontSize: 13.5 }}>What this page will use</strong>
        {sources.length ? (
          <ul className="nova-placeholder-list">
            {sources.map((s) => <li key={s}>{s}</li>)}
          </ul>
        ) : (
          <p style={{ margin: '10px 0 0' }}>Existing authoritative router data only.</p>
        )}
        <p style={{ margin: '16px 0 0', fontSize: 13 }}>
          This screen is not implemented yet, so nothing here is live. No numbers on this page
          are real, because none are shown — the redesign builds each page on data the backend
          already provides.
        </p>
      </div>
    </div>
  )
}
