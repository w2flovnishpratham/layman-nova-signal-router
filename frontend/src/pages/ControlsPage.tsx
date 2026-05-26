import { useState } from 'react'
import {
  AlertTriangle,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Square,
  Trash2,
  Zap,
} from 'lucide-react'
import {
  clearSeenSignals,
  emergencyStop,
  freshStart,
  pauseEntries,
  resetRuntimeState,
  resumeEntries,
  resumeTrading,
  setGlobalKillSwitch,
  stopEngine,
} from '../api/dashboard'
import { useSystemStatus } from '../hooks/useSystemStatus'

// ─── Confirm + run helper ────────────────────────────────────────────────────

async function confirmRun(
  message: string,
  action: () => Promise<unknown>,
  onDone: (msg: string) => void,
  onError: (msg: string) => void,
  successMsg: string,
) {
  if (!window.confirm(message)) return
  try {
    await action()
    onDone(successMsg)
  } catch {
    onError('Request failed — check backend logs.')
  }
}

// ─── Control button ──────────────────────────────────────────────────────────

interface CtrlBtnProps {
  icon: React.ElementType
  label: string
  description: string
  onClick: () => void
  variant: 'danger' | 'warning' | 'ghost' | 'primary'
  disabled?: boolean
}

const VARIANT_STYLES: Record<CtrlBtnProps['variant'], string> = {
  danger:  'border-[rgba(239,68,68,0.25)]  bg-[rgba(239,68,68,0.06)]  hover:bg-[rgba(239,68,68,0.12)]  text-red-300',
  warning: 'border-[rgba(245,158,11,0.25)] bg-[rgba(245,158,11,0.06)] hover:bg-[rgba(245,158,11,0.12)] text-amber-300',
  ghost:   'border-[#2b2a26] bg-[#151513] hover:bg-[#1b1a17] text-[#d0d0d0]',
  primary: 'border-[rgba(152,233,77,0.2)]  bg-[rgba(152,233,77,0.06)]  hover:bg-[rgba(152,233,77,0.12)] text-[#98e94d]',
}

function CtrlBtn({ icon: Icon, label, description, onClick, variant, disabled }: CtrlBtnProps) {
  return (
    <button
      className={`flex items-start gap-3 w-full text-left rounded-xl border p-4 transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${VARIANT_STYLES[variant]}`}
      onClick={onClick}
      disabled={disabled}
    >
      <Icon size={17} className="mt-0.5 flex-shrink-0" aria-hidden />
      <div>
        <p className="text-sm font-semibold">{label}</p>
        <p className="text-xs opacity-60 mt-0.5">{description}</p>
      </div>
    </button>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-xs font-semibold uppercase tracking-widest" style={{ color: '#77736c' }}>
      {children}
    </h2>
  )
}

// ─── Page ────────────────────────────────────────────────────────────────────

