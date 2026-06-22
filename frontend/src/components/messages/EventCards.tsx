import { ChevronDown, Copy, ExternalLink, RotateCcw, ShieldCheck, Wifi } from 'lucide-react'
import { useState } from 'react'
import { verifyEgressIp } from '../../api'
import { formatCurrency } from '../../lib/format'
import { MotionSpinner } from '../MotionPrimitives'
import { useSessionStore } from '../../state/sessionStore'
import type { NormalizedError, OrderJourneyStep, RenderableMessage } from '../../types'

export function SignalCard({ message }: { message: RenderableMessage }) {
  return (
    <article className="rich-event-card signal-card">
      <EventEyebrow label="Signal" mode={stringValue(message.data.mode)} />
      <h3>{String(message.data.strategy ?? 'TradingView')} signal received</h3>
      <div className="event-grid compact">
        <div><span>Action</span><strong>{String(message.data.action ?? 'Pending')}</strong></div>
        <div><span>Option</span><strong>{String(message.data.optionSide ?? 'NIFTY')}</strong></div>
        <div><span>Order sent</span><strong>No decision yet</strong></div>
      </div>
      {message.data.signalId ? <code>{String(message.data.signalId)}</code> : null}
    </article>
  )
}

export function OrderPlacedCard({ message }: { message: RenderableMessage }) {
  return (
    <article className="rich-event-card placed-card">
      <EventEyebrow label="Order" mode={stringValue(message.data.mode)} />
      <h3>{String(message.data.status ?? 'Order placed')}</h3>
      <div className="event-grid compact">
        <div><span>Was order sent?</span><strong>Yes</strong></div>
        <div><span>Money at risk?</span><strong>{message.data.mode === 'live' ? 'Yes' : 'No'}</strong></div>
        <div><span>Order ID</span><strong>{String(message.data.orderId ?? 'Pending')}</strong></div>
      </div>
      <OrderTimeline steps={journey(message)} />
    </article>
  )
}

export function OrderErrorCard({ message }: { message: RenderableMessage }) {
  const [copied, setCopied] = useState(false)
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [actionStatus, setActionStatus] = useState('')
  const error = normalizedError(message)
  const mode = stringValue(message.data.mode)

  async function copyDebugPack() {
    const pack = error.debugPack ?? message.data.debugPack ?? {
      timestamp: error.timestamp,
      mode,
      source: error.source,
      category: error.category,
      severity: error.severity,
      signalId: error.signalId,
      correlationId: error.correlationId,
      orderSentToBroker: error.orderSentToBroker,
      moneyAtRisk: error.moneyAtRisk,
      rawStatusCode: error.rawStatusCode,
      technicalMessage: error.technicalMessage,
    }
    await navigator.clipboard.writeText(JSON.stringify(pack, null, 2))
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1400)
  }

  async function runAction(action: string) {
    setActionStatus('')
    if (action === 'copy_debug_pack') {
      await copyDebugPack()
      return
    }
    if (action === 'open_dhan') {
      window.open('https://web.dhan.co/', '_blank', 'noreferrer')
      return
    }
    if (action === 'reconnect_dhan') {
      window.dispatchEvent(new CustomEvent('nova:reconfigure-request'))
      return
    }
    if (action === 'retry') {
      setActionStatus('Retry requires a fresh TradingView signal or a manual order after checks.')
      return
    }
    setBusyAction(action)
    try {
      if (action === 'verify_static_ip') {
        await verifyEgressIp()
        setActionStatus('Static IP verification completed.')
      }
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : 'Action failed.')
    } finally {
      setBusyAction(null)
    }
  }

  return (
    <article className={`rich-event-card order-error-card severity-${error.severity}`}>
      <div className="error-card-topline">
        <EventEyebrow label="Rejected" mode={mode} />
        <span className={`severity-badge severity-${error.severity}`}>{error.severity}</span>
      </div>
      <h3>{error.userTitle}</h3>
      <p>{error.userMessage}</p>

      <div className="error-badge-row">
        <span>{sourceLabel(error.source)}</span>
        <span>{error.category.replaceAll('_', ' ')}</span>
      </div>

      <div className="event-grid compact">
        <div><span>Was order sent?</span><strong>{yesNoUnknown(error.orderSentToBroker)}</strong></div>
        <div><span>Money at risk?</span><strong>{yesNoUnknown(error.moneyAtRisk)}</strong></div>
        <div><span>Next action</span><strong>{error.nextAction}</strong></div>
      </div>

      <OrderTimeline steps={journey(message)} />

      <div className="error-meta-grid">
        {error.correlationId ? <div><span>Correlation</span><code>{error.correlationId}</code></div> : null}
        {error.signalId ? <div><span>Signal</span><code>{error.signalId}</code></div> : null}
        <div><span>Error</span><code>{error.errorId}</code></div>
      </div>

      {error.technicalMessage ? (
        <details className="technical-details">
          <summary><ChevronDown size={13} /> Technical details</summary>
          <code>{error.technicalMessage}</code>
        </details>
      ) : null}

      <div className="error-action-row">
        {actionButtons(error).map((action) => (
          <button
            key={action}
            type="button"
            className="event-action-button"
            disabled={busyAction !== null}
            onClick={() => void runAction(action)}
            aria-label={actionLabel(action)}
            title={actionLabel(action)}
          >
            {actionIcon(action, busyAction === action)}
            {actionLabel(action)}
          </button>
        ))}
      </div>
      {copied || actionStatus ? <p className="event-action-status">{copied ? 'Debug pack copied.' : actionStatus}</p> : null}
    </article>
  )
}

