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
import ConfirmModal from '../components/ConfirmModal'

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
  const [confirmModal, setConfirmModal] = useState<{
    isOpen: boolean
    title: string
    message: string
    variant: 'danger' | 'warning' | 'primary'
    onConfirm: () => void
  }>({
    isOpen: false,
    title: '',
    message: '',
    variant: 'primary',
    onConfirm: () => {},
  })

  const ok = (msg: string) => { setMessage(msg); setError(''); window.setTimeout(() => setMessage(''), 4000) }
  const err = (msg: string) => { setError(msg); setMessage('') }

  const triggerConfirm = (
    title: string,
    message: string,
    action: () => Promise<unknown>,
    successMsg: string,
    variant: 'danger' | 'warning' | 'primary'
  ) => {
    setConfirmModal({
      isOpen: true,
      title,
      message,
      variant,
      onConfirm: async () => {
        setConfirmModal((prev) => ({ ...prev, isOpen: false }))
        try {
          await action()
          ok(successMsg)
        } catch {
          err('Request failed — check backend logs.')
        }
      },
    })
  }

  return (
    <div className="space-y-8 w-full">
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
            onClick={() => triggerConfirm('Activate Emergency Stop', 'No new entries will be routed. Are you sure you want to stop trading?', emergencyStop, 'Emergency stop activated.', 'danger')} />
          <CtrlBtn icon={ShieldCheck} label="Resume trading" description="Clear emergency stop and kill switch. Re-enables normal flow." variant="primary"
            onClick={() => triggerConfirm('Resume Trading', 'Resume trading? Risk checks will be re-enabled.', resumeTrading, 'Trading resumed.', 'primary')} />
          <CtrlBtn icon={Zap}
            label="Toggle kill switch"
            description={status.killSwitch ? 'Kill switch ON — click to turn OFF.' : 'Kill switch OFF — click to turn ON.'}
            variant={status.killSwitch ? 'danger' : 'warning'}
            onClick={() => triggerConfirm(
              status.killSwitch ? 'Disable Kill Switch' : 'Enable Kill Switch',
              status.killSwitch ? 'Turn OFF the global kill switch?' : 'Turn ON the global kill switch? All trading will pause.',
              () => setGlobalKillSwitch(!status.killSwitch),
              `Kill switch ${status.killSwitch ? 'disabled' : 'enabled'}.`,
              status.killSwitch ? 'primary' : 'danger'
            )} />
          <CtrlBtn icon={Square} label="Stop engine" description="Stop the signal routing engine. No new alerts will be processed." variant="warning"
            onClick={() => triggerConfirm('Stop Engine', 'Stop the engine? No new alerts will be processed.', stopEngine, 'Engine stopped.', 'warning')} />
        </div>
      </section>

      <section className="space-y-3">
        <SectionLabel>Entry flow</SectionLabel>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <CtrlBtn icon={Square} label="Pause entries" description="Block new entry orders without clearing position state." variant="warning"
            onClick={() => triggerConfirm('Pause Entries', 'Pause new entries?', pauseEntries, 'Entries paused.', 'warning')} />
          <CtrlBtn icon={RefreshCw} label="Resume entries" description="Re-enable entry routing after a pause." variant="primary"
            onClick={() => triggerConfirm('Resume Entries', 'Resume entry routing?', resumeEntries, 'Entries resumed.', 'primary')} />
        </div>
      </section>

      <section className="space-y-3">
        <SectionLabel>State management</SectionLabel>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <CtrlBtn icon={RefreshCw} label="Clear seen signals" description="Reset duplicate-detection cache. Allows re-processing of old signal IDs." variant="ghost"
            onClick={() => triggerConfirm('Clear Seen Signals', 'Clear seen signals cache? Old signal IDs can re-trigger.', clearSeenSignals, 'Seen signals cleared.', 'primary')} />
          <CtrlBtn icon={Trash2} label="Reset runtime state" description="Clear open position and app state. Does not affect broker orders." variant="warning"
            onClick={() => triggerConfirm('Reset Runtime State', 'Reset runtime state? Local position tracking will be cleared.', resetRuntimeState, 'Runtime state reset.', 'warning')} />
          <CtrlBtn icon={AlertTriangle} label="Fresh start" description="Full reset: clears position, signals, state. Use after a manual close in Dhan." variant="danger"
            onClick={() => triggerConfirm('Fresh Start', 'Full fresh start? This clears all local state including position tracking.', freshStart, 'Fresh start complete.', 'danger')} />
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

      <ConfirmModal
        isOpen={confirmModal.isOpen}
        title={confirmModal.title}
        message={confirmModal.message}
        variant={confirmModal.variant}
        onConfirm={confirmModal.onConfirm}
        onCancel={() => setConfirmModal((prev) => ({ ...prev, isOpen: false }))}
      />
    </div>
  )
}
