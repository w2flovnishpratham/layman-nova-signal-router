import type { RuntimeStatus } from '../api'

export function RiskAutomationCard({ runtime }: { runtime: RuntimeStatus | null }) {
  const active = runtime?.config.active ?? {}
  const maxLoss = numberValue(active.max_daily_loss)
  const maxTrades = numberValue(active.max_trades_per_day)
  const cutoff = String(active.entry_cutoff_ist || 'No cutoff')
  return (
    <section className="sidebar-card terminal-risk-card">
      <div className="sidebar-title"><span>Risk & Automation</span><strong>1 position max</strong></div>
      <dl>
        <div><dt>Daily loss cap</dt><dd>{maxLoss > 0 ? `₹${maxLoss.toLocaleString('en-IN')}` : 'No cap'}</dd></div>
        <div><dt>Max trades / day</dt><dd>{maxTrades > 0 ? maxTrades : 'No cap'}</dd></div>
        <div><dt>Entry cutoff</dt><dd>{cutoff === 'No cutoff' ? cutoff : `${cutoff} IST`}</dd></div>
        <div><dt>Open position policy</dt><dd>{runtime?.position.has_open_position ? '1 / 1 used' : '0 / 1 used'}</dd></div>
      </dl>
    </section>
  )
}

function numberValue(value: unknown): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}
