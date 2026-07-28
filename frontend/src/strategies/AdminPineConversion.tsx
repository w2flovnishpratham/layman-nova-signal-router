import { useCallback, useEffect, useState } from 'react'
import { Check, Copy, FileCode2, Loader2, RefreshCcw, Sparkles, Upload, X } from 'lucide-react'
import {
  approveAdminPineConversion,
  getAdminPineConversion,
  getAdminPineManualPackage,
  listAdminPineConversions,
  rejectAdminPineConversion,
  requestChangesAdminPineConversion,
  runAdminPineConversion,
  submitAdminPineConversion,
  submitAdminPineManualResponse,
} from '../api'
import type { AdminPineConversion } from '../api'
import { C2AdminPanel } from './C2AdminPanel'

const EMPTY_SOURCE = '//@version=6\nindicator("NIFTY strategy", overlay=true)\n'

export function AdminPineConversionWorkspace() {
  const [items, setItems] = useState<AdminPineConversion[]>([])
  const [selected, setSelected] = useState<AdminPineConversion | null>(null)
  const [strategyName, setStrategyName] = useState('')
  const [source, setSource] = useState(EMPTY_SOURCE)
  const [filename, setFilename] = useState('strategy.pine')
  const [notes, setNotes] = useState('')
  const [symbol, setSymbol] = useState('NIFTY')
  const [timeframe, setTimeframe] = useState('5')
  const [setupType, setSetupType] = useState<'USER_MANAGED_TRADINGVIEW' | 'NOVA_MANAGED_TRADINGVIEW'>('USER_MANAGED_TRADINGVIEW')
  const [manualResponse, setManualResponse] = useState('')
  const [manualPackage, setManualPackage] = useState('')
  const [reviewReason, setReviewReason] = useState('')
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const refresh = useCallback(async (preferredId?: string) => {
    const conversions = await listAdminPineConversions()
    setItems(conversions)
    const id = preferredId ?? selected?.id ?? conversions[0]?.id
    if (id) setSelected(await getAdminPineConversion(id))
  }, [selected?.id])

  useEffect(() => {
    let active = true
    listAdminPineConversions()
      .then(async (conversions) => {
        if (!active) return
        setItems(conversions)
        if (conversions[0]) {
          const detail = await getAdminPineConversion(conversions[0].id)
          if (active) setSelected(detail)
        }
      })
      .catch((reason) => { if (active) setError(messageOf(reason)) })
    return () => { active = false }
  }, [])

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label); setError(''); setMessage('')
    try { await action(); setMessage(`${label} completed`) }
    catch (reason) { setError(messageOf(reason)) }
    finally { setBusy('') }
  }

  async function open(id: string) {
    setManualPackage('')
    await run('Conversion refresh', async () => setSelected(await getAdminPineConversion(id)))
  }

  async function submit() {
    await run('Source submission', async () => {
      setManualPackage('')
      const conversion = await submitAdminPineConversion({
        strategy_name: strategyName.trim(),
        source,
        original_filename: filename,
        internal_notes: notes.trim() || undefined,
        options: {
          requested_setup_type: setupType,
          intended_symbol: symbol.trim(),
          intended_timeframe: timeframe.trim(),
        },
      })
      setSelected(await getAdminPineConversion(conversion.id))
      setStrategyName(''); setNotes('')
      await refresh(conversion.id)
    })
  }

  async function readFile(file: File | undefined) {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.pine') && !file.name.toLowerCase().endsWith('.txt')) {
      setError('Only .pine and .txt files are supported.')
      return
    }
    try {
      const bytes = await file.arrayBuffer()
      const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
      if (text.includes('\0')) throw new Error('binary')
      setSource(text); setFilename(file.name); setError('')
    } catch {
      setError('The selected file is not valid plain UTF-8 text.')
    }
  }

  return (
    <section className="ps-card c1-workspace" aria-label="Admin Pine Conversion">
      <div className="ps-card-head">
        <div>
          <span>Admin only · Claude candidate workflow</span>
          <h2>Admin Pine Conversion</h2>
        </div>
        <span className="ps-status">Compile review only</span>
      </div>
      <p className="ps-note">Claude returns an untrusted strategy layer. NOVA appends Transport V2 and validates the final candidate. No strategy instance, credential, alert, or order is created here.</p>
      {error ? <div className="ps-message error" role="alert">{error}</div> : null}
      {message ? <div className="ps-message success" role="status">{message}</div> : null}

      <div className="c1-submit-grid">
        <div className="c1-submit-form">
          <label>Strategy name<input aria-label="Conversion strategy name" value={strategyName} maxLength={160} onChange={(event) => setStrategyName(event.target.value)} /></label>
          <label>Original filename<input aria-label="Original Pine filename" value={filename} maxLength={120} onChange={(event) => setFilename(event.target.value)} /></label>
          <label>Internal notes<textarea aria-label="Internal conversion notes" value={notes} maxLength={2000} onChange={(event) => setNotes(event.target.value)} /></label>
          <div className="c1-options">
            <label>Symbol<input aria-label="Intended symbol" value={symbol} maxLength={30} onChange={(event) => setSymbol(event.target.value)} /></label>
            <label>Timeframe<input aria-label="Intended timeframe" value={timeframe} maxLength={20} onChange={(event) => setTimeframe(event.target.value)} /></label>
            <label>Setup type<select aria-label="Requested setup type" value={setupType} onChange={(event) => setSetupType(event.target.value as typeof setupType)}><option value="USER_MANAGED_TRADINGVIEW">User managed</option><option value="NOVA_MANAGED_TRADINGVIEW">NOVA managed</option></select></label>
          </div>
          <label className="secondary-button c1-file-button"><Upload size={14} /> Upload Pine<input className="pine-file-input" aria-label="Upload Pine source for conversion" type="file" accept=".pine,.txt,text/plain" onChange={(event) => void readFile(event.target.files?.[0])} /></label>
        </div>
        <label className="c1-source-label">Exact Pine source<textarea aria-label="Admin Pine source" className="pine-source" value={source} onChange={(event) => setSource(event.target.value)} /></label>
      </div>
      <button className="ps-primary" type="button" disabled={!strategyName.trim() || !source.trim() || !!busy} onClick={() => void submit()}>{busy === 'Source submission' ? <Loader2 className="ps-spin" size={14} /> : <FileCode2 size={14} />} Submit and analyze</button>

      <div className="c1-conversion-grid">
        <aside className="ps-list" aria-label="Conversion list">
          {items.map((item) => <button type="button" className={`ps-list-item${selected?.id === item.id ? ' active' : ''}`} key={item.id} onClick={() => void open(item.id)}><strong>{item.strategy_name}</strong><span>Owner {item.owner_user_id.slice(0, 8)} · {item.source_sha256.slice(0, 12)}…</span><span>{item.conversion_status.replaceAll('_', ' ')}</span><span>{item.provider_mode ?? 'No provider used'}</span></button>)}
          {!items.length ? <div className="ps-empty-small"><FileCode2 size={20} /><strong>No conversion submissions</strong></div> : null}
        </aside>
        <main className="c1-detail">
          {selected ? <ConversionDetail
            conversion={selected}
            busy={busy}
            manualPackage={manualPackage}
            manualResponse={manualResponse}
            reviewReason={reviewReason}
            onManualResponse={setManualResponse}
            onReviewReason={setReviewReason}
            onConvert={() => run('AI conversion', async () => { const value = await runAdminPineConversion(selected.id); setSelected(value); await refresh(selected.id) })}
            onManualPackage={() => run('Manual package copy', async () => { const value = await getAdminPineManualPackage(selected.id); setManualPackage(value.package); await navigator.clipboard.writeText(value.package) })}
            onSubmitManual={() => run('Manual response', async () => { const value = await submitAdminPineManualResponse(selected.id, manualResponse); setSelected(value); await refresh(selected.id) })}
            onApprove={() => run('Candidate approval', async () => { if (!window.confirm('Approve this exact candidate for TradingView compilation only?')) return; const value = await approveAdminPineConversion(selected.id, reviewReason); setSelected(value); await refresh(selected.id) })}
            onReject={() => run('Candidate rejection', async () => { const value = await rejectAdminPineConversion(selected.id, reviewReason); setSelected(value); await refresh(selected.id) })}
            onRequestChanges={() => run('Changes requested', async () => { const value = await requestChangesAdminPineConversion(selected.id, reviewReason); setSelected(value); await refresh(selected.id) })}
          /> : <div className="ps-empty"><FileCode2 size={28} /><h2>Select a conversion</h2></div>}
        </main>
      </div>
    </section>
  )
}

