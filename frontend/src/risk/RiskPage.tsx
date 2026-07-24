import { AlertTriangle, Loader2, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { getRiskOverview, paise, type RiskOverview, type RiskUtilisation } from './riskApi'

function Meter({ label, util, money }: { label: string; util: RiskUtilisation; money?: boolean }) {
  const used = money ? paise(util.used) : String(util.used)
  const limit = util.unlimited ? 'No limit' : money ? paise(util.limit) : String(util.limit)
  return (
    <div className="nova-risk-meter">
      <div className="nova-risk-meter-head">
        <span>{label}</span>
        {/* Non-colour text always states the numbers, including the unlimited case. */}
        <strong>{used} / {limit}{util.pct !== null ? ` · ${util.pct}%` : ''}</strong>
      </div>
      <div className="nova-risk-track" role="img" aria-label={`${label}: ${used} of ${limit}`}>
        {util.pct !== null ? (
          <div className="nova-risk-fill" style={{ width: `${util.pct}%` }} data-high={util.pct >= 80} />
        ) : null}
      </div>
    </div>
  )
}

export function RiskPage() {
  const [data, setData] = useState<RiskOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await getRiskOverview())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load risk limits.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  return (
    <div className="nova-signals">
      <header className="nova-signals-head">
        <div>
          <h1>Risk</h1>
          <p>
            Server-side strategy fan-out limits, enforced before an order intent is accepted.
            These are separate from the engine&apos;s runtime exit settings.
          </p>
        </div>
      </header>

      {loading ? (
        <p className="nova-signals-state" role="status"><Loader2 size={16} /> Loading risk limits…</p>
      ) : error ? (
        <p className="nova-signals-state" role="alert"><AlertTriangle size={16} /> {error}
          <button type="button" className="conv-pill" onClick={() => void load()}>Retry</button>
        </p>
      ) : !data ? null : (
        <>
          <section className="nova-hooks-card" aria-label="Account limits">
            <div className="nova-hooks-card-head">
              <strong>Account limits</strong>
              <span className="nova-hooks-method">IST day {data.trade_date_ist}</span>
            </div>
            <p>
              {data.user.kill_switch
                ? 'Account kill switch is ON — new strategy order intents are blocked.'
                : 'Account kill switch is off.'}
            </p>
            <div className="nova-risk-grid">
              <div><span>Max lots / order</span><strong>{data.user.max_lots_per_order || 'No limit'}</strong></div>
              <div><span>Max orders / day</span><strong>{data.user.max_orders_per_day || 'No limit'}</strong></div>
              <div><span>Max notional / trade</span><strong>{data.user.max_notional_per_trade_paise ? paise(data.user.max_notional_per_trade_paise) : 'No limit'}</strong></div>
              <div><span>Max loss / day</span><strong>{data.user.max_loss_per_day_paise ? paise(data.user.max_loss_per_day_paise) : 'No limit'}</strong></div>
            </div>
            <p className="nova-risk-note">A limit of zero means no limit is configured, not zero allowed.</p>
          </section>

          {data.strategies.length === 0 ? (
            <p className="nova-signals-state" role="status">
              No strategy activity or per-strategy overrides recorded for {data.trade_date_ist}.
            </p>
          ) : data.strategies.map((row) => (
            <section key={row.strategy_name} className="nova-hooks-card" aria-label={`${row.strategy_name} limits`}>
              <div className="nova-hooks-card-head">
                <ShieldCheck size={15} />
                <strong>{row.strategy_name}</strong>
                {row.kill_switch ? <span className="nova-sig-bad">kill switch on</span> : null}
              </div>
              <Meter label="Orders today" util={row.utilisation.orders} />
              <Meter label="Notional per trade" util={row.utilisation.notional} money />
              <Meter label="Daily loss budget" util={row.utilisation.loss} money />
              <p className="nova-risk-note">
                Realised P&amp;L today: {paise(row.usage.realized_pnl_paise)}
                {row.usage.realized_pnl_paise >= 0 ? ' — no loss budget consumed.' : ''}
              </p>
            </section>
          ))}
        </>
      )}
    </div>
  )
}
