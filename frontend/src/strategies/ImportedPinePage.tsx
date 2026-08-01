import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { NativeSelect } from "@/components/ui/native-select"
import { Button } from '@/components/ui/button'
import { AlertTriangle, Check, Copy, Download, FileCode2, Loader2, Plus, Shield, Sparkles, Trash2, Upload, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from '@/components/ui/toast'
import {
  acceptPineConversion,
  createOwnerClaudeConversion,
  createPineStrategy,
  createPineVersion,
  createTradingViewSetup,
  decidePineReview,
  deletePineStrategy,
  generateManagedTradingViewCredential,
  generatePineConversionPackage,
  getPineConversion,
  startManagedTradingViewVerification,
  getPineConversionConfig,
  getOwnerClaudeConversion,
  getOwnerClaudeConversionConfig,
  getPineReview,
  getPineSource,
  getPineStrategy,
  getTradingViewSetup,
  linkPineVersion,
  listManagedTradingViewSetups,
  listPineConversions,
  listOwnerClaudeConversions,
  listPineReviews,
  listPineStrategies,
  listStrategyInstances,
  rejectPineConversion,
  retryPineConversion,
  recordManagedTradingViewInstallation,
  submitPineVersion,
  validatePineVersion,
  type PineFinding,
  type PineConversion,
  type PineConversionConfig,
  type AdminPineConversion,
  type OwnerClaudeConversionConfig,
  type PineReview,
  type PineStrategy,
  type PineVersion,
  type StrategyInstance,
  type TradingViewSetup,
  type TradingViewSetupType,
} from '../api'
import { AdminPineConversionWorkspace } from './AdminPineConversion'

const MAX_BYTES = 256 * 1024

export function ImportedPinePage({ isAdmin = false }: { isAdmin?: boolean }) {
  const [mode, setMode] = useState<'owner' | 'admin'>('owner')
  return (
    <div className="pine-page">
      {isAdmin ? (
        <div className="ps-toolbar-row">
          <p className="ps-note">Import private Pine, convert it with Claude, and follow its admin approval and TradingView installation.</p>
          <Button variant="unstyled" className="secondary-button" type="button" onClick={() => setMode(mode === 'owner' ? 'admin' : 'owner')}>{mode === 'owner' ? 'Admin review queue' : 'My scripts'}</Button>
        </div>
      ) : null}
      <div className="ps-warning"><AlertTriangle size={16} /><span>Conversion and admin approval do not place orders. The installed strategy remains HOLD-only until genuine TradingView routing and Paper entry/exit verification pass.</span></div>
      {mode === 'admin' ? <AdminWorkspace /> : <OwnerWorkspace />}
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
  const [error, setError] = useState('')
  const [conversionConfig, setConversionConfig] = useState<PineConversionConfig | null>(null)
  const [claudeConfig, setClaudeConfig] = useState<OwnerClaudeConversionConfig | null>(null)
  const [claudeConversion, setClaudeConversion] = useState<AdminPineConversion | null>(null)
  const [claudeHistory, setClaudeHistory] = useState<AdminPineConversion[]>([])
  const [conversion, setConversion] = useState<PineConversion | null>(null)
  const [consent, setConsent] = useState(false)
  const [originalVersionId, setOriginalVersionId] = useState('')
  const [acceptance, setAcceptance] = useState([false, false, false, false])
  const [conversionAssumptions, setConversionAssumptions] = useState('')
  const [setupType, setSetupType] = useState<TradingViewSetupType>('USER_MANAGED_TRADINGVIEW')
  const [tvSetup, setTvSetup] = useState<TradingViewSetup | null>(null)
  const [conversionHistory, setConversionHistory] = useState<PineConversion[]>([])
  const sourceRef = useRef<HTMLTextAreaElement>(null)

  const selected = versions.find((row) => row.id === versionId) ?? null
  const findings = selected?.validation?.findings ?? []
  const rejectionNote = selected?.review_history?.filter((event) => ['rejected', 'changes_requested'].includes(event.decision)).at(-1) ?? null

  const refreshList = useCallback(async (preferred?: string) => {
    const [scripts, rows] = await Promise.all([listPineStrategies(), listStrategyInstances()])
    setStrategies(scripts)
    setInstances(rows.filter((row) => row.execution_mode !== 'real_orders'))
    setStrategyId((current) => preferred ?? current ?? scripts[0]?.id ?? '')
  }, [])

  useEffect(() => {
    Promise.all([
      listPineStrategies(),
      listStrategyInstances(),
      getPineConversionConfig(),
      listPineConversions(),
      getOwnerClaudeConversionConfig(),
      listOwnerClaudeConversions(),
    ]).then(([scripts, rows, config, conversions, ownerConfig, ownerConversions]) => {
      setStrategies(scripts)
      setInstances(rows.filter((row) => row.execution_mode !== 'real_orders'))
      setStrategyId((current) => current || scripts[0]?.id || '')
      setConversionConfig(config)
      setConversionHistory(conversions)
      setClaudeConfig(ownerConfig)
      setClaudeHistory(ownerConversions)
      setClaudeConversion(ownerConversions[0] ?? null)
    }).catch((reason) => setError(messageOf(reason)))
  }, [])
  useEffect(() => {
    if (!strategyId) return
    void getPineStrategy(strategyId).then((result) => {
      setVersions(result.versions)
      setVersionId((current) => result.versions.some((v) => v.id === current) ? current : result.versions[0]?.id ?? '')
      setOriginalVersionId((current) => result.versions.some((v) => v.id === current) ? current : result.versions.at(-1)?.id ?? '')
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
  useEffect(() => {
    if (!claudeConversion) return
    const terminal = new Set([
      'APPROVED_FOR_TRADINGVIEW_COMPILE',
      'REJECTED',
      'UNSUPPORTED_STRATEGY',
      'MANUAL_CONVERSION_REQUIRED',
      'VALIDATION_FAILED',
    ])
    if (terminal.has(claudeConversion.conversion_status)) return
    const timer = window.setInterval(() => {
      void getOwnerClaudeConversion(claudeConversion.id)
        .then((current) => {
          setClaudeConversion(current)
          setClaudeHistory((rows) => [current, ...rows.filter((row) => row.id !== current.id)])
        })
        .catch((reason) => setError(messageOf(reason)))
    }, 3000)
    return () => window.clearInterval(timer)
  }, [claudeConversion])
  useEffect(() => {
    if (!instanceId) return
    void getTradingViewSetup(instanceId).then(setTvSetup).catch(() => setTvSetup(null))
  }, [instanceId])

  async function run(
    label: string,
    task: () => Promise<void>,
    feedback?: { loading: string; success: string },
  ) {
    setBusy(label)
    try {
      const request = task()
      await (feedback ? toast.promise(request, {
        loading: { title: feedback.loading, type: 'loading', timeout: 0 },
        success: { title: feedback.success, type: 'success' },
        error: (reason) => ({ title: messageOf(reason), type: 'error' }),
      }) : request)
      if (!feedback) toast.add({ title: `${label} completed.`, type: 'success' })
    } catch (reason) {
      if (!feedback) toast.add({ title: messageOf(reason), type: 'error' })
    } finally {
      setBusy('')
    }
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
    await navigator.clipboard.writeText(source)
    toast.add({ title: 'Source copied.', type: 'success' })
  }

  function downloadSource() {
    const url = URL.createObjectURL(new Blob([source], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click(); URL.revokeObjectURL(url)
  }

  async function withdrawStrategy() {
    if (!strategyId) return
    if (!window.confirm('Withdraw this script? Every draft version on it is removed. This cannot be undone.')) return
    await run('Withdraw script', async () => {
      await deletePineStrategy(strategyId)
      setStrategyId(''); setVersions([]); setVersionId('')
      await refreshList()
    })
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
      <div className="pine-toolbar ps-card">
        <label>Script<NativeSelect variant="unstyled" value={strategyId} onChange={(e) => { const id = e.target.value; setStrategyId(id); if (!id) { setVersions([]); setVersionId('') } }}><option value="">New script</option>{strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</NativeSelect></label>
        <label>Version<NativeSelect variant="unstyled" value={versionId} disabled={!strategyId} onChange={(e) => setVersionId(e.target.value)}>{versions.map((v) => <option key={v.id} value={v.id}>{v.version} · {v.status}</option>)}</NativeSelect></label>
        <label>New script name<Input variant="unstyled" value={name} maxLength={160} onChange={(e) => setName(e.target.value)} placeholder="My NIFTY Pine strategy" /></label>
        {strategyId ? <Button variant="unstyled" className="ps-danger" type="button" disabled={!!busy} onClick={() => void withdrawStrategy()}><Trash2 size={14} /> Withdraw script</Button> : null}
      </div>
      <div className="pine-grid">
        <section className="ps-card pine-editor-card">
          <div className="ps-card-head"><div><span>Private source</span><h2>{selected ? `Immutable version ${selected.version}` : 'Paste or upload Pine'}</h2></div><span className="ps-status">{selected?.status ?? 'new'}</span></div>
          {rejectionNote ? (
            <div className="ps-message error" role="alert">
              <strong>{rejectionNote.decision === 'rejected' ? 'Rejected by admin review' : 'Changes requested by admin review'}</strong>
              <span>{rejectionNote.note || 'No reason was recorded. Create a new version to address the review.'}</span>
            </div>
          ) : null}
          <div className="pine-file-row"><label className="secondary-button"><Upload size={14} /> Upload .pine/.txt<Input variant="unstyled" className="pine-file-input" type="file" accept=".pine,.txt,text/plain" onChange={(e) => void readFile(e.target.files?.[0])} /></label><Input variant="unstyled" aria-label="Source filename" value={filename} maxLength={120} onChange={(e) => { setFilename(e.target.value); setDirty(true) }} /></div>
          <Textarea variant="unstyled" ref={sourceRef} className="pine-source" aria-label="Pine source" spellCheck={false} value={source} onChange={(e) => { setSource(e.target.value); setDirty(true) }} />
          <label className="pine-changelog">Version note<Input variant="unstyled" value={changelog} maxLength={1000} onChange={(e) => setChangelog(e.target.value)} placeholder="What changed?" /></label>
          <div className="ps-actions">
            {!strategyId ? <Button variant="unstyled" className="ps-primary" type="button" disabled={!name.trim() || !source.trim() || !!busy} onClick={() => void run('Script creation', async () => { const result = await createPineStrategy({ name: name.trim(), source, filename }); setDirty(false); await reloadStrategy(result.strategy.id, result.version.id) })}><Plus size={14} /> Create immutable version</Button> : <Button variant="unstyled" className="ps-primary" type="button" disabled={!dirty || !source.trim() || !!busy} onClick={() => void run('New version', async () => { const result = await createPineVersion(strategyId, { source, filename, changelog }); setDirty(false); await reloadStrategy(strategyId, result.version.id) })}><Plus size={14} /> Save as new version</Button>}
            {selected && ['draft', 'validation_failed', 'ready_for_review'].includes(selected.status) ? <Button variant="unstyled" className="secondary-button" type="button" disabled={!!busy} onClick={() => void run('Static validation', async () => { await validatePineVersion(strategyId, selected.id); await reloadStrategy(strategyId, selected.id) }, { loading: 'Validating this Pine version…', success: 'Pine validation finished.' })}>{busy === 'Static validation' ? <Loader2 className="ps-spin" size={14} /> : <FileCode2 size={14} />} Validate</Button> : null}
            {selected ? <Button variant="unstyled" className="secondary-button" type="button" onClick={() => void copySource()}><Copy size={14} /> Copy</Button> : null}
            {selected?.status === 'approved' ? <Button variant="unstyled" className="secondary-button" type="button" onClick={downloadSource}><Download size={14} /> Download</Button> : null}
          </div>
          {selected && claudeConfig && !claudeConfig.enabled && conversionConfig?.manual_package_enabled ? <div className="pine-convert-panel"><div><strong>Manual conversion fallback</strong><span>Prompt {conversionConfig.prompt_version} · {conversionConfig.prompt_status}. This fallback makes no provider request.</span>{conversionConfig.prompt_version.startsWith('v3') ? <><ol><li>Copy this package into ChatGPT or Claude.</li><li>Copy only Artifact 1 back into NOVA as the converted Pine.</li><li>Artifact 2 is a simple status.</li></ol><p className="ps-note"><strong>Artifact 3 is for NOVA review.</strong> You do not need to edit it.</p></> : null}</div><Button variant="unstyled" className="secondary-button" type="button" onClick={() => void run('Package copy', async () => manualPackage())}><Copy size={14} /> Copy conversion package</Button><Button variant="unstyled" className="secondary-button" type="button" onClick={() => void manualPackage(true)}><Download size={14} /> Download package</Button></div> : null}
          {claudeConfig && !claudeConfig.enabled && selected?.status === 'ready_for_review' ? <div className="pine-acceptance-panel"><strong>Legacy manual review fallback</strong><p>Static validation is deterministic, but it is not a TradingView compilation test.</p><label>Original Pine version<NativeSelect variant="unstyled" aria-label="Original Pine version" value={originalVersionId} onChange={(event) => setOriginalVersionId(event.target.value)}>{versions.map((version) => <option key={version.id} value={version.id}>{version.version} · {version.source_sha256.slice(0, 10)}</option>)}</NativeSelect></label><fieldset><legend>TradingView setup type</legend><label className="ps-check"><Input variant="unstyled" type="radio" name="review-tv-setup" checked={setupType === 'USER_MANAGED_TRADINGVIEW'} onChange={() => setSetupType('USER_MANAGED_TRADINGVIEW')} />I have TradingView Premium</label><label className="ps-check"><Input variant="unstyled" type="radio" name="review-tv-setup" checked={setupType === 'NOVA_MANAGED_TRADINGVIEW'} onChange={() => setSetupType('NOVA_MANAGED_TRADINGVIEW')} />I need NOVA-managed TradingView setup</label></fieldset>{!conversionConfig?.prompt_version.startsWith('v3') ? <label>Conversion assumptions (one per line)<Textarea variant="unstyled" value={conversionAssumptions} maxLength={4000} onChange={(event) => setConversionAssumptions(event.target.value)} /></label> : null}{[
            'I reviewed the converted strategy.',
            'I understand static validation does not guarantee TradingView compilation.',
            'I understand backtests do not guarantee future returns.',
            'I understand the strategy will initially run in paper mode.',
          ].map((label, index) => <label className="ps-check" key={label}><Input variant="unstyled" type="checkbox" checked={acceptance[index]} onChange={(event) => setAcceptance((current) => current.map((value, item) => item === index ? event.target.checked : value))} />{label}</label>)}<Button variant="unstyled" className="ps-primary" type="button" disabled={!originalVersionId || !acceptance.every(Boolean) || !!busy} onClick={() => void run('Review submission', async () => { await submitPineVersion(strategyId, selected.id, { original_version_id: originalVersionId, prompt_version_id: conversionConfig?.prompt_version ?? '', setup_type: setupType, assumptions: conversionConfig?.prompt_version.startsWith('v3') ? [] : conversionAssumptions.split('\n').map((value) => value.trim()).filter(Boolean), reviewed_strategy: true, understands_static_validation: true, understands_performance_risk: true, accepts_paper_only: true }); setAcceptance([false, false, false, false]); await reloadStrategy(strategyId, selected.id) }, { loading: 'Submitting this Pine version for review…', success: 'Pine version sent for admin review.' })}><Check size={14} /> Accept and submit for review</Button></div> : null}
          {selected ? (
            <div className="pine-ai-panel">
              <div>
                <strong><Sparkles size={14} /> Convert with Claude</strong>
                <span>
                  {claudeConfig?.enabled
                    ? `${claudeConfig.provider} · ${claudeConfig.model} · prompt ${claudeConfig.prompt_version}`
                    : 'Claude conversion is not configured on this environment.'}
                </span>
              </div>
              <p>
                NOVA binds this exact source hash to your account, converts it with Claude,
                validates it, and sends it to the admin review queue. Approval can install
                the strategy only into your account.
              </p>
              <fieldset>
                <legend>TradingView setup after approval</legend>
                <label className="ps-check">
                  <Input variant="unstyled" type="radio" name="claude-tv-setup" checked={setupType === 'USER_MANAGED_TRADINGVIEW'} onChange={() => setSetupType('USER_MANAGED_TRADINGVIEW')} />
                  I have TradingView Premium
                </label>
                <label className="ps-check">
                  <Input variant="unstyled" type="radio" name="claude-tv-setup" checked={setupType === 'NOVA_MANAGED_TRADINGVIEW'} onChange={() => setSetupType('NOVA_MANAGED_TRADINGVIEW')} />
                  Use NOVA-managed TradingView
                </label>
              </fieldset>
              <label className="ps-check">
                <Input variant="unstyled" type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
                I consent to sending this exact private Pine version to Claude for conversion.
              </label>
              <Button variant="unstyled"
                className="ps-primary"
                type="button"
                disabled={!claudeConfig?.enabled || !consent || !!busy}
                onClick={() => void run('Claude conversion request', async () => {
                  const result = await createOwnerClaudeConversion(strategyId, selected.id, {
                    requested_setup_type: setupType,
                    intended_symbol: 'NIFTY',
                    intended_timeframe: '5',
                  })
                  setConsent(false)
                  setClaudeConversion(result.conversion)
                  setClaudeHistory((rows) => [result.conversion, ...rows.filter((row) => row.id !== result.conversion.id)])
                }, {
                  loading: 'Converting with Claude… this can take up to a minute.',
                  success: 'Conversion finished and was sent for admin review.',
                })}
              >
                {busy === 'Claude conversion request' ? (
                  <><Loader2 className="ps-spin" size={14} /> Converting with Claude… this can take up to a minute</>
                ) : (
                  <><Sparkles size={14} /> Convert and send for admin review</>
                )}
              </Button>
            </div>
          ) : null}
        </section>
        <aside className="ps-card pine-findings">
          <div className="ps-card-head"><div><span>NOVA Pine Contract v1</span><h2>Static findings</h2></div>{selected?.validation ? <strong>{selected.validation.error_count}E · {selected.validation.warning_count}W</strong> : null}</div>
          {findings.length ? findings.map((finding, index) => <Button variant="unstyled" type="button" key={`${finding.code}-${index}`} className={`pine-finding ${finding.severity.toLowerCase()}`} onClick={() => jumpTo(finding)}><span>{finding.severity} · {finding.code}{finding.line ? ` · line ${finding.line}` : ''}</span><strong>{finding.title}</strong><small>{finding.explanation}</small><em>{finding.remediation}</em></Button>) : <div className="ps-empty-small"><FileCode2 size={22} /><strong>No validation report</strong><span>Run deterministic static validation for this exact version.</span></div>}
        </aside>
      </div>
      {claudeConversion ? <OwnerClaudeStatus conversion={claudeConversion} /> : null}
      {strategyId && claudeHistory.some((row) => row.strategy_id === strategyId) ? (
        <section className="ps-card pine-conversion-history">
          <div className="ps-card-head">
            <div><span>Owner-bound Claude requests</span><h2>Conversion and review history</h2></div>
          </div>
          <div className="ps-list">
            {claudeHistory.filter((row) => row.strategy_id === strategyId).map((row) => (
              <Button variant="unstyled" className="ps-list-item" type="button" key={row.id} onClick={() => setClaudeConversion(row)}>
                <strong>{row.strategy_name}</strong>
                <span>{row.conversion_status.replaceAll('_', ' ')}</span>
                <span>{row.source_sha256.slice(0, 12)}… · owner bound</span>
              </Button>
            ))}
          </div>
        </section>
      ) : null}
      {claudeConfig && !claudeConfig.enabled && conversion ? <ConversionReview conversion={conversion} busy={busy} onAccept={() => run('Candidate acceptance', async () => { const result = await acceptPineConversion(conversion.id); setConversion(result.conversion); await reloadStrategy(strategyId, result.conversion.candidate_version_id ?? undefined); setConversionHistory(await listPineConversions()) })} onReject={() => run('Candidate rejection', async () => { setConversion((await rejectPineConversion(conversion.id)).conversion); setConversionHistory(await listPineConversions()) })} onRetry={() => run('Conversion retry', async () => { setConversion((await retryPineConversion(conversion.id)).conversion); setConversionHistory(await listPineConversions()) })} /> : null}
      {claudeConfig && !claudeConfig.enabled && strategyId && conversionHistory.some((row) => row.strategy_id === strategyId) ? (
        <section className="ps-card pine-conversion-history">
          <div className="ps-card-head"><div><span>All attempts for this script</span><h2>Conversion history</h2></div></div>
          <div className="ps-list">
            {conversionHistory.filter((row) => row.strategy_id === strategyId).map((row) => (
              <div className="ps-list-item" key={row.id}>
                <strong>{row.provider} · {row.model}</strong>
                <span>{row.status}{row.safe_error_code ? ` · ${row.safe_error_code.replaceAll('_', ' ').toLowerCase()}` : ''}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}
      {claudeConfig && !claudeConfig.enabled && selected?.status === 'approved' ? <section className="ps-card pine-link"><div><span>Legacy fallback · TradingView setup</span><h2>Link approved version and choose setup path</h2><p className="ps-note">Paper-only. NOVA never compiles or executes Pine.</p></div><NativeSelect variant="unstyled" aria-label="Personal strategy instance" value={instanceId} onChange={(e) => { setInstanceId(e.target.value); setTvSetup(null) }}><option value="">Choose an instance</option>{instances.map((i) => <option key={i.id} value={i.id}>{i.label} · {i.execution_mode}</option>)}</NativeSelect><fieldset><legend>TradingView setup type</legend><label className="ps-check"><Input variant="unstyled" type="radio" name="tv-setup" checked={setupType === 'USER_MANAGED_TRADINGVIEW'} onChange={() => setSetupType('USER_MANAGED_TRADINGVIEW')} />I have TradingView Premium</label><small>You manage this strategy in your TradingView account.</small><label className="ps-check"><Input variant="unstyled" type="radio" name="tv-setup" checked={setupType === 'NOVA_MANAGED_TRADINGVIEW'} onChange={() => setSetupType('NOVA_MANAGED_TRADINGVIEW')} />I need NOVA-managed TradingView setup</label><small>NOVA-managed TradingView setup requested; installation remains a manual admin task.</small></fieldset><Button variant="unstyled" className="ps-primary" type="button" disabled={!instanceId || !!busy} onClick={() => void run('TradingView setup', async () => { await linkPineVersion(instanceId, strategyId, selected.id); setTvSetup(await createTradingViewSetup(instanceId, setupType)) })}>Save setup path</Button>{tvSetup ? <div className={`ps-message ${tvSetup.ready_for_paper ? 'success' : ''}`} role="status"><strong>{tvSetup.ready_for_paper ? 'READY FOR PAPER USE' : tvSetup.status.replaceAll('_', ' ')}</strong><span>{tvSetup.ready_for_paper ? 'All server-observed paper gates passed.' : `Pending: ${tvSetup.blocking_step ?? 'manual review'}. Next: ${tvSetup.who_acts_next}.`}</span>{tvSetup.blocking_reason ? <span>{tvSetup.blocking_reason}</span> : null}</div> : null}</section> : null}
    </>
  )
}

function OwnerClaudeStatus({ conversion }: { conversion: AdminPineConversion }) {
  const approved = conversion.conversion_status === 'APPROVED_FOR_TRADINGVIEW_COMPILE'
  const reviewReady = conversion.conversion_status === 'READY_FOR_ADMIN_REVIEW'
  return (
    <section className="ps-card pine-conversion-review">
      <div className="ps-card-head">
        <div>
          <span>{conversion.provider} · {conversion.model} · source {conversion.source_sha256.slice(0, 12)}…</span>
          <h2>Claude conversion for {conversion.strategy_name}</h2>
        </div>
        <span className="ps-status">{conversion.conversion_status.replaceAll('_', ' ')}</span>
      </div>
      {conversion.safe_error_code ? (
        <div className="ps-message error" role="alert">
          Conversion stopped safely: {conversion.safe_error_code.replaceAll('_', ' ').toLowerCase()}
        </div>
      ) : null}
      {!conversion.safe_error_code && conversion.unsupported_features.length > 0 && ['READY_FOR_ADMIN_REVIEW', 'APPROVED_FOR_TRADINGVIEW_COMPILE'].includes(conversion.conversion_status) ? (
        <div className="ps-message warning" role="status">
          Converted with disclosed NOVA normalizations — TradingView-specific execution behaviour was mapped to NOVA's confirmed-bar / server-managed execution model. Review all disclosed changes below.
        </div>
      ) : null}
      <div className="ps-summary-grid">
        <div><span>Conversion</span><strong>{conversion.conversion_status.replaceAll('_', ' ')}</strong></div>
        <div><span>Validation</span><strong>{conversion.validation_status.replaceAll('_', ' ')}</strong></div>
        <div><span>Admin review</span><strong>{conversion.review_status.replaceAll('_', ' ')}</strong></div>
      </div>
      {conversion.conversion_summary ? <p>{conversion.conversion_summary}</p> : null}
      {conversion.unsupported_features.length ? (
        <div className="c1-normalization"><strong>Removed / normalized behaviour</strong><ul>{conversion.unsupported_features.map((item, index) => <li key={index}>{item}</li>)}</ul></div>
      ) : null}
      {reviewReady ? <div className="ps-message">Claude conversion and deterministic validation passed. An admin must now review this exact candidate.</div> : null}
      {approved ? <div className="ps-message success">Admin approved the exact candidate. TradingView compile evidence will create an installation in your account; HOLD and Paper verification remain required before it can trade.</div> : null}
      {conversion.final_candidate ? (
        <details>
          <summary>View converted Pine candidate</summary>
          <pre className="pine-review-source">{conversion.final_candidate}</pre>
        </details>
      ) : null}
    </section>
  )
}

function ConversionReview({ conversion, busy, onAccept, onReject, onRetry }: { conversion: PineConversion; busy: string; onAccept: () => Promise<void>; onReject: () => Promise<void>; onRetry: () => Promise<void> }) {
  const pending = ['queued', 'processing'].includes(conversion.status)
  return <section className="ps-card pine-conversion-review"><div className="ps-card-head"><div><span>{conversion.provider} · {conversion.model} · prompt {conversion.prompt_version}</span><h2>Conversion candidate</h2></div><span className="ps-status">{conversion.status}</span></div>{pending ? <div className="ps-page-state"><Loader2 className="ps-spin" size={20} /> Conversion is processing in the durable queue.</div> : null}{conversion.safe_error_code ? <div className="ps-message error" role="alert">Conversion failed safely: {conversion.safe_error_code.replaceAll('_', ' ').toLowerCase()}</div> : null}{conversion.candidate_source ? <><div className="pine-diff"><div><strong>Original source</strong><pre>{conversion.original_source}</pre></div><div><strong>Converted candidate</strong><pre>{conversion.candidate_source}</pre></div></div><div className="pine-conversion-meta"><p><strong>Summary:</strong> {conversion.conversion_summary}</p><p><strong>Assumptions:</strong> {conversion.assumptions.join('; ') || 'None reported'}</p><p><strong>Unsupported/removed:</strong> {conversion.unsupported_features.join('; ') || 'None reported'}</p><p><strong>AI warnings:</strong> {conversion.warnings.join('; ') || 'None reported'}</p><p><strong>Deterministic validation:</strong> {conversion.validation?.eligible_for_review ? 'Eligible for review' : 'Blocking findings remain'}</p></div><div className="ps-actions">{conversion.status === 'succeeded' ? <Button variant="unstyled" className="ps-primary" type="button" disabled={!!busy} onClick={() => void onAccept()}><Check size={14} /> Accept as new version</Button> : null}{['succeeded', 'validation_failed'].includes(conversion.status) ? <Button variant="unstyled" className="ps-danger" type="button" disabled={!!busy} onClick={() => void onReject()}><X size={14} /> Reject candidate</Button> : null}</div></> : null}{['provider_failed', 'canceled'].includes(conversion.status) ? <Button variant="unstyled" className="secondary-button" type="button" disabled={!!busy} onClick={() => void onRetry()}><Sparkles size={14} /> Request another conversion</Button> : null}</section>
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
  async function decide(action: 'start' | 'approve' | 'request-changes' | 'reject') {
    if (!selected) return
    try {
      await decidePineReview(selected.version.id, action, note, acknowledge)
      await refresh()
      await open(selected.version.id)
      toast.add({ title: 'Review decision saved.', type: 'success' })
    } catch (reason) {
      toast.add({ title: messageOf(reason), type: 'error' })
    }
  }
  return <div className="pine-admin-grid"><aside className="ps-list">{queue.map((item) => <Button variant="unstyled" className={`ps-list-item${selected?.version.id === item.version.id ? ' active' : ''}`} type="button" key={item.version.id} onClick={() => void open(item.version.id)}><strong>{item.strategy.name}</strong><span>{item.version.version} · {item.version.status}</span><span>{item.version.validation?.error_count ?? 0} errors · {item.version.validation?.warning_count ?? 0} warnings</span></Button>)}</aside><main className="ps-card">{error ? <div className="ps-message error">{error}</div> : null}{selected ? <><div className="ps-card-head"><div><span>Exact source {selected.version.source_sha256.slice(0, 12)}…</span><h2>{selected.strategy.name} · {selected.version.version}</h2></div><span className="ps-status">{selected.version.status}</span></div>{selected.acceptance ? <div className="pine-acceptance-panel"><strong>User acceptance recorded</strong><span>Prompt {selected.acceptance.prompt_version_id} · {selected.acceptance.setup_type.replaceAll('_', ' ')}</span><span>Validation {selected.acceptance.validation_report_sha256.slice(0, 12)}… · original version {selected.acceptance.original_version_id}</span><span>Accepted {new Date(selected.acceptance.accepted_at).toLocaleString()}</span><span>Assumptions: {selected.acceptance.assumptions.join('; ') || 'None supplied'}</span></div> : <div className="ps-message error">User acceptance evidence is missing. Review actions are blocked.</div>}<pre className="pine-review-source">{lines.map((line, i) => <span key={i}><i>{i + 1}</i>{line}{'\n'}</span>)}</pre><Textarea variant="unstyled" className="pine-review-note" aria-label="Review note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Review note" /><label className="ps-check"><Input variant="unstyled" type="checkbox" checked={acknowledge} onChange={(e) => setAcknowledge(e.target.checked)} />I reviewed and acknowledge all warnings on this exact source hash.</label><div className="ps-actions">{selected.version.status === 'submitted' ? <Button variant="unstyled" className="ps-primary" type="button" disabled={!selected.acceptance} onClick={() => void decide('start')}>Start review</Button> : null}{selected.version.status === 'under_review' ? <><Button variant="unstyled" className="ps-primary" type="button" disabled={!selected.acceptance} onClick={() => void decide('approve')}>Approve</Button><Button variant="unstyled" className="secondary-button" type="button" onClick={() => void decide('request-changes')}>Request changes</Button><Button variant="unstyled" className="ps-danger" type="button" onClick={() => void decide('reject')}>Reject</Button></> : null}</div></> : <div className="ps-empty"><FileCode2 size={28} /><h2>Select a review</h2></div>}</main></div>
}

function AdminWorkspace() {
  return <><AdminConsole /><AdminReview /></>
}

function AdminConsole() {
  const [tab, setTab] = useState<'queue' | 'managed' | 'all'>('queue')
  return (
    <div className="admin-console">
      <span className="admin-console-badge"><Shield size={12} /> ADMIN CONSOLE</span>
      <div className="admin-console-tabs">
        <button type="button" className={tab === 'queue' ? 'active' : ''} onClick={() => setTab('queue')}>Review queue</button>
        <button type="button" className={tab === 'managed' ? 'active' : ''} onClick={() => setTab('managed')}>Managed setups</button>
        <button type="button" className={tab === 'all' ? 'active' : ''} onClick={() => setTab('all')}>All strategies</button>
      </div>
      {tab === 'queue' ? <AdminPineConversionWorkspace /> : null}
      {tab === 'managed' ? <ManagedSetupQueue /> : null}
      {tab === 'all' ? <div className="ps-empty-small"><FileCode2 size={20} /><strong>Full strategy registry browser coming soon</strong></div> : null}
    </div>
  )
}

function ManagedSetupQueue() {
  const [setups, setSetups] = useState<TradingViewSetup[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [workspace, setWorkspace] = useState('')
  const [alertRef, setAlertRef] = useState('')
  const [symbol, setSymbol] = useState('NIFTY')
  const [timeframe, setTimeframe] = useState('5')
  const [error, setError] = useState('')
  const [issuedToken, setIssuedToken] = useState<string | null>(null)
  const [revealed, setRevealed] = useState(false)
  const selected = setups.find((setup) => setup.id === selectedId) ?? null
  const refresh = useCallback(async () => {
    const rows = await listManagedTradingViewSetups()
    setSetups(rows); setSelectedId((current) => rows.some((row) => row.id === current) ? current : rows[0]?.id ?? '')
  }, [])
  useEffect(() => {
    void listManagedTradingViewSetups().then((rows) => {
      setSetups(rows); setSelectedId(rows[0]?.id ?? '')
    }).catch((reason) => setError(messageOf(reason)))
  }, [])
  async function recordInstallation() {
    if (!selected?.approved_source_sha256) return
    try {
      await recordManagedTradingViewInstallation(selected.id, {
        installed_version_hash: selected.approved_source_sha256,
        workspace_reference: workspace.trim() || undefined,
        alert_reference: alertRef.trim() || undefined,
        symbol, timeframe, installed_at: new Date().toISOString(),
      })
      await refresh()
      toast.add({ title: 'Manual installation recorded.', type: 'success' })
    } catch (reason) { toast.add({ title: messageOf(reason), type: 'error' }) }
  }
  async function provisionCredential(rotate: boolean) {
    if (!selected) return
    try {
      const credential = await generateManagedTradingViewCredential(selected.id, rotate)
      setIssuedToken(credential.token ?? null); setRevealed(false); await refresh()
      toast.add({ title: rotate ? 'Managed credential rotated.' : 'Managed credential generated.', type: 'success' })
    } catch (reason) { toast.add({ title: messageOf(reason), type: 'error' }) }
  }
  async function startVerification() {
    if (!selected) return
    try {
      await startManagedTradingViewVerification(selected.id)
      await refresh()
      toast.add({ title: 'Paper verification started.', type: 'success' })
    } catch (reason) { toast.add({ title: messageOf(reason), type: 'error' }) }
  }
  return <section className="ps-card pine-managed-queue"><div className="ps-card-head"><div><span>Admin only · manual operations</span><h2>Managed TradingView setup</h2></div><span>{setups.length} queued</span></div>{error ? <div className="ps-message error">{error}</div> : null}{setups.length ? <div className="pine-managed-grid"><aside className="ps-list">{setups.map((setup) => <Button variant="unstyled" type="button" className={`ps-list-item${selectedId === setup.id ? ' active' : ''}`} key={setup.id} onClick={() => setSelectedId(setup.id)}><strong>User {setup.user_id.slice(0, 8)}</strong><span>{setup.status.replaceAll('_', ' ')}</span><span>{setup.blocking_step ?? 'No blocking step'}</span></Button>)}</aside>{selected ? <div className="pine-managed-detail"><p><strong>Approved source:</strong> <code>{selected.approved_source_sha256}</code></p><p><strong>Instance:</strong> {selected.strategy_instance_id}</p><p><strong>Credential:</strong> {selected.credential_status}</p><div className="ps-actions"><Button variant="unstyled" className="secondary-button" type="button" onClick={() => void provisionCredential(false)}>{selected.credential_status === 'active' ? 'Credential active' : 'Generate managed credential'}</Button>{selected.credential_status === 'active' ? <Button variant="unstyled" className="ps-danger" type="button" onClick={() => { if (window.confirm('Rotate the managed credential? The current TradingView alert will stop working until re-configured.')) void provisionCredential(true) }}>Rotate</Button> : null}</div>{issuedToken ? <div className="ps-secret-panel"><div><strong>Shown only now</strong><span>Add it to the NOVA-controlled TradingView alert; it is never stored or shown again.</span></div><code>{revealed ? issuedToken : '••••••••••••••••••••••••••••••••'}</code><Button variant="unstyled" type="button" className="secondary-button" onClick={() => setRevealed((value) => !value)}>{revealed ? 'Hide' : 'Reveal'}</Button></div> : null}<p className="ps-note">Never paste this credential into any user-facing field. The non-Premium user never sees it.</p><div className="ps-actions"><Button variant="unstyled" className="secondary-button" type="button" onClick={() => void startVerification()} disabled={selected.credential_status !== 'active' || Boolean(selected.hold_verified_at)}>Start paper verification</Button></div><p className="ps-note">Start verification, then send genuine HOLD, entry and exit alerts from the NOVA-controlled TradingView to complete it.</p><p><strong>Alert verification:</strong> {selected.hold_verified_at ? 'Server confirmed' : 'Pending'}</p><p><strong>Paper verification:</strong> {selected.paper_entry_verified_at && selected.paper_exit_verified_at ? 'Entry and exit confirmed' : 'Pending'}</p><label>Safe workspace label (optional -- auto-filled if left blank)<Input variant="unstyled" value={workspace} maxLength={120} placeholder="Auto-filled from this setup" onChange={(event) => setWorkspace(event.target.value)} /></label><label>Alert reference (optional -- auto-filled if left blank)<Input variant="unstyled" value={alertRef} maxLength={120} placeholder="Auto-filled from the approved version" onChange={(event) => setAlertRef(event.target.value)} /></label><label>Symbol<Input variant="unstyled" value={symbol} maxLength={30} onChange={(event) => setSymbol(event.target.value)} /></label><label>Timeframe<Input variant="unstyled" value={timeframe} maxLength={20} onChange={(event) => setTimeframe(event.target.value)} /></label><Button variant="unstyled" className="ps-primary" type="button" onClick={() => void recordInstallation()}>Record manual installation</Button><small>Never enter a TradingView password, cookie, browser token, or private webhook credential here.</small></div> : null}</div> : <div className="ps-empty-small"><Check size={22} /><strong>No managed setups pending</strong></div>}</section>
}

function messageOf(reason: unknown) { return reason instanceof Error ? reason.message : 'Request failed.' }
