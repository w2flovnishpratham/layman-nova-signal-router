import { CheckCircle2, Copy, Eye, EyeOff, ShieldX, XCircle } from 'lucide-react'
import { useState } from 'react'
import type { AuthStatus, SafetyStatus, SessionBootstrap, TradeConfig } from '../types'

interface Props {
  auth: AuthStatus
  safety: SafetyStatus | null
  session: SessionBootstrap | null
  config: TradeConfig
}

export function SafetyReadiness({ auth, safety, session, config }: Props) {
  const paperChecks = [
    check('Signed in', !auth.authRequired || auth.authenticated),
    check('Dhan market-data account connected', Boolean(safety?.broker.connected)),
    check('Strategy selected', Boolean(config.strategy)),
    check('Risk limits saved', Boolean(config.risk)),
    check('Webhook secret configured', Boolean(safety?.webhook.secret_set)),
    check('Real order placement disabled', safety ? !safety.live_orders_enabled : false),
  ]
  const liveChecks = [
    check('Signed in', !auth.authRequired || auth.authenticated),
    check('Broker connected and token saved', Boolean(safety?.broker.connected && safety.broker.access_token_present)),
    check('Risk limits saved', Boolean(config.risk)),
    check('Webhook HMAC required', Boolean(safety?.webhook_hmac_required)),
    check('Timestamp replay protection active', Boolean(safety?.webhook_replay_protection)),
    check('Signing relay configured', Boolean(safety?.signing_relay_configured)),
    check('Executor and unique egress verified', Boolean(safety?.executor_egress_verified)),
    check('Live orders enabled by operator', Boolean(safety?.live_orders_enabled)),
    check('Authenticated trading workers ready', Boolean(safety?.authenticated_live_workers_ready)),
    check('Market-hours safety valid', Boolean(safety?.market_hours_valid)),
  ]

  return (
    <section className="safety-overview" aria-label="Launch readiness">
      <div className="safety-overview-heading">
        <div>
          <span>Operator safety</span>
          <h2>Launch Readiness</h2>
        </div>
        <strong className={safety?.single_operator_live_allowed ? 'ready' : 'blocked'}>
          {safety?.single_operator_live_allowed ? 'Live prerequisites satisfied' : 'Live launch blocked'}
        </strong>
      </div>

      <div className="readiness-grid">
        <ReadinessList title="Paper beta" checks={paperChecks} />
        <ReadinessList title="Live trading" checks={liveChecks} />
      </div>

      {safety?.reasons_live_blocked.length ? (
        <div className="live-blockers">
          <ShieldX size={17} />
          <div>
            <strong>Why Live is unavailable</strong>
            <ul>
              {safety.reasons_live_blocked.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
          </div>
        </div>
      ) : null}

      <div className="trust-status-grid">
        <BrokerTrust safety={safety} />
        <WebhookTrust safety={safety} session={session} />
      </div>
    </section>
  )
}

function ReadinessList({ title, checks }: { title: string; checks: CheckItem[] }) {
  const passed = checks.filter((item) => item.ready).length
  return (
    <section className="readiness-list">
      <div><strong>{title}</strong><span>{passed}/{checks.length} ready</span></div>
      {checks.map((item) => (
        <p key={item.label} className={item.ready ? 'ready' : 'blocked'}>
          {item.ready ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
          {item.label}
        </p>
      ))}
    </section>
  )
}

function BrokerTrust({ safety }: { safety: SafetyStatus | null }) {
  const broker = safety?.broker
  return (
    <section className="trust-status">
      <div><span>Broker trust</span><strong>{broker?.connected ? 'Dhan connected' : 'Not connected'}</strong></div>
      <TrustRow label="Client ID" value={broker?.client_id_masked ?? 'Not saved'} />
      <TrustRow label="Access token" value={broker?.access_token_present ? 'Saved securely - value hidden' : 'Not saved'} />
      <TrustRow label="Token age" value={formatTokenAge(broker?.token_age_minutes)} warning={Boolean(broker?.token_warn)} />
      <TrustRow label="Last verified" value={formatDate(broker?.last_validated_at)} />
      {broker?.token_expired ? <p className="trust-warning">The saved Dhan token may be expired. Reconnect before relying on account data.</p> : null}
      {broker?.connected && !safety?.live_orders_enabled ? <p className="trust-note">Broker connected, but live order placement remains disabled.</p> : null}
    </section>
  )
}

function WebhookTrust({ safety, session }: { safety: SafetyStatus | null; session: SessionBootstrap | null }) {
  const [secretVisible, setSecretVisible] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const oneTimeSecret = session?.webhookSecretAvailableOnce ? session.webhookSecret : null

  async function copy(label: string, value: string) {
    await navigator.clipboard?.writeText(value)
    setCopied(label)
    window.setTimeout(() => setCopied(null), 1200)
  }

  return (
    <section className="trust-status">
      <div><span>Webhook trust</span><strong>{safety?.webhook.secret_set ? 'Secret configured' : 'Not configured'}</strong></div>
      <TrustRow label="HMAC" value={safety?.webhook_hmac_required ? 'Required' : 'Not required'} warning={!safety?.webhook_hmac_required} />
      <TrustRow label="Replay guard" value={safety?.webhook_replay_protection ? 'Timestamp + signal ID active' : 'Incomplete'} warning={!safety?.webhook_replay_protection} />
      <TrustRow label="Signing relay" value={safety?.signing_relay_configured ? 'Configured' : 'Not configured'} warning={!safety?.signing_relay_configured} />
      <TrustRow label="Legacy unsigned mode" value={safety?.legacy_unsigned_webhooks_disabled ? 'Disabled' : 'Enabled'} warning={!safety?.legacy_unsigned_webhooks_disabled} />
      <TrustRow label="Last webhook" value={formatDate(safety?.webhook.last_received_at, 'Never received')} />
      {safety?.webhook.last_rejection_category ? <TrustRow label="Last rejection" value={safety.webhook.last_rejection_category} warning /> : null}
      <CopyValue
        label="Webhook URL"
        value={safety?.webhook.url ?? session?.webhookUrl ?? ''}
        copied={copied === 'url'}
        onCopy={() => void copy('url', safety?.webhook.url ?? session?.webhookUrl ?? '')}
      />
      <TrustRow label="Secret" value={safety?.webhook.secret_masked ?? 'Not configured'} />
      {oneTimeSecret ? (
        <div className="one-time-secret">
          <strong>One-time secret display</strong>
          <p>This value will not be shown again after this session.</p>
          <code>{secretVisible ? oneTimeSecret : '••••••••••••••••••••••••'}</code>
          <div>
            <button type="button" onClick={() => setSecretVisible((current) => !current)}>
              {secretVisible ? <EyeOff size={13} /> : <Eye size={13} />}
              {secretVisible ? 'Hide' : 'Reveal'}
            </button>
            <button type="button" onClick={() => void copy('secret', oneTimeSecret)}>
              <Copy size={13} />
              {copied === 'secret' ? 'Copied' : 'Copy once'}
            </button>
          </div>
        </div>
      ) : null}
      <p className="trust-warning">Production live alerts require a signing relay. Direct unsigned TradingView alerts are not live-safe.</p>
    </section>
  )
}

function TrustRow({ label, value, warning = false }: { label: string; value: string; warning?: boolean }) {
  return <p className={warning ? 'trust-row warning' : 'trust-row'}><span>{label}</span><strong>{value}</strong></p>
}

function CopyValue({ label, value, copied, onCopy }: { label: string; value: string; copied: boolean; onCopy: () => void }) {
  return (
    <div className="trust-copy">
      <span>{label}</span>
      <code>{value || 'Unavailable'}</code>
      <button type="button" onClick={onCopy} disabled={!value}>
        <Copy size={13} />
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  )
}

interface CheckItem {
  label: string
  ready: boolean
}

function check(label: string, ready: boolean): CheckItem {
  return { label, ready }
}

function formatTokenAge(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return 'Unknown'
  if (minutes < 60) return `${minutes} minutes`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

function formatDate(value: string | null | undefined, fallback = 'Not verified'): string {
  if (!value) return fallback
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return fallback
  return new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}
