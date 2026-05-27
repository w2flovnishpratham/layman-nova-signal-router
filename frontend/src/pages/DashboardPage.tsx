import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  Play,
  RefreshCw,
  ShieldAlert,
  Square,
  Wallet,
  Clock,
  Circle,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  emergencyStop,
  getDashboardSummary,
  getLiveFlow,
  getOrders,
  getSetupStatus,
  startEngine,
  stopEngine,
} from '../api/dashboard'
import type { DashboardSummary, LiveFlowStep, OrderEvent, SetupStatus } from '../types'
import ConfirmModal from '../components/ConfirmModal'

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
      <CheckCircle2 style={{ color: 'var(--c-lime-text)', flexShrink: 0 }} size={20} />
      <div>
        <p className="text-sm font-semibold" style={{ color: 'var(--c-lime-text)' }}>Ready to trade</p>
        <p className="text-xs mt-0.5" style={{ color: 'rgba(152,233,77,0.65)' }}>Engine running · Dhan connected · {setup.mode.dhan_mode} mode</p>
      </div>
    </div>
  )
}

// ─── Pipeline strip ──────────────────────────────────────────────────────────

// ─── Pipeline strip labels mapping ──────────────────────────────────────────

const PIPELINE_LABELS: Record<string, string> = {
  // Signal desk stages
  WAITING_ENTRY_SIGNAL: 'Ready',
  ENTRY_SIGNAL_RECEIVED: 'Entry Signal',
  ENTRY_RISK_CHECK_PASSED: 'Risk Gate',
  ENTRY_ORDER_PLACED: 'Order Sent',
  ENTERED: 'Entered',
  WAITING_EXIT_SIGNAL: 'Wait Exit',
  EXIT_SIGNAL_RECEIVED: 'Exit Signal',
  EXIT_ORDER_PLACED: 'Exit Order',
  EXITED: 'Exited',

  // Custom step labels
  WAITING_ENTRY: 'Ready',
  ENTRY_RISK_CHECK: 'Risk Gate',
  ENTRY_ORDER_SENDING: 'Order Sent',
  OPEN: 'Entered',
  WAITING_EXIT: 'Wait Exit',
  EXIT_ORDER_SENDING: 'Order Sent',
  BLOCKED: 'Blocked',
}

const STEP_THEMES: Record<string, { borderCls: string; textCls: string; bgStyle: string; stroke: string }> = {
  done:    { borderCls: 'border-[#98e94d]/40', textCls: 'text-[#98e94d]',  bgStyle: 'rgba(152,233,77,0.1)',  stroke: '#98e94d' },
  active:  { borderCls: 'border-blue-500/50',  textCls: 'text-blue-400',   bgStyle: 'rgba(96,165,250,0.1)',  stroke: '#60a5fa' },
  blocked: { borderCls: 'border-red-500/50',   textCls: 'text-red-400',    bgStyle: 'rgba(239,68,68,0.1)',   stroke: '#ef4444' },
  error:   { borderCls: 'border-red-500/50',   textCls: 'text-red-400',    bgStyle: 'rgba(239,68,68,0.1)',   stroke: '#ef4444' },
  pending: { borderCls: 'border-[#2b2a26]',    textCls: 'text-[#5e5a53]',  bgStyle: 'transparent',          stroke: '#2b2a26' },
}

