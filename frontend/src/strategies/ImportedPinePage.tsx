import { AlertTriangle, Check, Copy, Download, FileCode2, Loader2, Plus, ShieldCheck, Sparkles, Upload, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  acceptPineConversion,
  createPineConversion,
  createPineStrategy,
  createPineVersion,
  decidePineReview,
  generatePineConversionPackage,
  getPineConversion,
  getPineConversionConfig,
  getPineReview,
  getPineSource,
  getPineStrategy,
  linkPineVersion,
  listPineReviews,
  listPineStrategies,
  listStrategyInstances,
  rejectPineConversion,
  retryPineConversion,
  submitPineVersion,
  validatePineVersion,
  type PineFinding,
  type PineConversion,
  type PineConversionConfig,
  type PineReview,
  type PineStrategy,
  type PineVersion,
  type StrategyInstance,
} from '../api'

const MAX_BYTES = 256 * 1024

export function ImportedPinePage({ isAdmin = false }: { isAdmin?: boolean }) {
  const [mode, setMode] = useState<'owner' | 'admin'>('owner')
  return (
    <div className="ps-page pine-page">
      <div className="ps-heading">
        <div>
          <span className="ps-eyebrow"><ShieldCheck size={13} /> Static review only</span>
          <h1>Imported Pine Scripts</h1>
          <p>Store, validate and review private Pine source. NOVA does not compile, backtest or execute it.</p>
        </div>
        {isAdmin ? <div className="ps-heading-actions"><button className="secondary-button" type="button" onClick={() => setMode(mode === 'owner' ? 'admin' : 'owner')}>{mode === 'owner' ? 'Admin review queue' : 'My scripts'}</button></div> : null}
      </div>
      <div className="ps-warning"><AlertTriangle size={16} /><span>Hosted execution is unavailable. Approval records compatibility evidence only and never enables live or paper execution.</span></div>
      {mode === 'admin' ? <AdminReview /> : <OwnerWorkspace />}
    </div>
  )
}

