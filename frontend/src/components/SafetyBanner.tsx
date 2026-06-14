import { AlertOctagon, FlaskConical, ShieldCheck, Zap } from 'lucide-react'
import type { EngineMode, SafetyStatus, WsStatus } from '../types'

interface Props {
  engineMode: EngineMode | null
  safety: SafetyStatus | null
  wsStatus: WsStatus
  error?: string
}

export function SafetyBanner({ engineMode, safety, wsStatus, error }: Props) {
  const disconnected = wsStatus !== 'live' || !safety
  const mode = disconnected ? null : (engineMode ?? safety.mode)
  const tone = disconnected ? 'unknown' : mode ?? 'unknown'
  const headline = safetyHeadline(mode, disconnected, safety)

  return (
    <section className={`safety-banner safety-banner-${tone}`} aria-live="assertive">
      <div className="safety-banner-primary">
        <span className="safety-mode-mark" aria-hidden="true">
          {tone === 'paper' ? <FlaskConical size={18} /> : tone === 'live' ? <Zap size={18} /> : <AlertOctagon size={18} />}
        </span>
        <div>
          <strong>{disconnected ? 'UNKNOWN / DISCONNECTED' : `${mode?.toUpperCase() ?? 'UNKNOWN'} MODE`}</strong>
          <span>{error || headline}</span>
        </div>
      </div>
      <div className="safety-banner-meta" aria-label="Backend safety status">
        <StatusChip label="Live orders" value={safety?.live_orders_enabled ? 'enabled' : 'disabled'} ok={!safety?.live_orders_enabled} />
        <StatusChip label="Workers" value={safety?.trading_workers_enabled ? 'enabled' : 'disabled'} ok={!safety?.trading_workers_enabled} />
        <StatusChip label="Role" value={safety?.worker_role ?? 'unknown'} ok={safety?.worker_role === 'web'} />
        <StatusChip label="Webhook HMAC" value={safety?.webhook_hmac_required ? 'required' : 'off'} ok={Boolean(safety?.webhook_hmac_required)} />
        <StatusChip label="Replay guard" value={safety?.webhook_replay_protection ? 'active' : 'missing'} ok={Boolean(safety?.webhook_replay_protection)} />
        <StatusChip label="Executor egress" value={safety?.executor_egress_verified ? 'verified' : 'not verified'} ok={Boolean(safety?.executor_egress_verified)} />
      </div>
    </section>
  )
}

function StatusChip({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return (
    <span className={`safety-status-chip ${ok ? 'ok' : 'blocked'}`}>
      <ShieldCheck size={12} />
      <span>{label}</span>
      <strong>{value}</strong>
    </span>
  )
}

function safetyHeadline(mode: EngineMode | null, disconnected: boolean, safety: SafetyStatus | null): string {
  if (disconnected) return 'Backend disconnected. Do not trust live status until reconnect.'
  if (mode === 'paper') return 'Paper mode - no real orders'
  if (!safety?.live_orders_enabled) return 'Live trading is disabled by system policy'
  if (mode === 'live') return 'LIVE MODE - real broker orders can be placed'
  return 'Select a mode before trusting trading controls.'
}