function PipelineStrip({ steps }: { steps: LiveFlowStep[] }) {
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)

  // Default selection to active step or latest completed step
  useEffect(() => {
    if (!steps.length) {
      setSelectedIndex(null)
      return
    }

    const activeIdx = steps.findIndex(s => s.status === 'active')
    if (activeIdx !== -1) {
      setSelectedIndex(activeIdx)
      return
    }

    const latestDone = steps.reduce((acc, s, idx) =>
      s.status === 'done' || s.status === 'blocked' || s.status === 'error' ? idx : acc, 0
    )
    setSelectedIndex(latestDone)
  }, [steps])

  if (!steps.length) return <p className="text-sm" style={{ color: 'var(--c-text-4)' }}>No flow data.</p>

  const safeSelectedIndex = selectedIndex !== null && selectedIndex < steps.length ? selectedIndex : 0
  const selectedStep = steps[safeSelectedIndex] ?? null

  return (
    <div className="space-y-4">
      
      {/* Desktop Stepper Track (horizontal) */}
      <div className="hidden md:flex items-center justify-between w-full overflow-x-auto py-4 px-1.5 terminal-scrollbar scroll-smooth">
        {steps.map((step, i) => {
          const theme = STEP_THEMES[step.status] ?? STEP_THEMES.pending
          const Icon = step.status === 'done' ? CheckCircle2 :
                       step.status === 'active' ? Clock :
                       step.status === 'blocked' ? ShieldAlert :
                       step.status === 'error' ? AlertTriangle : Circle

          const isSelected = selectedIndex === i
          const labelText = PIPELINE_LABELS[step.step] || step.step.replace(/_/g, ' ')

          // Check if connector line should render completed glow
          const nextStatus = steps[i + 1]?.status
          const connColor = (step.status === 'done' && (nextStatus === 'done' || nextStatus === 'active')) ? '#98e94d' :
                            (step.status === 'done' && (nextStatus === 'blocked' || nextStatus === 'error')) ? '#ef4444' :
                            (step.status === 'active' || step.status === 'done') ? 'var(--c-border-4)' : 'var(--c-border-1)'

          return (
            <div key={step.step} className="flex items-center flex-1 min-w-0">
              
              {/* Stepper Node Button */}
              <button
                onClick={() => setSelectedIndex(i)}
                className={`flex flex-col items-center flex-shrink-0 cursor-pointer focus:outline-none transition-transform hover:scale-[1.05] ${
                  isSelected ? 'scale-[1.02]' : ''
                }`}
                style={{ width: '72px' }}
                title={`Click to inspect details of ${labelText}`}
              >
                {/* Node Circle */}
                <div className={`w-8 h-8 rounded-full flex items-center justify-center border transition-all duration-200 ${theme.borderCls} ${
                  isSelected ? 'ring-2 ring-nova-50/20' : ''
                }`}>
                  {step.status === 'active' ? (
                    <Icon size={14} className={`${theme.textCls} animate-pulse`} />
                  ) : (
                    <Icon size={14} className={theme.textCls} />
                  )}
                </div>

                {/* Node Label (Wraps on multiple lines cleanly to prevent truncation) */}
                <p className={`text-[10px] font-bold text-center mt-2 leading-tight uppercase tracking-wider transition-colors max-w-[70px] min-h-[24px] break-words ${
                  isSelected ? 'font-black underline underline-offset-4 decoration-[#98e94d]' :
                  step.status === 'active' ? 'text-blue-400 font-black' :
                  step.status === 'done' ? 'opacity-70' :
                  step.status === 'blocked' || step.status === 'error' ? 'text-red-400' : 'opacity-30'
                }`} style={isSelected ? { color: 'var(--c-lime-text)' } : undefined}>
                  {labelText}
                </p>
              </button>

              {/* Stepper Connector Link Segment */}
              {i < steps.length - 1 && (
                <div 
                  className="flex-1 h-[2px] mx-1 min-w-[12px] md:min-w-[20px] transition-colors duration-300" 
                  style={{ background: connColor }} 
                />
              )}
            </div>
          )
        })}
      </div>

      {/* Mobile Stepper Track (vertical) */}
      <div className="flex md:hidden flex-col gap-0 w-full py-2">
        {steps.map((step, i) => {
          const theme = STEP_THEMES[step.status] ?? STEP_THEMES.pending
          const Icon = step.status === 'done' ? CheckCircle2 :
                       step.status === 'active' ? Clock :
                       step.status === 'blocked' ? ShieldAlert :
                       step.status === 'error' ? AlertTriangle : Circle

          const isSelected = selectedIndex === i
          const labelText = PIPELINE_LABELS[step.step] || step.step.replace(/_/g, ' ')

          // Check if connector line should render completed glow
          const nextStatus = steps[i + 1]?.status
          const connColor = (step.status === 'done' && (nextStatus === 'done' || nextStatus === 'active')) ? '#98e94d' :
                            (step.status === 'done' && (nextStatus === 'blocked' || nextStatus === 'error')) ? '#ef4444' :
                            (step.status === 'active' || step.status === 'done') ? 'var(--c-border-4)' : 'var(--c-border-1)'

          return (
            <div key={step.step + '-mobile'} className="flex flex-col">
              
              {/* Stepper Node Row Button */}
              <button
                onClick={() => setSelectedIndex(i)}
                className={`flex items-center w-full text-left p-1.5 rounded-xl transition-all cursor-pointer ${
                  isSelected ? 'bg-[#151513]/60' : 'hover:bg-[#1b1a17]/30'
                }`}
                title={`Inspect ${labelText}`}
              >
                {/* Node Circle */}
                <div className={`w-8 h-8 rounded-full flex items-center justify-center border flex-shrink-0 transition-all duration-200 ${theme.borderCls} ${
                  isSelected ? 'ring-2 ring-nova-50/20' : ''
                }`}>
                  {step.status === 'active' ? (
                    <Icon size={14} className={`${theme.textCls} animate-pulse`} />
                  ) : (
                    <Icon size={14} className={theme.textCls} />
                  )}
                </div>

                {/* Node Text Content (Next to circle) */}
                <div className="ml-3">
                  <p className={`text-[11px] font-bold uppercase tracking-wider transition-colors ${
                    isSelected ? 'font-black underline underline-offset-2' :
                    step.status === 'active' ? 'text-blue-400 font-black' :
                    step.status === 'done' ? 'opacity-70' :
                    step.status === 'blocked' || step.status === 'error' ? 'text-red-400' : 'opacity-30'
                  }`}>
                    {labelText}
                  </p>
                  <p className="text-[9px] font-mono leading-none mt-0.5" style={{ color: "var(--c-text-4)" }}>
                    {step.status}
                  </p>
                </div>
              </button>

              {/* Vertical Stepper Connector Line */}
              {i < steps.length - 1 && (
                <div 
                  className="w-[2px] h-3 transition-colors duration-300"
                  style={{ background: connColor, marginLeft: '21px' }} 
                />
              )}
            </div>
          )
        })}
      </div>

      {/* Interactive Step Details Inspector Card */}
      {selectedStep && (
        <div 
          className="rounded-xl p-4 mt-2 transition-all duration-300 relative overflow-hidden bg-[#11110f]/40 border border-[#1c1c19]"
          style={{ boxShadow: 'inset 0 1px 0 rgba(244, 241, 234, 0.02)' }}
        >
          {/* Subtle soft backdrop glow matching active color */}
          <div 
            className="absolute top-0 right-0 w-36 h-36 pointer-events-none blur-3xl rounded-full" 
            style={{
              background: selectedStep.status === 'done' ? 'rgba(152, 233, 77, 0.04)' :
                          selectedStep.status === 'active' ? 'rgba(96, 165, 250, 0.04)' :
                          selectedStep.status === 'blocked' || selectedStep.status === 'error' ? 'rgba(239, 68, 68, 0.04)' : 'transparent'
            }}
          />

          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b pb-3 mb-3">
            <div className="space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className={`text-[9px] font-mono font-bold px-2 py-0.5 rounded ${
                    selectedStep.status === 'done' ? 'bg-[#98e94d]/10 text-[#98e94d] border border-[#98e94d]/15' :
                    selectedStep.status === 'active' ? 'bg-blue-500/10 text-blue-400 border border-blue-400/15' :
                    selectedStep.status === 'blocked' || selectedStep.status === 'error' ? 'bg-red-500/10 text-red-400 border border-red-500/15' :
                    ''
                  }`}
                  style={!['done','active','blocked','error'].includes(selectedStep.status) ? { background: 'var(--c-raised)', borderColor: 'var(--c-border-3)', color: 'var(--c-text-4)' } : undefined}>
                  STAGE { (selectedIndex ?? 0) + 1 } / { steps.length }
                </span>
                <h3 className="text-xs font-bold uppercase tracking-wide" style={{ color: "var(--c-text-1)" }}>
                  {PIPELINE_LABELS[selectedStep.step] || selectedStep.step.replace(/_/g, ' ')}
                </h3>
              </div>
              <p className="text-[10px] font-mono leading-none" style={{ color: "var(--c-text-5)" }}>
                System state code: {selectedStep.step}
              </p>
            </div>

            <div className="text-left sm:text-right flex flex-row sm:flex-col items-center sm:items-end justify-between sm:justify-start gap-1">
              <span className={`inline-flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider ${
                selectedStep.status === 'done' ? 'text-[var(--c-lime-text)]' :
                selectedStep.status === 'active' ? 'text-blue-400 animate-pulse' :
                selectedStep.status === 'blocked' || selectedStep.status === 'error' ? 'text-red-400' : 'text-[#77736c]'
              }`}>
                <span className={`w-1.5 h-1.5 rounded-full ${
                  selectedStep.status === 'done' ? 'bg-[#98e94d]' :
                  selectedStep.status === 'active' ? 'bg-blue-400' :
                  selectedStep.status === 'blocked' || selectedStep.status === 'error' ? 'bg-red-400' : 'bg-[#444444]'
                }`} />
                {selectedStep.status}
              </span>
              {selectedStep.timestamp && (
                <p className="text-[10px] font-mono" style={{ color: "var(--c-text-5)" }}>
                  Processed at {new Date(selectedStep.timestamp).toLocaleTimeString()}
                </p>
              )}
            </div>
          </div>

          <div className="space-y-3">
            <p className="text-xs leading-relaxed" style={{ color: "var(--c-text-2)" }}>
              {selectedStep.message || (
                selectedStep.status === 'pending'
                  ? 'This stage is waiting for alert triggers to activate.'
                  : 'Processed successfully with no additional messages.'
              )}
            </p>

            {selectedStep.order_id && (
              <div className="inline-flex items-center gap-2 rounded-lg px-3 py-1.5" style={{ background: 'var(--c-page)', border: '1px solid var(--c-border-3)' }}>
                <span className="text-[10px] font-mono" style={{ color: 'var(--c-text-4)' }}>Dhan Order Reference:</span>
                <span className="text-[11px] font-mono font-bold select-all" style={{ color: 'var(--c-lime-text)' }}>{selectedStep.order_id}</span>
              </div>
            )}

            {selectedStep.reason && (
              <div className="rounded-lg bg-red-950/10 border border-red-900/25 px-3.5 py-2.5 text-xs text-red-400 leading-normal">
                <strong className="block font-bold mb-0.5 uppercase tracking-wide text-[10px] text-red-300">Block Details:</strong>
                {selectedStep.reason}
              </div>
            )}
          </div>
        </div>
      )}

    </div>
  )
}

// ─── Metric card ─────────────────────────────────────────────────────────────

function MetricCard({ label, value, accent }: { label: string; value: string; accent?: 'green' | 'red' | 'amber' }) {
  const valueColor = accent === 'green' ? 'var(--c-lime-text)' : accent === 'red' ? '#f87171' : accent === 'amber' ? '#fbbf24' : 'var(--c-text-1)'
  return (
    <div className="card py-3 px-4">
      <p className="text-xs uppercase tracking-wide mb-1" style={{ color: 'var(--c-text-4)' }}>{label}</p>
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
const notifyNewOrder = (order: OrderEvent) => {
  const action = (order.normalized_action ?? order.action ?? 'ORDER').toUpperCase()
  const symbol = order.trading_symbol ?? order.normalized_symbol ?? 'Symbol'
  const qty = order.normalized_qty ?? order.qty ?? 0
  const status = (order.status ?? order.phase ?? '').toUpperCase()
  
  const isSuccess = status.includes('TRADED') || status.includes('FILLED') || order.success
  const isBlocked = order.blocked || status.includes('BLOCK') || status.includes('REJECT') || status.includes('FAIL')

  let title = 'Order Placed'
  if (isBlocked) title = 'Order Blocked'
  else if (isSuccess) title = 'Order Executed'

  const message = `${action} ${qty} qty of ${symbol}`

  if (isBlocked) {
    toast.error(title, { description: message + (order.reason ? ` - ${order.reason}` : ''), duration: 5000 })
  } else if (isSuccess) {
    toast.success(title, { description: message, duration: 4000 })
  } else {
    toast(title, { description: message, duration: 4000 })
  }
}

const orderEventKey = (order: OrderEvent) => [
  order.created_at, order.phase, order.signal_id, order.order_id, order.status, order.reason,
].map(value => String(value ?? '')).join('|')

export default function DashboardPage() {
  const [summary, setSummary]   = useState<DashboardSummary | null>(null)
  const [setup,   setSetup]     = useState<SetupStatus | null>(null)
  const [flow,    setFlow]      = useState<LiveFlowStep[]>([])
  const [orders,  setOrders]    = useState<OrderEvent[]>([])
  const [confirmModal, setConfirmModal] = useState<{
    isOpen: boolean; title: string; message: string;
    variant: 'danger' | 'warning' | 'primary'; onConfirm: () => void;
  }>({ isOpen: false, title: '', message: '', variant: 'primary', onConfirm: () => {} })

  const seenOrderKeysRef = useRef<Set<string>>(new Set())
  const isFirstLoadRef = useRef(true)

  const animBalance  = useAnimatedNumber(summary?.wallet?.available_balance ?? null)
  const animUtilised = useAnimatedNumber(summary?.wallet?.utilized_amount ?? null)

  const loadData = async () => {
    const [s, st, f, o] = await Promise.all([
      getDashboardSummary(), getSetupStatus(), getLiveFlow(), getOrders(),
    ])
    const freshOrders = o.data || []
    if (isFirstLoadRef.current) {
      seenOrderKeysRef.current = new Set(freshOrders.map(orderEventKey))
      isFirstLoadRef.current = false
    } else {
      const newOrdersToToast: OrderEvent[] = []
      for (const ord of freshOrders) {
        const key = orderEventKey(ord)
        if (!seenOrderKeysRef.current.has(key)) {
          newOrdersToToast.push(ord)
          seenOrderKeysRef.current.add(key)
        }
      }
      for (let i = newOrdersToToast.length - 1; i >= 0; i--) notifyNewOrder(newOrdersToToast[i])
    }
    setSummary(s.data); setSetup(st.data); setFlow(f.data)
    setOrders(freshOrders.slice(0, 10))
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
    setConfirmModal({
      isOpen: true, title: 'Activate Emergency Stop',
      message: 'This will immediately block all incoming trading alerts. Are you sure you want to activate emergency stop?',
      variant: 'danger',
      onConfirm: async () => {
        setConfirmModal(prev => ({ ...prev, isOpen: false }))
        try { await emergencyStop(); toast.error('Emergency stop activated'); loadData() }
        catch { toast.error('Failed to activate emergency stop') }
      },
    })
  }

  const runStartEngine = async () => {
    const confirmLive = Boolean(setup?.mode.dhan_mode === 'REAL' && setup.mode.live_orders_enabled)
    if (confirmLive) {
      setConfirmModal({
        isOpen: true, title: 'Start Engine (LIVE MODE)',
        message: 'LIVE ORDERS ARE ENABLED — real money orders will be placed on Dhan. Are you sure you want to start the signal routing engine?',
        variant: 'danger',
        onConfirm: () => { setConfirmModal(prev => ({ ...prev, isOpen: false })); executeStartEngine(true) },
      })
    } else {
      executeStartEngine(false)
    }
  }

  const executeStartEngine = async (live: boolean) => {
    try { await startEngine(live); toast.success('Engine started'); loadData() }
    catch { toast.error('Failed to start engine') }
  }

  const runStopEngine = async () => {
    try { await stopEngine(); toast.warning('Engine stopped'); loadData() }
    catch { toast.error('Failed to stop engine') }
  }

  if (!summary || !setup) return (
    <div className="flex min-h-[60vh] items-center justify-center gap-3" style={{ color: 'var(--c-text-4)' }}>
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
          <h1 className="text-2xl font-semibold" style={{ color: 'var(--c-text-1)' }}>Dashboard</h1>
          <p className="mt-0.5 text-sm" style={{ color: 'var(--c-text-3)' }}>Live system state and recent orders.</p>
        </div>
        <button onClick={loadData} className="btn-ghost flex items-center gap-2 text-sm py-1.5">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      <CanTradeBanner summary={summary} setup={setup} />

      <section className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MetricCard label="Engine"    value={setup.engine_started  ? 'Running'   : 'Stopped'}  accent={setup.engine_started  ? 'green' : 'amber'} />
        <MetricCard label="Dhan"      value={setup.dhan_connected  ? 'Connected' : 'Offline'}  accent={setup.dhan_connected  ? 'green' : 'red'}   />
        <MetricCard label="Available" value={`₹${Math.round(animBalance).toLocaleString('en-IN')}`} />
        <MetricCard label="Utilised"  value={`₹${Math.round(animUtilised).toLocaleString('en-IN')}`} accent={animUtilised > 0 ? 'amber' : undefined} />
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="card space-y-3 lg:col-span-2">
          <h2 className="text-xs font-semibold uppercase tracking-widest" style={{ color: 'var(--c-text-4)' }}>Alert lifecycle</h2>
          <PipelineStrip steps={flow} />
        </div>
        <div className="card space-y-3">
          <h2 className="text-xs font-semibold uppercase tracking-widest" style={{ color: 'var(--c-text-4)' }}>Quick controls</h2>
          <button onClick={runStartEngine} disabled={setup.engine_started} className="btn-primary flex w-full items-center justify-center gap-2 text-sm disabled:cursor-not-allowed disabled:opacity-45"><Play size={14} /> Start Engine</button>
          <button onClick={runStopEngine} disabled={!setup.engine_started} className="btn-ghost  flex w-full items-center justify-center gap-2 text-sm disabled:cursor-not-allowed disabled:opacity-45"><Square size={14} /> Stop Engine</button>
          <button onClick={runEmergencyStop} className="btn-danger flex w-full items-center justify-center gap-2 text-sm"><ShieldAlert size={14} /> Emergency Stop</button>
          <p className="text-xs" style={{ color: 'var(--c-text-4)' }}>Full controls at <a href="/app/controls" className="underline underline-offset-2" style={{ color: 'var(--c-text-3)' }}>/controls</a>.</p>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card space-y-3">
          <h2 className="text-xs font-semibold uppercase tracking-widest" style={{ color: 'var(--c-text-4)' }}>Open position</h2>
          {openPosition.has_open_position ? (
            <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
              {([
                ['Symbol', openPosition.trading_symbol, false],
                ['Security ID', openPosition.security_id, true],
                ['Qty', openPosition.qty, false],
                ['Entry order', openPosition.entry_order_id, true],
                ['Option LTP', openPosition.live_pnl?.ltp != null ? currency(openPosition.live_pnl.ltp) : '-', false],
                ['PnL', openPosition.live_pnl?.unrealized_pnl != null ? currency(openPosition.live_pnl.unrealized_pnl) : '-', false],
                ['SL / TP', `${openPosition.live_pnl?.sl_price != null ? currency(openPosition.live_pnl.sl_price) : '-'} / ${openPosition.live_pnl?.tp_price != null ? currency(openPosition.live_pnl.tp_price) : '-'}`, false],
                ['LTP source', openPosition.live_pnl?.source ?? '-', false],
                ['Monitor', openPosition.live_pnl?.status ?? '-', false],
                ['Entry price', openPosition.entry_price != null ? currency(openPosition.entry_price) : '-', false],
                ['Opened', openPosition.opened_at ? new Date(openPosition.opened_at).toLocaleTimeString() : '-', false]
              ] as [string, unknown, boolean][]).map(([l, v, isMono]) => (
                <div key={String(l)}>
                  <p className="text-xs" style={{ color: 'var(--c-text-4)' }}>{String(l)}</p>
                  <p className={`font-medium ${isMono ? 'font-mono text-xs break-all select-all' : 'truncate'}`} style={{ color: isMono ? 'var(--c-text-3)' : 'var(--c-text-1)' }}>
                    {String(v ?? '-')}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <div className="space-y-1">
              <p className="text-sm" style={{ color: 'var(--c-text-4)' }}>No open position tracked locally.</p>
              {openPosition.broker_sync?.message && (
                <p className="text-xs" style={{ color: 'var(--c-lime-text)' }}>{openPosition.broker_sync.message}</p>
              )}
            </div>
          )}
        </div>

        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-widest" style={{ color: 'var(--c-text-4)' }}>Wallet</h2>
            <Wallet size={14} style={{ color: 'var(--c-text-4)' }} />
          </div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
            {([['Available', currency(summary.wallet.available_balance)], ['Utilised', currency(summary.wallet.utilized_amount)], ['Withdrawable', currency(summary.wallet.withdrawable_balance)], ['Mode', setup.mode.dhan_mode]] as [string, string][]).map(([l, v]) => (
              <div key={l}>
                <p className="text-xs" style={{ color: 'var(--c-text-4)' }}>{l}</p>
                <p className="font-medium" style={{ color: l === 'Mode' && v === 'REAL' ? '#f87171' : 'var(--c-text-1)' }}>{v}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="card space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-widest" style={{ color: 'var(--c-text-4)' }}>TradingView webhook</h2>
        <div className="flex items-center gap-2 rounded-lg px-3 py-2" style={{ border: '1px solid var(--c-border-2)', background: 'var(--c-page)' }}>
          <p className="flex-1 min-w-0 truncate font-mono text-xs" style={{ color: 'var(--c-text-3)' }}>{setup.webhook_url || '—'}</p>
          <button onClick={copyWebhook} className="btn-ghost p-1.5" style={{ color: 'var(--c-text-3)' }} title="Copy"><Copy size={13} /></button>
        </div>
        {latestAlert && (
          <p className="text-xs" style={{ color: 'var(--c-text-4)' }}>
            Last alert: <span style={{ color: 'var(--c-lime-text)' }}>{String(latestAlert.event_type ?? '—')}</span>
            {' · '}<span className="font-mono" style={{ color: 'var(--c-text-3)' }}>{String(latestAlert.signal_id ?? '—')}</span>
          </p>
        )}
      </section>

      <section className="card">
        <h2 className="mb-4 text-xs font-semibold uppercase tracking-widest" style={{ color: 'var(--c-text-4)' }}>Recent orders</h2>

        <div className="block sm:hidden space-y-3">
          {orders.length === 0 ? (
            <p className="py-6 text-center text-sm" style={{ color: 'var(--c-text-4)' }}>No order events yet.</p>
          ) : orders.map(order => (
            <div key={order.id} className="rounded-xl p-3.5 space-y-2.5 transition-colors"
              style={{ border: '1px solid var(--c-border-1)', background: 'var(--c-card)' }}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <ActionBadge order={order} />
                  <span className="text-[10px] font-mono" style={{ color: 'var(--c-text-4)' }}>
                    {order.created_at ? new Date(order.created_at).toLocaleTimeString() : '—'}
                  </span>
                </div>
                <OrderStatusBadge order={order} />
              </div>
              <div className="flex items-center justify-between gap-4">
                <span className="font-mono text-xs font-bold break-words" style={{ color: 'var(--c-text-1)' }}>
                  {order.trading_symbol ?? order.normalized_symbol ?? '—'}
                </span>
                <span className="text-xs font-semibold whitespace-nowrap" style={{ color: 'var(--c-text-2)' }}>
                  Qty: {order.normalized_qty ?? order.qty ?? '—'}
                </span>
              </div>
              {order.reason && (
                <div className="rounded-lg p-2.5 text-[11px] leading-relaxed"
                  style={{ background: 'rgba(239,68,68,0.04)', border: '1px solid rgba(239,68,68,0.15)', color: '#f87171' }}>
                  <span className="font-bold uppercase tracking-wider text-[9px] mr-1 block">Block Reason:</span>
                  {order.reason}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="hidden sm:block overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead style={{ borderBottom: '1px solid var(--c-border-2)' }}>
              <tr className="text-xs uppercase tracking-wide" style={{ color: 'var(--c-text-4)' }}>
                {['Time', 'Action', 'Symbol', 'Qty', 'Status', 'Reason'].map(h => (
                  <th key={h} className="pb-2 pr-4 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {orders.length === 0 ? (
                <tr><td colSpan={6} className="py-6 text-center text-sm" style={{ color: 'var(--c-text-4)' }}>No order events yet.</td></tr>
              ) : orders.map(order => (
                <tr key={order.id} className="transition-colors" style={{ borderBottom: '1px solid var(--c-border-1)' }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--c-raised)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                  <td className="py-2.5 pr-4 text-xs whitespace-nowrap" style={{ color: 'var(--c-text-4)' }}>{order.created_at ? new Date(order.created_at).toLocaleTimeString() : '—'}</td>
                  <td className="py-2.5 pr-4"><ActionBadge order={order} /></td>
                  <td className="py-2.5 pr-4 font-mono text-xs font-medium" style={{ color: 'var(--c-text-1)' }}>{order.trading_symbol ?? order.normalized_symbol ?? '—'}</td>
                  <td className="py-2.5 pr-4" style={{ color: 'var(--c-text-2)' }}>{order.normalized_qty ?? order.qty ?? '—'}</td>
                  <td className="py-2.5 pr-4"><OrderStatusBadge order={order} /></td>
                  <td className="py-2.5 text-xs max-w-xs break-words whitespace-normal" style={{ color: 'var(--c-text-4)' }}>{order.reason ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <ConfirmModal
        isOpen={confirmModal.isOpen}
        title={confirmModal.title}
        message={confirmModal.message}
        variant={confirmModal.variant}
        onConfirm={confirmModal.onConfirm}
        onCancel={() => setConfirmModal(prev => ({ ...prev, isOpen: false }))}
      />
    </div>
  )
}