export function RejectCard({ message }: { message: RenderableMessage }) {
  return <OrderErrorCard message={message} />
}

export function RecentActivityCard() {
  const hiddenCount = useSessionStore((state) => state.hiddenRecentMessages.length)
  const showRecentActivity = useSessionStore((state) => state.showRecentActivity)
  return (
    <article className="rich-event-card recent-activity-card">
      <EventEyebrow label="History" mode={null} />
      <h3>Show recent backend activity</h3>
      <p>{hiddenCount > 0 ? `${hiddenCount} restored event${hiddenCount === 1 ? '' : 's'} are hidden from this fresh chat.` : 'Recent backend activity is available.'}</p>
      <button type="button" className="secondary-button" onClick={showRecentActivity} disabled={hiddenCount === 0}>
        Show recent backend activity
      </button>
    </article>
  )
}

export function ExitCard({ message }: { message: RenderableMessage }) {
  const netPnl = optionalNumber(message.data.netPnl ?? message.data.pnl)
  const paper = message.data.mode === 'paper'
  return (
    <article className="rich-event-card exit-card">
      <EventEyebrow label="Exit" mode={paper ? 'paper' : stringValue(message.data.mode)} />
      <h3>{paper ? 'Paper trade closed' : 'Trade closed'}</h3>
      <div className="event-grid">
        <div><span>Gross</span><strong>{formatOptionalCurrency(optionalNumber(message.data.grossPnl) ?? netPnl)}</strong></div>
        <div><span>Charges</span><strong>{formatOptionalCurrency(optionalNumber(message.data.charges))}</strong></div>
        <div><span>Net</span><strong>{formatOptionalCurrency(netPnl)}</strong></div>
      </div>
      <OrderTimeline steps={journey(message)} />
    </article>
  )
}

export function EodCard({ message }: { message: RenderableMessage }) {
  const pdfUrl = String(message.data.reportUrl ?? '')
  const paper = message.data.mode === 'paper'
  return (
    <article className="rich-event-card eod-card">
      <EventEyebrow label="EOD" mode={paper ? 'paper' : stringValue(message.data.mode)} />
      <h3>{paper ? 'Paper session summary' : 'Session summary'}</h3>
      <div className="event-grid">
        <div><span>Gross</span><strong>{formatOptionalCurrency(optionalNumber(message.data.grossPnl))}</strong></div>
        <div><span>Charges</span><strong>{formatOptionalCurrency(optionalNumber(message.data.charges))}</strong></div>
        <div><span>Net</span><strong>{formatOptionalCurrency(optionalNumber(message.data.netPnl))}</strong></div>
      </div>
      {pdfUrl ? (
        <a className="event-link" href={pdfUrl} target="_blank" rel="noreferrer">
          Open report
          <ExternalLink size={13} />
        </a>
      ) : null}
    </article>
  )
}

export function OrderTimeline({ steps }: { steps: OrderJourneyStep[] }) {
  if (!steps.length) return null
  return (
    <ol className="order-timeline" aria-label="Order journey">
      {steps.map((step, index) => (
        <li key={`${step.label}-${index}`} className={`timeline-step ${step.status}`}>
          <span>{index + 1}</span>
          <div>
            <strong>{step.label}</strong>
            {step.detail ? <small>{step.detail}</small> : null}
          </div>
        </li>
      ))}
    </ol>
  )
}

