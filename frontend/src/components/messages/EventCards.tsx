import { ExternalLink } from 'lucide-react'
import { formatCurrency } from '../../lib/format'
import type { RenderableMessage } from '../../types'

export function RejectCard({ message }: { message: RenderableMessage }) {
  const paper = message.data.mode === 'paper'
  const title = String(message.data.humanMessage ?? message.data.message ?? 'Order rejected')
  return (
    <article className="rich-event-card reject-card">
      <EventEyebrow label="Rejected" paper={paper} />
      <h3>{paper ? `Paper order rejected: ${title}` : title}</h3>
      <p>{String(message.data.correlationId ?? message.data.reason ?? 'Check Dhan for the exact rejection reason.')}</p>
    </article>
  )
}

export function ExitCard({ message }: { message: RenderableMessage }) {
  const netPnl = optionalNumber(message.data.netPnl ?? message.data.pnl)
  const paper = message.data.mode === 'paper'
  return (
    <article className="rich-event-card exit-card">
      <EventEyebrow label="Exit" paper={paper} />
      <h3>{paper ? 'Paper trade closed' : 'Trade closed'}</h3>
      <div className="event-grid">
        <div><span>Gross</span><strong>{formatOptionalCurrency(optionalNumber(message.data.grossPnl) ?? netPnl)}</strong></div>
        <div><span>Charges</span><strong>{formatOptionalCurrency(optionalNumber(message.data.charges))}</strong></div>
        <div><span>Net</span><strong>{formatOptionalCurrency(netPnl)}</strong></div>
      </div>
    </article>
  )
}

export function EodCard({ message }: { message: RenderableMessage }) {
  const pdfUrl = String(message.data.reportUrl ?? '')
  const paper = message.data.mode === 'paper'
  return (
    <article className="rich-event-card eod-card">
      <EventEyebrow label="EOD" paper={paper} />
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

function EventEyebrow({ label, paper }: { label: string; paper: boolean }) {
  return (
    <span className="event-eyebrow">
      <span className="eyebrow">{label}</span>
      {paper ? <span className="event-mode-chip">paper</span> : null}
    </span>
  )
}

function optionalNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function formatOptionalCurrency(value: number | null): string {
  return value === null ? 'Pending' : formatCurrency(value)
}
