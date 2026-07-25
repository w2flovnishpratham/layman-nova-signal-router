import type { RuntimeStatus } from '../api'

export function EngineConfigCard({ runtime, onStop }: { runtime: RuntimeStatus | null; onStop: () => void }) {
  const active = runtime?.config.active ?? {}
  return (
    <section className="sidebar-card terminal-engine-card">
      <div className="sidebar-title"><span>Engine</span><strong>{runtime?.engine.state ?? 'LOADING'}</strong></div>
      <label className="terminal-engine-strategy">
        <span>Selected strategy</span>
        <strong>{runtime?.selected_strategy?.display_name ?? 'No strategy selected'}</strong>
      </label>
      <div className="terminal-engine-facts">
        <div><span>Allowed sides</span><strong>{String(active.allowed_option_side || 'BOTH')}</strong></div>
        <div><span>Lots</span><strong>{numberValue(active.configured_lots, 1)}</strong></div>
        <div><span>SL</span><strong>{numberValue(active.option_sl_percent, 0)}%</strong></div>
        <div><span>TP</span><strong>{numberValue(active.option_tp_percent, 0)}%</strong></div>
      </div>
      <button type="button" className="terminal-stop-engine" onClick={onStop}>Stop Engine</button>
      <p>{runtime?.engine.mode === 'paper' ? 'Paper Trading · all orders simulated' : 'Live Trading · real-money routing'}</p>
    </section>
  )
}

export function DailyDrawdownCard({ runtime }: { runtime: RuntimeStatus | null }) {
  const active = runtime?.config.active ?? {}
  const limit = numberValue(active.max_daily_loss, 0)
  const pnl = runtime?.pnl.session ?? 0
  const used = limit > 0 && pnl < 0 ? Math.min(100, Math.abs(pnl) / limit * 100) : 0
  return (
    <section className="sidebar-card daily-drawdown-card">
      <div className="sidebar-title"><span>Daily Drawdown</span><strong>{used.toFixed(1)}%</strong></div>
      <div className="daily-drawdown-track"><span style={{ width: `${used}%` }} /></div>
      <dl>
        <div><dt>Current</dt><dd className={pnl < 0 ? 'negative' : 'positive'}>{signedMoney(pnl)}</dd></div>
        <div><dt>Limit</dt><dd>{limit > 0 ? `₹${limit.toLocaleString('en-IN')}` : 'No cap'}</dd></div>
        <div><dt>Remaining</dt><dd>{limit > 0 ? `₹${Math.max(0, limit - Math.abs(Math.min(0, pnl))).toLocaleString('en-IN')}` : '—'}</dd></div>
      </dl>
    </section>
  )
}

function numberValue(value: unknown, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function signedMoney(value: number): string {
  return `${value >= 0 ? '+' : '-'}₹${Math.abs(value).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