function ConversionDetail({
  conversion, busy, manualPackage, manualResponse, reviewReason, onManualResponse, onReviewReason,
  onConvert, onManualPackage, onSubmitManual, onApprove, onReject, onRequestChanges,
}: {
  conversion: AdminPineConversion
  busy: string
  manualPackage: string
  manualResponse: string
  reviewReason: string
  onManualResponse: (value: string) => void
  onReviewReason: (value: string) => void
  onConvert: () => Promise<void>
  onManualPackage: () => Promise<void>
  onSubmitManual: () => Promise<void>
  onApprove: () => Promise<void>
  onReject: () => Promise<void>
  onRequestChanges: () => Promise<void>
}) {
  const canConvert = ['READY_FOR_CONVERSION', 'AI_FAILED_RETRYABLE'].includes(conversion.conversion_status)
  const canManual = !['UNSUPPORTED_STRATEGY', 'APPROVED_FOR_TRADINGVIEW_COMPILE', 'REJECTED', 'CHANGES_REQUESTED'].includes(conversion.conversion_status)
  const canReview = conversion.conversion_status === 'READY_FOR_ADMIN_REVIEW' && conversion.validation?.eligible_for_review
  const canDecide = ['READY_FOR_ADMIN_REVIEW', 'VALIDATION_FAILED'].includes(conversion.conversion_status)
  const provenance = conversion.provenance ?? {}
  return <>
    <div className="ps-card-head"><div><span>{conversion.source_sha256.slice(0, 12)}… · {new Date(conversion.submitted_at ?? '').toLocaleString()}</span><h2>{conversion.strategy_name}</h2></div><span className="ps-status">{conversion.conversion_status.replaceAll('_', ' ')}</span></div>
    {conversion.safe_error_code ? <div className="ps-message error">Safe failure: {conversion.safe_error_code.replaceAll('_', ' ')}</div> : null}
    {!conversion.safe_error_code && conversion.unsupported_features.length > 0 && ['READY_FOR_ADMIN_REVIEW', 'APPROVED_FOR_TRADINGVIEW_COMPILE'].includes(conversion.conversion_status) ? (
      <div className="ps-message warning">
        Converted with disclosed NOVA normalizations — TradingView-specific execution behaviour was mapped to NOVA's confirmed-bar / server-managed execution model. Review every disclosed change below before approval.
      </div>
    ) : null}
    <div className="ps-summary-grid">
      <div><span>Analysis</span><strong>{conversion.analysis_status}</strong></div>
      <div><span>Validation</span><strong>{conversion.validation_status}</strong></div>
      <div><span>Review</span><strong>{conversion.review_status}</strong></div>
    </div>
    {conversion.conversion_summary || conversion.unsupported_features.length || conversion.warnings.length ? (
      <div className="c1-normalization">
        <strong>Semantic changes disclosed by Claude</strong>
        {conversion.conversion_summary ? <p className="ps-note">{conversion.conversion_summary}</p> : null}
        {conversion.unsupported_features.length ? <div><strong>Removed / normalized behaviour</strong><ul>{conversion.unsupported_features.map((item, index) => <li key={index}>{item}</li>)}</ul></div> : null}
        {conversion.warnings.length ? <div><strong>Admin review points</strong><ul>{conversion.warnings.map((item, index) => <li key={index}>{item}</li>)}</ul></div> : null}
      </div>
    ) : null}
    <div className="c1-capabilities">
      <strong>Deterministic pre-analysis (advisory only — never blocks conversion)</strong>
      <span>{conversion.analysis.effective_capability_level} · {conversion.analysis.confidence}</span>
      <span>Matched: {conversion.analysis.matched_capabilities.join(', ') || 'None'}</span>
      {conversion.analysis.blockers.length ? <span className="c1-blocker">Blockers: {conversion.analysis.blockers.join(', ')}</span> : null}
      {conversion.analysis.admin_review_points.length ? <span>Review: {conversion.analysis.admin_review_points.join('; ')}</span> : null}
    </div>
    {conversion.conversion_guidance?.notes.length ? (
      <div className="c1-normalization">
        <strong>Conversion guidance given to Claude</strong>
        <p className="ps-note">Informational context, not a gate — Claude was instructed to normalize these mechanisms to the safest supported NOVA equivalent and disclose the change.</p>
        {conversion.conversion_guidance.notes.map((note) => (
          <div className="c1-normalization-policy" key={note.blocker_code}>
            <span className="c1-blocker">{note.title} · {note.blocker_code}</span>
            {note.original_semantics.length ? <div><strong>Original semantics</strong><ul>{note.original_semantics.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
            <div><strong>Proposed NOVA semantics</strong><ul>{note.proposed_semantics.map((item) => <li key={item}>{item}</li>)}</ul></div>
          </div>
        ))}
      </div>
    ) : null}
    <div className="ps-actions">
      <button className="ps-primary" type="button" disabled={!canConvert || !!busy} onClick={() => void onConvert()}><Sparkles size={14} /> Run AI Conversion</button>
      <button className="secondary-button" type="button" disabled={!canManual || !!busy} onClick={() => void onManualPackage()}><Copy size={14} /> Open Manual Fallback</button>
    </div>
    {manualPackage ? <details open className="c1-transport"><summary>Current C1 manual package</summary><pre>{manualPackage}</pre></details> : null}
    {canManual ? <div className="c1-manual"><label>Structured manual Claude response<textarea aria-label="Manual Claude response JSON" value={manualResponse} onChange={(event) => onManualResponse(event.target.value)} /></label><button className="secondary-button" type="button" disabled={!manualResponse.trim() || !!busy} onClick={() => void onSubmitManual()}>Submit Manual Response</button></div> : null}
    {conversion.original_source ? <div className="pine-diff"><CodePanel title="Exact original source" value={conversion.original_source} /><CodePanel title="Converted strategy layer" value={conversion.strategy_layer ?? 'No candidate yet'} /></div> : null}
    {conversion.diff?.length ? <div className="c1-line-diff"><strong>Source / final candidate line diff</strong><pre>{conversion.diff.map((line, index) => <span className={`c1-diff-${line.kind}`} key={`${index}-${line.text}`}>{line.kind === 'added' ? '+ ' : line.kind === 'removed' ? '- ' : '  '}{line.text}{'\n'}</span>)}</pre></div> : null}
    {conversion.transport_source ? <details className="c1-transport"><summary>Server-added Transport V2</summary><pre>{conversion.transport_source}</pre></details> : null}
    {conversion.validation ? <div className="c1-findings"><strong>Deterministic validation · {conversion.validation.error_count} errors · {conversion.validation.warning_count} warnings</strong>{conversion.validation.findings.map((finding, index) => <span key={`${finding.code}-${index}`}>{finding.severity} · {finding.code} · {finding.title}</span>)}</div> : null}
    <div className="c1-provenance">
      <strong>Provider provenance</strong>
      <span>{conversion.provider_mode ?? 'Not called'} · {conversion.provider} · {conversion.model || 'Not configured'}</span>
      <span>Tokens: {String(provenance.input_token_count ?? '—')} in / {String(provenance.output_token_count ?? '—')} out · Latency: {String(provenance.latency_ms ?? '—')} ms · Cache: {String(provenance.cache_status ?? 'MISS')} · Repairs: {String(provenance.repair_count ?? 0)}</span>
      <span>Layer SHA: {conversion.strategy_layer_sha256 ?? '—'}</span>
      <span>Candidate SHA: {conversion.candidate_sha256 ?? '—'}</span>
    </div>
    <label className="c1-review-reason">Internal review reason<textarea aria-label="Conversion review reason" value={reviewReason} maxLength={500} onChange={(event) => onReviewReason(event.target.value)} /></label>
    <div className="ps-actions">
      <button className="ps-primary" type="button" disabled={!canReview || !!busy} onClick={() => void onApprove()}><Check size={14} /> Approve for TradingView compile</button>
      <button className="secondary-button" type="button" disabled={!canDecide || !reviewReason.trim() || !!busy} onClick={() => void onRequestChanges()}><RefreshCcw size={14} /> Request Changes</button>
      <button className="ps-danger" type="button" disabled={!canDecide || !reviewReason.trim() || !!busy} onClick={() => void onReject()}><X size={14} /> Reject Candidate</button>
    </div>
    {conversion.approval_integrity === false ? <div className="ps-message error">Approval binding no longer matches the candidate SHA.</div> : null}
    <C2AdminPanel conversion={conversion} />
  </>
}

function CodePanel({ title, value }: { title: string; value: string }) {
  return <div><strong>{title}</strong><pre>{value}</pre></div>
}

function messageOf(reason: unknown) {
  return reason instanceof Error ? reason.message : 'Request failed.'
}