export default function ControlsPage() {
  const status = useSystemStatus()
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const ok = (msg: string) => { setMessage(msg); setError(''); window.setTimeout(() => setMessage(''), 4000) }
  const err = (msg: string) => { setError(msg); setMessage('') }

  return (
    <div className="space-y-8 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold" style={{ color: '#f4f1ea' }}>Controls</h1>
        <p className="mt-1 text-sm" style={{ color: '#9a968f' }}>
          Emergency actions, trading flow controls, and state management.
        </p>
      </div>

      {(status.emergencyStop || status.killSwitch) && (
        <div className="rounded-xl p-4" style={{ border: '1px solid rgba(239,68,68,0.3)', background: 'rgba(239,68,68,0.07)' }}>
          <div className="flex items-center gap-2 text-sm font-semibold text-red-300">
            <ShieldAlert size={16} />
            {status.emergencyStop ? 'Emergency stop is active — new entries are blocked.' : 'Global kill switch is ON — all trading is paused.'}
          </div>
          <p className="text-xs mt-1" style={{ color: 'rgba(239,68,68,0.6)' }}>Use Resume Trading below to re-enable.</p>
        </div>
      )}

      {message && (
        <div className="rounded-xl border p-3 text-sm" style={{ borderColor: 'rgba(152,233,77,0.2)', background: 'rgba(152,233,77,0.06)', color: '#98e94d' }}>
          {message}
        </div>
      )}
      {error && (
        <div className="rounded-xl border p-3 text-sm" style={{ borderColor: 'rgba(239,68,68,0.25)', background: 'rgba(239,68,68,0.07)', color: '#f87171' }}>
          {error}
        </div>
      )}

      <section className="space-y-3">
        <SectionLabel>Emergency</SectionLabel>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <CtrlBtn icon={ShieldAlert} label="Emergency stop" description="Immediately block all new entry risk checks." variant="danger"
            onClick={() => confirmRun('Activate emergency stop? No new entries will be routed.', emergencyStop, ok, err, 'Emergency stop activated.')} />
          <CtrlBtn icon={ShieldCheck} label="Resume trading" description="Clear emergency stop and kill switch. Re-enables normal flow." variant="primary"
            onClick={() => confirmRun('Resume trading? Risk checks will be re-enabled.', resumeTrading, ok, err, 'Trading resumed.')} />
          <CtrlBtn icon={Zap}
            label="Toggle kill switch"
            description={status.killSwitch ? 'Kill switch ON — click to turn OFF.' : 'Kill switch OFF — click to turn ON.'}
            variant={status.killSwitch ? 'danger' : 'warning'}
            onClick={() => confirmRun(
              status.killSwitch ? 'Turn OFF the global kill switch?' : 'Turn ON the global kill switch? All trading will pause.',
              () => setGlobalKillSwitch(!status.killSwitch), ok, err,
              `Kill switch ${status.killSwitch ? 'disabled' : 'enabled'}.`,
            )} />
          <CtrlBtn icon={Square} label="Stop engine" description="Stop the signal routing engine. No new alerts will be processed." variant="warning"
            onClick={() => confirmRun('Stop the engine?', stopEngine, ok, err, 'Engine stopped.')} />
        </div>
      </section>

      <section className="space-y-3">
        <SectionLabel>Entry flow</SectionLabel>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <CtrlBtn icon={Square} label="Pause entries" description="Block new entry orders without clearing position state." variant="warning"
            onClick={() => confirmRun('Pause new entries?', pauseEntries, ok, err, 'Entries paused.')} />
          <CtrlBtn icon={RefreshCw} label="Resume entries" description="Re-enable entry routing after a pause." variant="primary"
            onClick={() => confirmRun('Resume entries?', resumeEntries, ok, err, 'Entries resumed.')} />
        </div>
      </section>

      <section className="space-y-3">
        <SectionLabel>State management</SectionLabel>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <CtrlBtn icon={RefreshCw} label="Clear seen signals" description="Reset duplicate-detection cache. Allows re-processing of old signal IDs." variant="ghost"
            onClick={() => confirmRun('Clear seen signals cache? Old signal IDs can re-trigger.', clearSeenSignals, ok, err, 'Seen signals cleared.')} />
          <CtrlBtn icon={Trash2} label="Reset runtime state" description="Clear open position and app state. Does not affect broker orders." variant="warning"
            onClick={() => confirmRun('Reset runtime state? Local position tracking will be cleared.', resetRuntimeState, ok, err, 'Runtime state reset.')} />
          <CtrlBtn icon={AlertTriangle} label="Fresh start" description="Full reset: clears position, signals, state. Use after a manual close in Dhan." variant="danger"
            onClick={() => confirmRun('Full fresh start? This clears all local state including position tracking.', freshStart, ok, err, 'Fresh start complete.')} />
        </div>
      </section>

      {/* Status summary */}
      <section className="rounded-xl p-5 space-y-3" style={{ background: '#151513', border: '1px solid #222222' }}>
        <h2 className="text-sm font-semibold" style={{ color: '#d8d3c8' }}>Current system state</h2>
        <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
          {([
            ['Engine',         status.engineRunning  ? 'Running'   : 'Stopped',  status.engineRunning],
            ['Dhan',           status.dhanConnected  ? 'Connected' : 'Offline',  status.dhanConnected],
            ['Emergency stop', status.emergencyStop  ? 'ACTIVE'    : 'Off',     !status.emergencyStop],
            ['Kill switch',    status.killSwitch     ? 'ON'        : 'Off',     !status.killSwitch],
            ['Live orders',    status.liveOrders     ? 'ENABLED'   : 'Disabled', !status.liveOrders],
            ['Mode',           status.dhanMode,                                  status.dhanMode !== 'REAL'],
          ] as [string, string, boolean][]).map(([label, value, isOk]) => (
            <div key={label} className="flex justify-between pb-1" style={{ borderBottom: '1px solid #222222' }}>
              <span style={{ color: '#77736c' }}>{label}</span>
              <span style={{ color: isOk ? '#98e94d' : '#ef4444' }}>{value}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