function OwnerWorkspace() {
  const [strategies, setStrategies] = useState<PineStrategy[]>([])
  const [strategyId, setStrategyId] = useState('')
  const [versions, setVersions] = useState<PineVersion[]>([])
  const [versionId, setVersionId] = useState('')
  const [source, setSource] = useState('')
  const [filename, setFilename] = useState('strategy.pine')
  const [name, setName] = useState('')
  const [changelog, setChangelog] = useState('')
  const [instances, setInstances] = useState<StrategyInstance[]>([])
  const [instanceId, setInstanceId] = useState('')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [conversionConfig, setConversionConfig] = useState<PineConversionConfig | null>(null)
  const [conversion, setConversion] = useState<PineConversion | null>(null)
  const [consent, setConsent] = useState(false)
  const sourceRef = useRef<HTMLTextAreaElement>(null)

  const selected = versions.find((row) => row.id === versionId) ?? null
  const findings = selected?.validation?.findings ?? []

  const refreshList = useCallback(async (preferred?: string) => {
    const [scripts, rows] = await Promise.all([listPineStrategies(), listStrategyInstances()])
    setStrategies(scripts)
    setInstances(rows.filter((row) => row.execution_mode !== 'real_orders'))
    setStrategyId((current) => preferred ?? current ?? scripts[0]?.id ?? '')
  }, [])

  useEffect(() => {
    Promise.all([listPineStrategies(), listStrategyInstances(), getPineConversionConfig()]).then(([scripts, rows, config]) => {
      setStrategies(scripts)
      setInstances(rows.filter((row) => row.execution_mode !== 'real_orders'))
      setStrategyId((current) => current || scripts[0]?.id || '')
      setConversionConfig(config)
    }).catch((reason) => setError(messageOf(reason)))
  }, [])
  useEffect(() => {
    if (!strategyId) return
    void getPineStrategy(strategyId).then((result) => {
      setVersions(result.versions)
      setVersionId((current) => result.versions.some((v) => v.id === current) ? current : result.versions[0]?.id ?? '')
    }).catch((reason) => setError(messageOf(reason)))
  }, [strategyId])
  useEffect(() => {
    if (!strategyId || !versionId) return
    void getPineSource(strategyId, versionId).then((result) => {
      setSource(result.source); setFilename(result.filename); setDirty(false)
    }).catch((reason) => setError(messageOf(reason)))
  }, [strategyId, versionId])
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])
  useEffect(() => {
    if (!conversion || !['queued', 'processing'].includes(conversion.status)) return
    const timer = window.setInterval(() => {
      void getPineConversion(conversion.id).then(({ conversion: current }) => setConversion(current)).catch((reason) => setError(messageOf(reason)))
    }, 1500)
    return () => window.clearInterval(timer)
  }, [conversion])

  async function run(label: string, task: () => Promise<void>) {
    setBusy(label); setError(''); setMessage('')
    try { await task(); setMessage(`${label} completed.`) } catch (reason) { setError(messageOf(reason)) } finally { setBusy('') }
  }

  async function readFile(file?: File) {
    if (!file) return
    if (!/\.(pine|txt)$/i.test(file.name)) { setError('Only .pine and .txt files are accepted.'); return }
    if (file.size > MAX_BYTES) { setError('The file exceeds the 256 KiB limit.'); return }
    try {
      const text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())
      setFilename(file.name); setSource(text); setDirty(true); setError('')
    } catch { setError('The file must be valid UTF-8 text.') }
  }

  async function reloadStrategy(id: string, preferredVersion?: string) {
    const result = await getPineStrategy(id)
    setVersions(result.versions)
    setVersionId(preferredVersion ?? result.versions[0]?.id ?? '')
    await refreshList(id)
  }

  function jumpTo(finding: PineFinding) {
    if (!finding.line || !sourceRef.current) return
    const lines = source.split('\n')
    const start = lines.slice(0, finding.line - 1).reduce((total, line) => total + line.length + 1, 0) + Math.max((finding.column ?? 1) - 1, 0)
    sourceRef.current.focus(); sourceRef.current.setSelectionRange(start, start + Math.max(finding.excerpt?.length ?? 1, 1))
  }

  async function copySource() {
    if (!navigator.clipboard) { setError('Clipboard access is unavailable.'); return }
    await navigator.clipboard.writeText(source); setMessage('Source copied.')
  }

  function downloadSource() {
    const url = URL.createObjectURL(new Blob([source], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click(); URL.revokeObjectURL(url)
  }

  async function manualPackage(download = false) {
    if (!selected) return
    const result = await generatePineConversionPackage(strategyId, selected.id)
    if (download) {
      const url = URL.createObjectURL(new Blob([result.package], { type: 'text/plain;charset=utf-8' }))
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = result.filename; anchor.click(); URL.revokeObjectURL(url)
    } else {
      await navigator.clipboard.writeText(result.package)
    }
  }

  return (
    <>
      {error ? <div className="ps-message error" role="alert">{error}</div> : null}
      {message ? <div className="ps-message success" role="status">{message}</div> : null}
      <div className="pine-toolbar ps-card">
        <label>Script<select value={strategyId} onChange={(e) => { const id = e.target.value; setStrategyId(id); if (!id) { setVersions([]); setVersionId('') } }}><option value="">New script</option>{strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        <label>Version<select value={versionId} disabled={!strategyId} onChange={(e) => setVersionId(e.target.value)}>{versions.map((v) => <option key={v.id} value={v.id}>{v.version} · {v.status}</option>)}</select></label>
        <label>New script name<input value={name} maxLength={160} onChange={(e) => setName(e.target.value)} placeholder="My NIFTY Pine strategy" /></label>
      </div>
      <div className="pine-grid">
        <section className="ps-card pine-editor-card">
          <div className="ps-card-head"><div><span>Private source</span><h2>{selected ? `Immutable version ${selected.version}` : 'Paste or upload Pine'}</h2></div><span className="ps-status">{selected?.status ?? 'new'}</span></div>
          <div className="pine-file-row"><label className="secondary-button"><Upload size={14} /> Upload .pine/.txt<input className="pine-file-input" type="file" accept=".pine,.txt,text/plain" onChange={(e) => void readFile(e.target.files?.[0])} /></label><input aria-label="Source filename" value={filename} maxLength={120} onChange={(e) => { setFilename(e.target.value); setDirty(true) }} /></div>
          <textarea ref={sourceRef} className="pine-source" aria-label="Pine source" spellCheck={false} value={source} onChange={(e) => { setSource(e.target.value); setDirty(true) }} />
          <label className="pine-changelog">Version note<input value={changelog} maxLength={1000} onChange={(e) => setChangelog(e.target.value)} placeholder="What changed?" /></label>
          <div className="ps-actions">
            {!strategyId ? <button className="ps-primary" type="button" disabled={!name.trim() || !source.trim() || !!busy} onClick={() => void run('Script creation', async () => { const result = await createPineStrategy({ name: name.trim(), source, filename }); setDirty(false); await reloadStrategy(result.strategy.id, result.version.id) })}><Plus size={14} /> Create immutable version</button> : <button className="ps-primary" type="button" disabled={!dirty || !source.trim() || !!busy} onClick={() => void run('New version', async () => { const result = await createPineVersion(strategyId, { source, filename, changelog }); setDirty(false); await reloadStrategy(strategyId, result.version.id) })}><Plus size={14} /> Save as new version</button>}
            {selected && ['draft', 'validation_failed', 'ready_for_review'].includes(selected.status) ? <button className="secondary-button" type="button" disabled={!!busy} onClick={() => void run('Static validation', async () => { await validatePineVersion(strategyId, selected.id); await reloadStrategy(strategyId, selected.id) })}>{busy === 'Static validation' ? <Loader2 className="ps-spin" size={14} /> : <FileCode2 size={14} />} Validate</button> : null}
            {selected?.status === 'ready_for_review' ? <button className="secondary-button" type="button" disabled={!!busy} onClick={() => void run('Review submission', async () => { await submitPineVersion(strategyId, selected.id); await reloadStrategy(strategyId, selected.id) })}><Check size={14} /> Submit for review</button> : null}
            {selected ? <button className="secondary-button" type="button" onClick={() => void copySource()}><Copy size={14} /> Copy</button> : null}
            {selected?.status === 'approved' ? <button className="secondary-button" type="button" onClick={downloadSource}><Download size={14} /> Download</button> : null}
          </div>
          {selected && conversionConfig?.manual_package_enabled ? <div className="pine-convert-panel"><div><strong>Convert to NOVA Format</strong><span>Manual packages make no provider request.</span></div><button className="secondary-button" type="button" onClick={() => void run('Package copy', async () => manualPackage())}><Copy size={14} /> Copy conversion package</button><button className="secondary-button" type="button" onClick={() => void manualPackage(true)}><Download size={14} /> Download package</button></div> : null}
          {selected ? <div className="pine-ai-panel"><div><strong><Sparkles size={14} /> AI-assisted conversion</strong><span>{conversionConfig?.ai_enabled ? `${conversionConfig.provider} · ${conversionConfig.model}` : 'Disabled by NOVA configuration. Manual conversion remains available.'}</span></div>{conversionConfig?.ai_enabled ? <><p>Your Pine source will be sent to the configured AI provider for conversion. NOVA will not execute the generated script automatically. Generated output may be incorrect and must pass NOVA validation and human review.</p><label className="ps-check"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />I consent to sending this exact source version for this conversion request.</label><button className="ps-primary" type="button" disabled={!consent || !!busy || (!!conversion && ['queued', 'processing'].includes(conversion.status))} onClick={() => void run('AI conversion request', async () => { const result = await createPineConversion(strategyId, selected.id); setConsent(false); setConversion(result.conversion) })}><Sparkles size={14} /> Send source for conversion</button></> : null}</div> : null}
        </section>
        <aside className="ps-card pine-findings">
          <div className="ps-card-head"><div><span>NOVA Pine Contract v1</span><h2>Static findings</h2></div>{selected?.validation ? <strong>{selected.validation.error_count}E · {selected.validation.warning_count}W</strong> : null}</div>
          {findings.length ? findings.map((finding, index) => <button type="button" key={`${finding.code}-${index}`} className={`pine-finding ${finding.severity.toLowerCase()}`} onClick={() => jumpTo(finding)}><span>{finding.severity} · {finding.code}{finding.line ? ` · line ${finding.line}` : ''}</span><strong>{finding.title}</strong><small>{finding.explanation}</small><em>{finding.remediation}</em></button>) : <div className="ps-empty-small"><FileCode2 size={22} /><strong>No validation report</strong><span>Run deterministic static validation for this exact version.</span></div>}
        </aside>
      </div>
      {conversion ? <ConversionReview conversion={conversion} busy={busy} onAccept={() => run('Candidate acceptance', async () => { const result = await acceptPineConversion(conversion.id); setConversion(result.conversion); await reloadStrategy(strategyId, result.conversion.candidate_version_id ?? undefined) })} onReject={() => run('Candidate rejection', async () => setConversion((await rejectPineConversion(conversion.id)).conversion))} onRetry={() => run('Conversion retry', async () => setConversion((await retryPineConversion(conversion.id)).conversion))} /> : null}
      {selected?.status === 'approved' ? <section className="ps-card pine-link"><div><h2>Link approved version</h2><p className="ps-note">This only records a paper-safe control-plane link. Hosted Pine execution remains unavailable.</p></div><select aria-label="Personal strategy instance" value={instanceId} onChange={(e) => setInstanceId(e.target.value)}><option value="">Choose an instance</option>{instances.map((i) => <option key={i.id} value={i.id}>{i.label} · {i.execution_mode}</option>)}</select><button className="ps-primary" type="button" disabled={!instanceId || !!busy} onClick={() => void run('Version link', async () => { await linkPineVersion(instanceId, strategyId, selected.id) })}>Link version</button></section> : null}
    </>
  )
}

function ConversionReview({ conversion, busy, onAccept, onReject, onRetry }: { conversion: PineConversion; busy: string; onAccept: () => Promise<void>; onReject: () => Promise<void>; onRetry: () => Promise<void> }) {
  const pending = ['queued', 'processing'].includes(conversion.status)
  return <section className="ps-card pine-conversion-review"><div className="ps-card-head"><div><span>{conversion.provider} · {conversion.model} · prompt {conversion.prompt_version}</span><h2>Conversion candidate</h2></div><span className="ps-status">{conversion.status}</span></div>{pending ? <div className="ps-page-state"><Loader2 className="ps-spin" size={20} /> Conversion is processing in the durable queue.</div> : null}{conversion.safe_error_code ? <div className="ps-message error" role="alert">Conversion failed safely: {conversion.safe_error_code.replaceAll('_', ' ').toLowerCase()}</div> : null}{conversion.candidate_source ? <><div className="pine-diff"><div><strong>Original source</strong><pre>{conversion.original_source}</pre></div><div><strong>Converted candidate</strong><pre>{conversion.candidate_source}</pre></div></div><div className="pine-conversion-meta"><p><strong>Summary:</strong> {conversion.conversion_summary}</p><p><strong>Assumptions:</strong> {conversion.assumptions.join('; ') || 'None reported'}</p><p><strong>Unsupported/removed:</strong> {conversion.unsupported_features.join('; ') || 'None reported'}</p><p><strong>AI warnings:</strong> {conversion.warnings.join('; ') || 'None reported'}</p><p><strong>Deterministic validation:</strong> {conversion.validation?.eligible_for_review ? 'Eligible for review' : 'Blocking findings remain'}</p></div><div className="ps-actions">{conversion.status === 'succeeded' ? <button className="ps-primary" type="button" disabled={!!busy} onClick={() => void onAccept()}><Check size={14} /> Accept as new version</button> : null}{['succeeded', 'validation_failed'].includes(conversion.status) ? <button className="ps-danger" type="button" disabled={!!busy} onClick={() => void onReject()}><X size={14} /> Reject candidate</button> : null}</div></> : null}{['provider_failed', 'canceled'].includes(conversion.status) ? <button className="secondary-button" type="button" disabled={!!busy} onClick={() => void onRetry()}><Sparkles size={14} /> Request another conversion</button> : null}</section>
}

function AdminReview() {
  const [queue, setQueue] = useState<PineReview[]>([])
  const [selected, setSelected] = useState<PineReview | null>(null)
  const [note, setNote] = useState('')
  const [acknowledge, setAcknowledge] = useState(false)
  const [error, setError] = useState('')
  const refresh = useCallback(async () => setQueue(await listPineReviews()), [])
  useEffect(() => { listPineReviews().then(setQueue).catch((reason) => setError(messageOf(reason))) }, [])
  const lines = useMemo(() => selected?.source?.split('\n') ?? [], [selected])
  async function open(id: string) { try { setSelected(await getPineReview(id)); setError('') } catch (reason) { setError(messageOf(reason)) } }
  async function decide(action: 'start' | 'approve' | 'request-changes' | 'reject') { if (!selected) return; try { await decidePineReview(selected.version.id, action, note, acknowledge); await refresh(); await open(selected.version.id) } catch (reason) { setError(messageOf(reason)) } }
  return <div className="pine-admin-grid"><aside className="ps-list">{queue.map((item) => <button className={`ps-list-item${selected?.version.id === item.version.id ? ' active' : ''}`} type="button" key={item.version.id} onClick={() => void open(item.version.id)}><strong>{item.strategy.name}</strong><span>{item.version.version} · {item.version.status}</span><span>{item.version.validation?.error_count ?? 0} errors · {item.version.validation?.warning_count ?? 0} warnings</span></button>)}</aside><main className="ps-card">{error ? <div className="ps-message error">{error}</div> : null}{selected ? <><div className="ps-card-head"><div><span>Exact source {selected.version.source_sha256.slice(0, 12)}…</span><h2>{selected.strategy.name} · {selected.version.version}</h2></div><span className="ps-status">{selected.version.status}</span></div><pre className="pine-review-source">{lines.map((line, i) => <span key={i}><i>{i + 1}</i>{line}{'\n'}</span>)}</pre><textarea className="pine-review-note" aria-label="Review note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Review note" /><label className="ps-check"><input type="checkbox" checked={acknowledge} onChange={(e) => setAcknowledge(e.target.checked)} />I reviewed and acknowledge all warnings on this exact source hash.</label><div className="ps-actions">{selected.version.status === 'submitted' ? <button className="ps-primary" type="button" onClick={() => void decide('start')}>Start review</button> : null}{selected.version.status === 'under_review' ? <><button className="ps-primary" type="button" onClick={() => void decide('approve')}>Approve</button><button className="secondary-button" type="button" onClick={() => void decide('request-changes')}>Request changes</button><button className="ps-danger" type="button" onClick={() => void decide('reject')}>Reject</button></> : null}</div></> : <div className="ps-empty"><FileCode2 size={28} /><h2>Select a review</h2></div>}</main></div>
}

function messageOf(reason: unknown) { return reason instanceof Error ? reason.message : 'Request failed.' }