function EventEyebrow({ label, mode }: { label: string; mode: string | null }) {
  return (
    <span className="event-eyebrow">
      <span className="eyebrow">{label}</span>
      {mode ? <span className={`event-mode-chip ${mode === 'live' ? 'live' : ''}`}>{mode}</span> : null}
    </span>
  )
}

function normalizedError(message: RenderableMessage): NormalizedError {
  const candidate = message.data.normalizedError
  if (isNormalizedError(candidate)) return candidate
  const title = String(message.data.userTitle ?? message.data.humanMessage ?? message.data.message ?? 'Order rejected')
  return {
    errorId: String(message.data.errorId ?? message.id),
    source: 'NOVA_BACKEND',
    category: 'UNKNOWN',
    severity: 'blocked',
    userTitle: title,
    userMessage: String(message.data.reason ?? message.data.message ?? 'Nova blocked this action.'),
    technicalMessage: String(message.data.reason ?? message.data.message ?? ''),
    nextAction: 'Copy the debug pack and inspect backend logs before retrying.',
    retryable: false,
    moneyAtRisk: false,
    orderSentToBroker: false,
    actionButtons: ['copy_debug_pack'],
    correlationId: stringValue(message.data.correlationId),
    signalId: stringValue(message.data.signalId),
    rawStatusCode: null,
    timestamp: String(message.ts),
    mode: stringValue(message.data.mode),
    debugPack: message.data.debugPack as Record<string, unknown> | undefined,
  }
}

function isNormalizedError(value: unknown): value is NormalizedError {
  return Boolean(value && typeof value === 'object' && 'errorId' in value && 'userTitle' in value)
}

function journey(message: RenderableMessage): OrderJourneyStep[] {
  const value = message.data.orderJourney
  if (!Array.isArray(value)) return []
  return value
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    .map((item) => ({
      label: String(item.label ?? ''),
      status: timelineStatus(item.status),
      detail: item.detail === null || item.detail === undefined ? null : String(item.detail),
    }))
    .filter((item) => item.label)
}

function timelineStatus(value: unknown): OrderJourneyStep['status'] {
  return value === 'complete' || value === 'pending' || value === 'warning' || value === 'failed' ? value : 'pending'
}

function actionButtons(error: NormalizedError): string[] {
  const unique = new Set(error.actionButtons?.length ? error.actionButtons : ['copy_debug_pack'])
  unique.delete('refresh_ltp')
  unique.add('copy_debug_pack')
  return [...unique]
}

function actionLabel(action: string): string {
  if (action === 'retry') return 'Retry'
  if (action === 'reconnect_dhan') return 'Reconnect Dhan'
  if (action === 'verify_static_ip') return 'Verify Static IP'
  if (action === 'open_dhan') return 'Open Dhan'
  return 'Copy Debug Pack'
}

function actionIcon(action: string, busy: boolean) {
  if (busy) return <MotionSpinner><RotateCcw size={13} /></MotionSpinner>
  if (action === 'reconnect_dhan' || action === 'retry') return <RotateCcw size={13} />
  if (action === 'verify_static_ip') return <Wifi size={13} />
  if (action === 'open_dhan') return <ExternalLink size={13} />
  if (action === 'copy_debug_pack') return <Copy size={13} />
  return <ShieldCheck size={13} />
}

function sourceLabel(source: NormalizedError['source']): string {
  if (source === 'DHAN') return 'Dhan'
  if (source === 'TRADINGVIEW') return 'TradingView'
  if (source === 'RISK_ENGINE') return 'Risk'
  if (source === 'PAPER_ENGINE') return 'Paper'
  if (source === 'STATIC_IP') return 'Static IP'
  if (source === 'NETWORK') return 'Network'
  if (source === 'PUBSUB') return 'Pub/Sub'
  return 'Nova'
}

function yesNoUnknown(value: boolean | null | undefined): string {
  if (value === true) return 'Yes'
  if (value === false) return 'No'
  return 'Unknown'
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null
}

function optionalNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function formatOptionalCurrency(value: number | null): string {
  return value === null ? 'Pending' : formatCurrency(value)
}
