import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  RefreshCw,
  ShieldAlert,
  Square,
  Wallet,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  emergencyStop,
  getDashboardSummary,
  getLiveFlow,
  getOrders,
  getSetupStatus,
  stopEngine,
} from '../api/dashboard'
import type { DashboardSummary, LiveFlowStep, OrderEvent, SetupStatus } from '../types'

// ─── Helpers ─────────────────────────────────────────────────────────────────

function currency(value: number | null | undefined) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return `₹${Number(value).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
}

function useAnimatedNumber(target: number | null) {
  const [display, setDisplay] = useState(target ?? 0)
  const ref = useRef(display)
  ref.current = display
  useEffect(() => {
    if (target == null) return
    const start = ref.current
    const diff = target - start
    if (Math.abs(diff) < 0.01) return
    const duration = 600
    const t0 = performance.now()
    const tick = (now: number) => {
      const t = Math.min((now - t0) / duration, 1)
      const ease = t < 0.5 ? 2 * t * t : (4 - 2 * t) * t - 1
      setDisplay(start + diff * ease)
      if (t < 1) requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  }, [target])
  return display
}

// ─── Can Trade? banner ───────────────────────────────────────────────────────

function CanTradeBanner({ summary, setup }: { summary: DashboardSummary; setup: SetupStatus }) {
  const { app_state, mode } = summary
  if (app_state.emergency_stop) return (
    <div className="can-trade-danger flex items-center gap-3">
      <ShieldAlert className="text-red-400 flex-shrink-0" size={20} />
      <div>
        <p className="text-sm font-semibold text-red-300">Blocked — Emergency stop active</p>
        <p className="text-xs text-red-400/80 mt-0.5">Go to Controls to resume.</p>
      </div>
    </div>
  )
  if (app_state.global_kill_switch) return (
    <div className="can-trade-danger flex items-center gap-3">
      <AlertTriangle className="text-red-400 flex-shrink-0" size={20} />
      <div>
        <p className="text-sm font-semibold text-red-300">Blocked — Kill switch is ON</p>
        <p className="text-xs text-red-400/80 mt-0.5">Go to Controls to re-enable.</p>
      </div>
    </div>
  )
  if (!setup.dhan_connected) return (
    <div className="can-trade-blocked flex items-center gap-3">
      <AlertTriangle className="text-amber-400 flex-shrink-0" size={20} />
      <div>
        <p className="text-sm font-semibold text-amber-300">Blocked — Dhan not connected</p>
        <p className="text-xs text-amber-400/80 mt-0.5">Go to Setup and enter your Dhan credentials.</p>
      </div>
    </div>
  )
  if (!setup.engine_started) return (
    <div className="can-trade-blocked flex items-center gap-3">
      <Square className="text-amber-400 flex-shrink-0" size={20} />
      <div>
        <p className="text-sm font-semibold text-amber-300">Blocked — Engine not running</p>
        <p className="text-xs text-amber-400/80 mt-0.5">Go to Setup and start the engine.</p>
      </div>
    </div>
  )
  if (mode.live_orders_enabled) return (
    <div className="can-trade-danger flex items-center gap-3">
      <span className="status-dot dot-red dot-pulse flex-shrink-0" />
      <div>
        <p className="text-sm font-semibold text-red-300">LIVE ORDERS ENABLED — real money orders will be placed</p>
        <p className="text-xs text-red-400/80 mt-0.5">All alerts will route to Dhan with real funds.</p>
      </div>
    </div>
  )
  return (
    <div className="can-trade-ready flex items-center gap-3">
      <CheckCircle2 style={{ color: '#98e94d', flexShrink: 0 }} size={20} />
      <div>
        <p className="text-sm font-semibold" style={{ color: '#98e94d' }}>Ready to trade</p>
        <p className="text-xs mt-0.5" style={{ color: 'rgba(152,233,77,0.65)' }}>Engine running · Dhan connected · {setup.mode.dhan_mode} mode</p>
      </div>
    </div>
  )
}

// ─── Pipeline strip ──────────────────────────────────────────────────────────

const STEP_STYLE: Record<string, React.CSSProperties> = {
  done:    { background: 'rgba(152,233,77,0.08)', border: '1px solid rgba(152,233,77,0.2)',  color: '#98e94d' },
  active:  { background: 'rgba(59,130,246,0.1)',  border: '1px solid rgba(59,130,246,0.3)',  color: '#93c5fd' },
  blocked: { background: 'rgba(239,68,68,0.08)',  border: '1px solid rgba(239,68,68,0.25)', color: '#f87171' },
  error:   { background: 'rgba(239,68,68,0.08)',  border: '1px solid rgba(239,68,68,0.25)', color: '#f87171' },
  pending: { background: '#151513',               border: '1px solid #2a2a2a',              color: '#77736c' },
}
const CONN_COLOR: Record<string, string> = {
  done: '#98e94d', active: '#3b82f6', blocked: '#ef4444', error: '#ef4444', pending: '#2b2a26',
}

function PipelineStrip({ steps }: { steps: LiveFlowStep[] }) {
  if (!steps.length) return <p className="text-sm" style={{ color: '#77736c' }}>No flow data.</p>
  return (
    <div className="flex items-stretch gap-0">
      {steps.map((step, i) => (
        <div key={step.step} className="flex items-center flex-1 min-w-0">
          <div
            className={`flex-1 rounded-lg px-2 py-2.5 text-center min-w-0 ${step.status === 'active' ? 'step-active-anim' : ''}`}
            style={STEP_STYLE[step.status] ?? STEP_STYLE.pending}
            title={step.message ?? undefined}
          >
            <p className="text-xs font-medium leading-tight truncate">{step.step.replace(/_/g, ' ')}</p>
            <p className="text-xs opacity-60 mt-0.5 capitalize">{step.status}</p>
          </div>
          {i < steps.length - 1 && (
            <div className="h-0.5 w-3 flex-shrink-0" style={{ background: CONN_COLOR[step.status] ?? CONN_COLOR.pending }} />
          )}
        </div>
      ))}
    </div>
  )
}

// ─── Metric card ─────────────────────────────────────────────────────────────

function MetricCard({ label, value, accent }: { label: string; value: string; accent?: 'green' | 'red' | 'amber' }) {
  const valueColor = accent === 'green' ? '#98e94d' : accent === 'red' ? '#f87171' : accent === 'amber' ? '#fbbf24' : '#f4f1ea'
  return (
    <div className="card py-3 px-4">
      <p className="text-xs uppercase tracking-wide mb-1" style={{ color: '#77736c' }}>{label}</p>
      <p className="text-xl font-semibold" style={{ color: valueColor }}>{value}</p>
    </div>
  )
}

// ─── Order badges ─────────────────────────────────────────────────────────────

function OrderStatusBadge({ order }: { order: OrderEvent }) {
  if (order.blocked) return <span className="badge-red">BLOCKED</span>
  const s = (order.status ?? order.phase ?? '').toUpperCase()
  if (s.includes('TRADED') || s.includes('FILLED')) return <span className="badge-green">{s}</span>
  if (s.includes('REJECT') || s.includes('FAIL'))   return <span className="badge-red">{s}</span>
  if (s.includes('PENDING') || s.includes('PLACED')) return <span className="badge-yellow">{s}</span>
  return <span className="badge-gray">{s || '—'}</span>
}

function ActionBadge({ order }: { order: OrderEvent }) {
  const a = (order.normalized_action ?? order.action ?? '').toUpperCase()
  if (a === 'BUY'  || a === 'LONG')  return <span className="badge-blue">{a}</span>
  if (a === 'SELL' || a === 'SHORT') return <span className="badge-red">{a}</span>
  return <span className="badge-gray">{a || '—'}</span>
}

// ─── Dashboard ───────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const [summary, setSummary]   = useState<DashboardSummary | null>(null)
  const [setup,   setSetup]     = useState<SetupStatus | null>(null)
  const [flow,    setFlow]      = useState<LiveFlowStep[]>([])
  const [orders,  setOrders]    = useState<OrderEvent[]>([])

  const animBalance  = useAnimatedNumber(summary?.wallet?.available_balance ?? null)
  const animUtilised = useAnimatedNumber(summary?.wallet?.utilized_amount ?? null)

  const loadData = async () => {
    const [s, st, f, o] = await Promise.all([
      getDashboardSummary(), getSetupStatus(), getLiveFlow(), getOrders(),
    ])
    setSummary(s.data); setSetup(st.data); setFlow(f.data); setOrders(o.data.slice(0, 10))
  }

  useEffect(() => {
    loadData().catch(() => toast.error('Backend APIs unreachable'))
    const id = window.setInterval(() => loadData().catch(() => undefined), 2000)
    return () => window.clearInterval(id)
  }, [])

  const copyWebhook = async () => {
    if (!setup?.webhook_url) return
    await navigator.clipboard.writeText(setup.webhook_url)
    toast.success('Webhook URL copied')
  }

  const runEmergencyStop = async () => {
    if (!window.confirm('Activate emergency stop?')) return
    await emergencyStop(); toast.error('Emergency stop activated'); loadData()
  }

  const runStopEngine = async () => {
    await stopEngine(); toast.warning('Engine stopped'); loadData()
  }

  if (!summary || !setup) return (
    <div className="flex min-h-[60vh] items-center justify-center gap-3" style={{ color: '#77736c' }}>
      <RefreshCw className="animate-spin" size={18} />
      <span className="text-sm">Loading dashboard…</span>
    </div>
  )

  const openPosition = summary.open_position
  const latestAlert  = summary.last_logs?.webhook as Record<string, unknown> | undefined

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold" style={{ color: '#f4f1ea' }}>Dashboard</h1>
          <p className="mt-0.5 text-sm" style={{ color: '#9a968f' }}>Live system state and recent orders.</p>
        </div>
        <button onClick={loadData} className="btn-ghost flex items-center gap-2 text-sm py-1.5">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {/* Can Trade? banner — always first */}
      <CanTradeBanner summary={summary} setup={setup} />

      {/* Metric strip */}
      <section className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MetricCard label="Engine"    value={setup.engine_started  ? 'Running'   : 'Stopped'}  accent={setup.engine_started  ? 'green' : 'amber'} />
        <MetricCard label="Dhan"      value={setup.dhan_connected  ? 'Connected' : 'Offline'}  accent={setup.dhan_connected  ? 'green' : 'red'}   />
        <MetricCard label="Available" value={`₹${Math.round(animBalance).toLocaleString('en-IN')}`} />
        <MetricCard label="Utilised"  value={`₹${Math.round(animUtilised).toLocaleString('en-IN')}`} accent={animUtilised > 0 ? 'amber' : undefined} />
      </section>

      {/* Pipeline + quick controls */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="card space-y-3 lg:col-span-2">
          <h2 className="text-xs font-semibold uppercase tracking-widest" style={{ color: '#77736c' }}>Alert lifecycle</h2>
          <PipelineStrip steps={flow} />
        </div>
        <div className="card space-y-3">
          <h2 className="text-xs font-semibold uppercase tracking-widest" style={{ color: '#77736c' }}>Quick controls</h2>
          <button onClick={runStopEngine}    className="btn-ghost  flex w-full items-center justify-center gap-2 text-sm"><Square size={14} /> Stop Engine</button>
          <button onClick={runEmergencyStop} className="btn-danger flex w-full items-center justify-center gap-2 text-sm"><ShieldAlert size={14} /> Emergency Stop</button>
          <p className="text-xs" style={{ color: '#77736c' }}>Full controls at <a href="/app/controls" className="underline underline-offset-2" style={{ color: '#9a968f' }}>/controls</a>.</p>
        </div>
      </section>

      {/* Position + wallet */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card space-y-3">
          <h2 className="text-xs font-semibold uppercase tracking-widest" style={{ color: '#77736c' }}>Open position</h2>
          {openPosition.has_open_position ? (
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
              {([['Symbol', openPosition.trading_symbol], ['Security ID', openPosition.security_id], ['Qty', openPosition.qty], ['Entry order', openPosition.entry_order_id], ['Entry price', openPosition.entry_price != null ? currency(openPosition.entry_price) : '—'], ['Opened', openPosition.opened_at ? new Date(openPosition.opened_at).toLocaleTimeString() : '—']] as [string, unknown][]).map(([l, v]) => (
                <div key={String(l)}><p className="text-xs" style={{ color: '#77736c' }}>{String(l)}</p><p className="font-medium truncate">{String(v ?? '—')}</p></div>
              ))}
            </div>
          ) : (
            <p className="text-sm" style={{ color: '#77736c' }}>No open position tracked locally.</p>
          )}
        </div>

        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-widest" style={{ color: '#77736c' }}>Wallet</h2>
            <Wallet size={14} style={{ color: '#77736c' }} />
          </div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
            {([['Available', currency(summary.wallet.available_balance)], ['Utilised', currency(summary.wallet.utilized_amount)], ['Withdrawable', currency(summary.wallet.withdrawable_balance)], ['Mode', setup.mode.dhan_mode]] as [string, string][]).map(([l, v]) => (
              <div key={l}><p className="text-xs" style={{ color: '#77736c' }}>{l}</p><p className="font-medium" style={{ color: l === 'Mode' && v === 'REAL' ? '#f87171' : '#f4f1ea' }}>{v}</p></div>
            ))}
          </div>
        </div>
      </section>

      {/* Webhook + latest alert */}
      <section className="card space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-widest" style={{ color: '#77736c' }}>TradingView webhook</h2>
        <div className="flex items-center gap-2 rounded-lg px-3 py-2" style={{ border: '1px solid #2a2a2a', background: '#090908' }}>
          <p className="flex-1 min-w-0 truncate font-mono text-xs" style={{ color: '#bcb5aa' }}>{setup.webhook_url || '—'}</p>
          <button onClick={copyWebhook} className="btn-ghost p-1.5" style={{ color: '#9a968f' }} title="Copy"><Copy size={13} /></button>
        </div>
        {latestAlert && (
          <p className="text-xs" style={{ color: '#77736c' }}>
            Last alert: <span style={{ color: '#98e94d' }}>{String(latestAlert.event_type ?? '—')}</span>
            {' · '}<span className="font-mono" style={{ color: '#bcb5aa' }}>{String(latestAlert.signal_id ?? '—')}</span>
          </p>
        )}
      </section>

      {/* Orders */}
      <section className="card">
        <h2 className="mb-4 text-xs font-semibold uppercase tracking-widest" style={{ color: '#77736c' }}>Recent orders</h2>
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead style={{ borderBottom: '1px solid #222222' }}>
              <tr className="text-xs uppercase tracking-wide" style={{ color: '#77736c' }}>
                {['Time', 'Action', 'Symbol', 'Qty', 'Status', 'Reason'].map(h => (
                  <th key={h} className="pb-2 pr-4 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {orders.length === 0 ? (
                <tr><td colSpan={6} className="py-6 text-center text-sm" style={{ color: '#77736c' }}>No order events yet.</td></tr>
              ) : orders.map(order => (
                <tr key={order.id} className="transition-colors" style={{ borderBottom: '1px solid #1a1a1a' }}
                  onMouseEnter={e => (e.currentTarget.style.background = '#1b1a17')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                  <td className="py-2.5 pr-4 text-xs whitespace-nowrap" style={{ color: '#77736c' }}>{order.created_at ? new Date(order.created_at).toLocaleTimeString() : '—'}</td>
                  <td className="py-2.5 pr-4"><ActionBadge order={order} /></td>
                  <td className="py-2.5 pr-4 font-mono text-xs font-medium">{order.trading_symbol ?? order.normalized_symbol ?? '—'}</td>
                  <td className="py-2.5 pr-4" style={{ color: '#d8d3c8' }}>{order.normalized_qty ?? order.qty ?? '—'}</td>
                  <td className="py-2.5 pr-4"><OrderStatusBadge order={order} /></td>
                  <td className="py-2.5 text-xs max-w-xs truncate" style={{ color: '#77736c' }}>{order.reason ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
