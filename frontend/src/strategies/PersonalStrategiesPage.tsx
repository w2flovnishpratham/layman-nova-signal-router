import {
  AlertTriangle,
  Check,
  ChevronLeft,
  ChevronRight,
  ClipboardCheck,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Pause,
  Play,
  Plus,
  RefreshCw,
  RotateCw,
  Send,
  ShieldCheck,
  Square,
  Trash2,
  Webhook,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { AuthUser } from '../api'
import { ImportedPinePage } from './ImportedPinePage'
import {
  activateStrategyInstance,
  createStrategyInstance,
  generateInstanceWebhookCredential,
  getC2Config,
  getStrategyInstance,
  listInstanceWebhookExecutions,
  listStrategyInstances,
  pauseStrategyInstance,
  resumeStrategyInstance,
  revokeInstanceWebhookCredential,
  rotateInstanceWebhookCredential,
  startInstanceVerification,
  stopStrategyInstance,
  testInstanceWebhookConnection,
  updateStrategyInstanceLots,
  type StrategyInstance,
  type WebhookExecution,
} from '../api'
import { backendHttpUrl } from '../lib/backend'
import { EngineStrategyPicker } from './EngineStrategyPicker'
import { C2MyStrategies } from './C2MyStrategies'
import { blockerText } from './strategyBlockers'
import './personalStrategies.css'

const ACTIONS = ['BUY_CE', 'BUY_PE', 'EXIT', 'HOLD'] as const
const PAGE_SIZE = 10

const READINESS_LABELS: Record<string, string> = {
  paper_mode: 'Paper mode',
  valid_lots: 'Valid lots',
  active_credential: 'Active credential',
  connection_tested: 'Connection tested',
  approved_version: 'Approved Pine version',
  installation_confirmed: 'TradingView installed',
  hold_verified: 'Genuine HOLD verified',
  paper_entry_verified: 'Paper entry verified',
  paper_exit_verified: 'Paper exit verified',
}


export function PersonalStrategiesPage({ user }: { user?: AuthUser }) {
  const [journey, setJourney] = useState<'engine' | 'webhook' | 'pine' | 'c2'>('webhook')
  const [c2Enabled, setC2Enabled] = useState(false)
  const [focusInstanceId, setFocusInstanceId] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    getC2Config().then((value) => {
      if (active) setC2Enabled(value.enabled)
    }).catch(() => undefined)
    return () => { active = false }
  }, [])
  return (
    <div className="ps-page">
      <nav className="ps-journey-tabs" aria-label="Personal strategy type">
        <button type="button" className={journey === 'engine' ? 'active' : ''} onClick={() => setJourney('engine')}>Engine picker</button>
        <button type="button" className={journey === 'webhook' ? 'active' : ''} onClick={() => setJourney('webhook')}>TradingView webhooks</button>
        {c2Enabled ? <button type="button" className={journey === 'c2' ? 'active' : ''} onClick={() => setJourney('c2')}>My Strategies</button> : null}
        <button type="button" className={journey === 'pine' ? 'active' : ''} onClick={() => setJourney('pine')}>Imported Pine scripts</button>
      </nav>
      {journey === 'engine' ? (
        <EngineStrategyPicker onManage={(id) => { setFocusInstanceId(id); setJourney('webhook') }} />
      ) : journey === 'pine' ? (
        <ImportedPinePage isAdmin={Boolean(user?.is_admin)} />
      ) : journey === 'c2' ? (
        <C2MyStrategies />
      ) : (
        <TradingViewStrategiesPage focusInstanceId={focusInstanceId} />
      )}
    </div>
  )
}

function TradingViewStrategiesPage({ focusInstanceId }: { focusInstanceId?: string | null }) {
  const [instances, setInstances] = useState<StrategyInstance[]>([])
  // Seeded from the engine picker's selection; this view remounts on tab switch
  // so no effect is needed to react to a later focus change.
  const [selectedId, setSelectedId] = useState<string | null>(focusInstanceId ?? null)
  const [detail, setDetail] = useState<StrategyInstance | null>(null)
  const [history, setHistory] = useState<WebhookExecution[]>([])
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [newName, setNewName] = useState('')
  const [newLots, setNewLots] = useState(1)
  const [paperAccepted, setPaperAccepted] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [issuedToken, setIssuedToken] = useState<string | null>(null)
  const [revealed, setRevealed] = useState(false)
  const [copied, setCopied] = useState('')
  const [actionFilter, setActionFilter] = useState('ALL')
  const [statusFilter, setStatusFilter] = useState('ALL')
  const [dateFilter, setDateFilter] = useState('')

  const loadList = useCallback(async (preferId?: string) => {
    const rows = (await listStrategyInstances()).filter(
      (row) => row.source_journey === 'PERSONAL_TRADINGVIEW',
    )
    setInstances(rows)
    setSelectedId((current) => preferId ?? current ?? rows[0]?.id ?? null)
    return rows
  }, [])

  const loadSelected = useCallback(async (id: string, pageOffset = offset) => {
    const [instance, page] = await Promise.all([
      getStrategyInstance(id),
      listInstanceWebhookExecutions(id, PAGE_SIZE, pageOffset),
    ])
    setDetail(instance)
    setHistory(page.executions)
    setOffset(page.offset)
  }, [offset])

  useEffect(() => {
    let active = true
    listStrategyInstances()
      .then((rows) => rows.filter((row) => row.source_journey === 'PERSONAL_TRADINGVIEW'))
      .then((rows) => {
        if (!active) return
        setInstances(rows)
        setSelectedId((current) => current ?? rows[0]?.id ?? null)
      })
      .catch((reason: unknown) => {
        if (active) setError(messageOf(reason))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!selectedId) return
    let active = true
    Promise.all([
      getStrategyInstance(selectedId),
      listInstanceWebhookExecutions(selectedId, PAGE_SIZE, 0),
    ])
      .then(([instance, page]) => {
        if (!active) return
        setDetail(instance)
        setHistory(page.executions)
        setOffset(0)
      })
      .catch((reason: unknown) => {
        if (active) setError(messageOf(reason))
      })
    return () => { active = false }
  }, [selectedId])

  useEffect(() => {
    if (!selectedId) return
    const timer = window.setInterval(() => {
      Promise.all([
        getStrategyInstance(selectedId),
        listInstanceWebhookExecutions(selectedId, PAGE_SIZE, offset),
      ]).then(([instance, page]) => {
        setDetail(instance)
        setHistory(page.executions)
      }).catch(() => undefined)
    }, 10000)
    return () => window.clearInterval(timer)
  }, [offset, selectedId])

  const run = useCallback(async (label: string, action: () => Promise<unknown>) => {
    setBusy(label)
    setError('')
    setNotice('')
    try {
      await action()
      setNotice(`${label} completed.`)
    } catch (reason) {
      setError(messageOf(reason))
    } finally {
      setBusy('')
    }
  }, [])

  async function createPersonalStrategy() {
    if (!newName.trim() || !paperAccepted) return
    await run('Strategy creation', async () => {
      const created = await createStrategyInstance({
        strategy_code: 'supertrend',
        source_journey: 'PERSONAL_TRADINGVIEW',
        label: newName.trim(),
        lots: newLots,
        execution_mode: 'paper_live_data',
      })
      await loadList(created.id)
      setShowCreate(false)
      setNewName('')
      setNewLots(1)
      setPaperAccepted(false)
    })
  }

  function selectInstance(id: string) {
    setSelectedId(id)
    setIssuedToken(null)
    setRevealed(false)
    setCopied('')
    setError('')
    setNotice('')
  }

  async function refresh() {
    if (!selectedId) return
    await run('Refresh', async () => {
      await Promise.all([loadList(selectedId), loadSelected(selectedId)])
    })
  }

  async function issueCredential() {
    if (!selectedId) return
    await run('Credential generation', async () => {
      const credential = await generateInstanceWebhookCredential(selectedId)
      setIssuedToken(credential.token ?? null)
      setRevealed(false)
      await loadSelected(selectedId, 0)
      await loadList(selectedId)
    })
  }

  async function rotateCredential() {
    if (!selectedId || !window.confirm('Rotate this credential? Every existing TradingView alert will stop working.')) return
    await run('Credential rotation', async () => {
      const credential = await rotateInstanceWebhookCredential(selectedId)
      setIssuedToken(credential.token ?? null)
      setRevealed(false)
      await loadSelected(selectedId, 0)
    })
  }

  async function revokeCredential() {
    if (!selectedId || !window.confirm('Revoke this credential? Existing alerts will immediately fail.')) return
    await run('Credential revocation', async () => {
      await revokeInstanceWebhookCredential(selectedId)
      setIssuedToken(null)
      setRevealed(false)
      await loadSelected(selectedId, 0)
      await loadList(selectedId)
    })
  }

  async function copyText(label: string, value: string) {
    try {
      if (!navigator.clipboard) throw new Error('Clipboard access is unavailable in this browser.')
      await navigator.clipboard.writeText(value)
      setCopied(label)
      window.setTimeout(() => setCopied(''), 1600)
    } catch (reason) {
      setError(messageOf(reason))
    }
  }

  if (loading) {
    return <PageState icon={<Loader2 className="ps-spin" />} text="Loading personal strategies…" />
  }

  return (
    <div className="ps-page">
      <div className="ps-heading">
        <div>
          <span className="ps-eyebrow"><ShieldCheck size={13} /> Paper mode only</span>
          <h1>Personal TradingView Strategies</h1>
          <p>Create a private route, paste four alert messages, test safely, then activate.</p>
        </div>
        <div className="ps-heading-actions">
          <button type="button" className="secondary-button" onClick={() => void refresh()} disabled={!selectedId || !!busy}>
            <RefreshCw size={14} /> Refresh
          </button>
          <button type="button" className="ps-primary" onClick={() => setShowCreate((open) => !open)}>
            <Plus size={15} /> New strategy
          </button>
        </div>
      </div>

      {error ? <div className="ps-message error" role="alert">{error}</div> : null}
      {notice ? <div className="ps-message success" role="status">{notice}</div> : null}

      {showCreate ? (
        <section className="ps-card ps-create" aria-labelledby="create-strategy-title">
          <div className="ps-card-head">
            <div><span>Step 1</span><h2 id="create-strategy-title">Create strategy</h2></div>
            <span className="ps-paper-badge">PAPER</span>
          </div>
          <div className="ps-form-grid">
            <label>
              Strategy name
              <input autoComplete="off" value={newName} maxLength={120} onChange={(event) => setNewName(event.target.value)} placeholder="My NIFTY strategy" />
            </label>
            <label>
              Lots
              <input type="number" min="1" max="1000" value={newLots} onChange={(event) => setNewLots(Number(event.target.value))} />
            </label>
          </div>
          <label className="ps-check">
            <input type="checkbox" checked={paperAccepted} onChange={(event) => setPaperAccepted(event.target.checked)} />
            I understand this strategy is paper-only and cannot place live orders.
          </label>
          <div className="ps-actions">
            <button type="button" className="ps-primary" disabled={!newName.trim() || !paperAccepted || newLots < 1 || !!busy} onClick={() => void createPersonalStrategy()}>
              {busy === 'Strategy creation' ? <Loader2 className="ps-spin" size={14} /> : <Plus size={14} />} Create strategy
            </button>
            <button type="button" className="secondary-button" onClick={() => setShowCreate(false)}>Cancel</button>
          </div>
        </section>
      ) : null}

      <div className="ps-layout">
        <aside className="ps-list" aria-label="Personal strategies">
          {instances.length ? instances.map((instance) => (
            <button key={instance.id} type="button" className={`ps-list-item${selectedId === instance.id ? ' active' : ''}`} onClick={() => selectInstance(instance.id)}>
              <span className="ps-list-top"><strong>{instance.label}</strong><StatusBadge status={instance.status} /></span>
              <span>{instance.current_lots} lot{instance.current_lots === 1 ? '' : 's'} · Paper</span>
              <span>{instance.credential_status === 'revoked' ? 'Credential revoked' : instance.webhook_credential ? 'Credential active' : 'Needs webhook'}</span>
              <span>Last signal: {formatDate(instance.last_signal_time)}</span>
              <span>Last result: {instance.last_execution_status ? friendlyStatus(instance.last_execution_status) : 'None'} · Created {formatDate(instance.created_at)}</span>
            </button>
          )) : (
            <div className="ps-empty-small"><Webhook size={22} /><strong>No personal strategies</strong><span>Create one to begin.</span></div>
          )}
        </aside>

        <main className="ps-detail">
          {detail ? (
            <StrategyDetail
              key={detail.id}
              detail={detail}
              history={history}
              offset={offset}
              busy={busy}
              issuedToken={issuedToken}
              revealed={revealed}
              copied={copied}
              actionFilter={actionFilter}
              statusFilter={statusFilter}
              dateFilter={dateFilter}
              onReveal={() => setRevealed((value) => !value)}
              onCopy={copyText}
              onIssue={issueCredential}
              onRotate={rotateCredential}
              onRevoke={revokeCredential}
              onLots={async (lots) => {
                await run('Lots update', async () => {
                  await updateStrategyInstanceLots(detail.id, lots)
                  await loadSelected(detail.id, offset)
                  await loadList(detail.id)
                })
              }}
              onTest={async () => {
                await run('Connection test', async () => {
                  await testInstanceWebhookConnection(detail.id)
                  await loadSelected(detail.id, 0)
                  await loadList(detail.id)
                })
              }}
              onStartVerification={async () => {
                await run('Verification', async () => {
                  await startInstanceVerification(detail.id)
                  await loadSelected(detail.id, offset)
                  await loadList(detail.id)
                })
              }}
              onLifecycle={async (action) => {
                await run(action, async () => {
                  if (action === 'Activate') await activateStrategyInstance(detail.id)
                  if (action === 'Resume') await resumeStrategyInstance(detail.id)
                  if (action === 'Pause') await pauseStrategyInstance(detail.id, 'Paused by owner')
                  if (action === 'Stop') await stopStrategyInstance(detail.id, 'Stopped by owner')
                  await loadSelected(detail.id, offset)
                  await loadList(detail.id)
                })
              }}
              onPage={async (nextOffset) => {
                setError('')
                try { await loadSelected(detail.id, nextOffset) } catch (reason) { setError(messageOf(reason)) }
              }}
              onActionFilter={setActionFilter}
              onStatusFilter={setStatusFilter}
              onDateFilter={setDateFilter}
            />
          ) : (
            <div className="ps-card ps-empty"><Webhook size={28} /><h2>Select or create a strategy</h2><p>Your setup steps will appear here.</p></div>
          )}
        </main>
      </div>
    </div>
  )
}

interface DetailProps {
  detail: StrategyInstance
  history: WebhookExecution[]
  offset: number
  busy: string
  issuedToken: string | null
  revealed: boolean
  copied: string
  actionFilter: string
  statusFilter: string
  dateFilter: string
  onReveal: () => void
  onCopy: (label: string, value: string) => Promise<void>
  onIssue: () => Promise<void>
  onRotate: () => Promise<void>
  onRevoke: () => Promise<void>
  onLots: (lots: number) => Promise<void>
  onTest: () => Promise<void>
  onStartVerification: () => Promise<void>
  onLifecycle: (action: 'Activate' | 'Resume' | 'Pause' | 'Stop') => Promise<void>
  onPage: (offset: number) => Promise<void>
  onActionFilter: (value: string) => void
  onStatusFilter: (value: string) => void
  onDateFilter: (value: string) => void
}

function StrategyDetail(props: DetailProps) {
  const { detail } = props
  const [lots, setLots] = useState(detail.current_lots)
  // NOVA-managed (non-Premium): NOVA provisions the credential and configures
  // the TradingView alert. The user never handles the private credential.
  const managed = Boolean(detail.requires_managed_setup)
  const webhookUrl = backendHttpUrl('/api/webhooks/private')
  const displayToken = props.issuedToken && props.revealed ? props.issuedToken : '<PRIVATE_WEBHOOK_CREDENTIAL>'
  const copyToken = props.issuedToken ?? '<PRIVATE_WEBHOOK_CREDENTIAL>'
  const displayTemplates = useMemo(() => templates(displayToken), [displayToken])
  const copyTemplates = useMemo(() => templates(copyToken), [copyToken])
  const filtered = props.history.filter((entry) => {
    if (props.actionFilter !== 'ALL' && entry.action !== props.actionFilter) return false
    const status = executionLabel(entry)
    if (props.statusFilter !== 'ALL' && status !== props.statusFilter) return false
    return !props.dateFilter || (entry.received_at ?? '').startsWith(props.dateFilter)
  })

  function syncLots(value: number) {
    setLots(value)
  }

  const setupText = setupInstructions(webhookUrl, copyTemplates)
  const connectionPassed = Boolean(
    detail.readiness?.connection_tested ?? detail.readiness?.hold_verified,
  )

  return (
    <>
      <section className="ps-card ps-summary">
        <div className="ps-card-head">
          <div><span>Personal TradingView</span><h2>{detail.label}</h2></div>
          <StatusBadge status={detail.status} />
        </div>
        <div className="ps-summary-grid">
          <Summary label="Mode" value="Paper" />
          <Summary label="Lots" value={String(detail.current_lots)} />
          <Summary label="Estimated quantity" value={detail.estimated_quantity ? `${detail.estimated_quantity} contracts` : 'Calculated at entry'} />
          <Summary label="Credential" value={detail.webhook_credential ? 'Active' : 'Not generated'} />
          <Summary label="Connection test" value={connectionPassed ? 'Passed' : 'Required'} />
          <Summary label="Last signal" value={formatDate(detail.last_signal_time)} />
        </div>
        <div className="ps-readiness" aria-label="Activation readiness">
          {Object.entries(detail.readiness ?? {}).filter(([key]) => key !== 'can_activate').map(([key, ready]) => (
            <span key={key} className={ready ? 'ready' : ''}>{ready ? <Check size={12} /> : '○'} {READINESS_LABELS[key] ?? key.replaceAll('_', ' ')}</span>
          ))}
        </div>
        {detail.readiness && !detail.readiness.can_activate ? (
          <p className="ps-note" role="status">Not ready — {blockerText(detail.blocking_code)}.</p>
        ) : null}
        {detail.readiness && 'paper_entry_verified' in detail.readiness && !detail.readiness.can_activate ? (
          detail.verification_mode ? (
            <p className="ps-note" role="status"><strong>Verification in progress.</strong> Send genuine TradingView HOLD, entry and exit signals to complete it. Live orders remain impossible.</p>
          ) : managed ? (
            <p className="ps-note">A NOVA administrator starts and completes verification for managed strategies.</p>
          ) : (
            <button type="button" className="ps-primary" disabled={!!props.busy} onClick={() => void props.onStartVerification()}>Start verification (paper-only)</button>
          )
        ) : null}
      </section>

      <section className="ps-card">
        <StepTitle number="2" title="Configure paper lots" />
        <div className="ps-inline-form">
          <label htmlFor="personal-lots">Lots</label>
          <input id="personal-lots" type="number" min="1" max="1000" value={lots} onChange={(event) => syncLots(Number(event.target.value))} />
          <button type="button" className="secondary-button" disabled={lots < 1 || lots === detail.current_lots || !!props.busy} onClick={() => void props.onLots(lots)}>Save lots</button>
        </div>
        <p className="ps-note">Estimated quantity: {lots} × {detail.lot_size ?? 'current NIFTY lot size'} = {detail.lot_size ? lots * detail.lot_size : 'calculated at entry'}. Changes affect the next entry only; they never resize an open position, and same-side signals do not scale in.</p>
      </section>

      <section className="ps-card">
        <StepTitle number="3" title={managed ? 'Private credential (NOVA-managed)' : 'Generate private webhook'} />
        {managed ? (
          <div className="ps-credential-status"><ShieldCheck size={16} /><span>{detail.webhook_credential ? 'Credential configured by NOVA' : detail.credential_status === 'revoked' ? 'Credential rotated — awaiting NOVA re-verification' : 'Credential provisioning pending with NOVA'}</span></div>
        ) : !detail.webhook_credential ? (
          <button type="button" className="ps-primary" disabled={!!props.busy} onClick={() => void props.onIssue()}><KeyRound size={14} /> Generate credential</button>
        ) : (
          <div className="ps-credential-status"><ShieldCheck size={16} /><span>Credential active · {detail.webhook_credential.token_prefix}…</span></div>
        )}
        {managed ? <p className="ps-note">NOVA installs your approved strategy on a NOVA-controlled TradingView account and never exposes the private credential to you.</p> : null}
        {!managed && props.issuedToken ? (
          <div className="ps-secret-panel">
            <div><strong>Shown only now</strong><span>Update every TradingView alert before leaving this page.</span></div>
            <code>{props.revealed ? props.issuedToken : '••••••••••••••••••••••••••••••••••••••••'}</code>
            <button type="button" className="secondary-button" aria-label={props.revealed ? 'Hide webhook credential' : 'Reveal webhook credential'} onClick={props.onReveal}>
              {props.revealed ? <EyeOff size={14} /> : <Eye size={14} />} {props.revealed ? 'Hide' : 'Reveal'}
            </button>
          </div>
        ) : null}
        {!managed ? (
          <div className="ps-copy-row">
            <code>{webhookUrl}</code>
            <CopyButton label="Webhook URL" copied={props.copied} onClick={() => props.onCopy('Webhook URL', webhookUrl)} />
          </div>
        ) : null}
        {!managed && detail.webhook_credential ? (
          <>
            {detail.has_open_position ? <div className="ps-warning"><AlertTriangle size={16} /><span>Revoking this credential disables TradingView exit signals. NOVA manual and protective exits remain available.</span></div> : null}
            <div className="ps-actions">
              <button type="button" className="secondary-button" disabled={!!props.busy} onClick={() => void props.onRotate()}><RotateCw size={14} /> Rotate</button>
              <button type="button" className="ps-danger" disabled={!!props.busy} onClick={() => void props.onRevoke()}><Trash2 size={14} /> Revoke</button>
            </div>
          </>
        ) : null}
      </section>

      {managed ? (
        <section className="ps-card">
          <StepTitle number="4" title="NOVA configures TradingView" />
          <p className="ps-note">A NOVA administrator installs your approved Pine on a NOVA-controlled TradingView account, creates the private alert, and verifies a genuine HOLD plus a confirmed paper entry and exit. No action is required from you here.</p>
        </section>
      ) : (
      <>
      <section className="ps-card">
        <StepTitle number="4" title="Add TradingView alerts" />
        <div className="ps-warning"><AlertTriangle size={16} /><span>Do not share your credential or JSON alert message. Anyone with it may send signals to this strategy.</span></div>
        <div className="ps-url-note"><strong>Webhook URL</strong><code>{webhookUrl}</code></div>
        <div className="ps-template-grid">
          {ACTIONS.map((action) => (
            <article key={action} className="ps-template">
              <div><strong>{action}</strong><CopyButton label={`${action} JSON`} copied={props.copied} onClick={() => props.onCopy(`${action} JSON`, copyTemplates[action])} /></div>
              <pre>{displayTemplates[action]}</pre>
            </article>
          ))}
        </div>
        <ol className="ps-instructions">
          <li>Open the converted strategy in TradingView.</li><li>Select “Create Alert”.</li>
          <li>Choose the strategy alert condition.</li><li>Paste NOVA’s webhook URL.</li>
          <li>Paste the matching JSON message.</li><li>Save the alert.</li>
          <li>Return to NOVA.</li><li>Send a paper test.</li><li>Activate after the test passes.</li>
        </ol>
        <CopyButton label="Complete setup" copied={props.copied} onClick={() => props.onCopy('Complete setup', setupText)} />
      </section>

      <section className="ps-card">
        <StepTitle number="5" title="Test connection" />
        <p className="ps-note">Sends a server-generated HOLD signal through the same durable ingestion path. HOLD cannot place an order.</p>
        <button type="button" className="ps-primary" disabled={!detail.webhook_credential || !!props.busy || detail.execution_mode === 'real_orders'} onClick={() => void props.onTest()}>
          <Send size={14} /> Send paper HOLD test
        </button>
      </section>
      </>
      )}

      <section className="ps-card">
        <StepTitle number="6" title="Control strategy" />
        <div className="ps-actions">
          {detail.status === 'ready' ? <button type="button" className="ps-primary" disabled={!detail.readiness?.can_activate || !!props.busy} onClick={() => void props.onLifecycle('Activate')}><Play size={14} /> Activate</button> : null}
          {detail.status === 'paused' ? <button type="button" className="ps-primary" disabled={!detail.readiness?.can_activate || !!props.busy} onClick={() => void props.onLifecycle('Resume')}><Play size={14} /> Resume</button> : null}
          {detail.status === 'active' ? <button type="button" className="secondary-button" disabled={!!props.busy} onClick={() => void props.onLifecycle('Pause')}><Pause size={14} /> Pause</button> : null}
          {['active', 'paused'].includes(detail.status) ? <button type="button" className="ps-danger" disabled={!!props.busy} onClick={() => { if (window.confirm('Stop this strategy? Stop is permitted only when the position is flat.')) void props.onLifecycle('Stop') }}><Square size={14} /> Stop</button> : null}
        </div>
        {detail.status === 'paused' ? <p className="ps-note"><strong>New entries are blocked.</strong> Exit signals and NOVA protective exits remain active.</p> : null}
        <p className="ps-note">Stopping is permitted only when flat. Close the open position from Trading first, or pause now to block new entries while keeping exits available. Stop never closes a position automatically.</p>
      </section>

      <section className="ps-card">
        <StepTitle number="7" title="Monitor signals" />
        <div className="ps-filters">
          <label>Action<select value={props.actionFilter} onChange={(event) => props.onActionFilter(event.target.value)}><option>ALL</option>{ACTIONS.map((action) => <option key={action}>{action}</option>)}</select></label>
          <label>Status<select value={props.statusFilter} onChange={(event) => props.onStatusFilter(event.target.value)}><option>ALL</option>{['Received', 'Queued', 'Processing', 'Executed', 'No action needed', 'Rejected', 'Failed'].map((status) => <option key={status}>{status}</option>)}</select></label>
          <label>Date<input type="date" value={props.dateFilter} onChange={(event) => props.onDateFilter(event.target.value)} /></label>
        </div>
        <div className="ps-history-wrap">
          <table className="ps-history">
            <thead><tr><th>Received</th><th>Action</th><th>Status</th><th>Mode</th><th>Contract / quantity</th><th>Signal ID</th></tr></thead>
            <tbody>{filtered.length ? filtered.map((entry) => (
              <tr key={entry.signal_id}>
                <td>{formatDate(entry.received_at)}</td><td>{entry.action ?? '—'}</td>
                <td><span className="ps-history-status">{executionLabel(entry)}</span>{entry.reason ? <small>{safeReason(entry.reason)}</small> : null}</td>
                <td>{entry.execution_mode === 'paper_live_data' ? 'Paper' : entry.execution_mode ?? '—'}</td>
                <td>{contractLabel(entry)}</td><td><code>{entry.signal_id}</code></td>
              </tr>
            )) : <tr><td colSpan={6} className="ps-history-empty">No matching signals.</td></tr>}</tbody>
          </table>
        </div>
        <div className="ps-pagination">
          <button type="button" className="secondary-button" disabled={props.offset === 0} onClick={() => void props.onPage(Math.max(0, props.offset - PAGE_SIZE))}><ChevronLeft size={14} /> Newer</button>
          <span>Page {Math.floor(props.offset / PAGE_SIZE) + 1}</span>
          <button type="button" className="secondary-button" disabled={props.history.length < PAGE_SIZE} onClick={() => void props.onPage(props.offset + PAGE_SIZE)}>Older <ChevronRight size={14} /></button>
        </div>
      </section>
    </>
  )
}

function StepTitle({ number, title }: { number: string; title: string }) {
  return <div className="ps-step-title"><span>{number}</span><h2>{title}</h2></div>
}

function Summary({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>
}

function CopyButton({ label, copied, onClick }: { label: string; copied: string; onClick: () => Promise<void> }) {
  const done = copied === label
  return <button type="button" className="secondary-button" aria-label={`Copy ${label}`} onClick={() => void onClick()}>{done ? <ClipboardCheck size={14} /> : <Copy size={14} />}{done ? 'Copied' : `Copy ${label}`}</button>
}

function StatusBadge({ status }: { status: string }) {
  const labels: Record<string, string> = { draft: 'Draft', ready: 'Needs setup', active: 'Active', paused: 'Paused', stopped: 'Stopped', error: 'Error' }
  return <span className={`ps-status ${status}`}>{labels[status] ?? status.replaceAll('_', ' ')}</span>
}

function PageState({ icon, text }: { icon: ReactNode; text: string }) {
  return <div className="ps-page-state">{icon}<span>{text}</span></div>
}

function templates(credential: string): Record<(typeof ACTIONS)[number], string> {
  return Object.fromEntries(ACTIONS.map((action) => [action, JSON.stringify({
    credential,
    action,
    signal_id: `{{ticker}}-{{interval}}-{{time}}-${action}`,
    signal_time: '{{timenow}}',
  }, null, 2)])) as Record<(typeof ACTIONS)[number], string>
}

function setupInstructions(url: string, kit: Record<(typeof ACTIONS)[number], string>): string {
  return [`NOVA TradingView setup`, `Webhook URL: ${url}`, '', ...ACTIONS.flatMap((action) => [`${action}:`, kit[action], ''])].join('\n')
}

function executionLabel(entry: WebhookExecution): string {
  if (entry.action === 'HOLD' && entry.status === 'completed') return 'No action needed'
  if (entry.job_status === 'failed' || entry.status === 'failed') return 'Failed'
  if (entry.job_status === 'processing') return 'Processing'
  if (entry.job_status === 'queued' || entry.status === 'queued') return 'Queued'
  if (entry.job_status === 'completed' || entry.status === 'completed') return 'Executed'
  if (entry.reason) return 'Rejected'
  return 'Received'
}

function contractLabel(entry: WebhookExecution): string {
  const contract = entry.result?.contract
  if (!contract) return entry.action === 'HOLD' ? 'No order' : '—'
  return `${contract.trading_symbol ?? contract.option_side ?? 'Contract'}${contract.qty ? ` · ${contract.qty} qty` : ''}`
}

function safeReason(reason: string): string {
  const labels: Record<string, string> = {
    STALE_SIGNAL: 'Alert arrived too late', CONFLICTING_DUPLICATE: 'Signal ID was reused with different data',
    INVALID_ACTION: 'Unsupported action', INACTIVE_INSTANCE: 'Strategy is stopped or inactive',
    INSTANCE_PAUSED_ENTRIES_BLOCKED: 'Paused: new entries are blocked',
    STORE_UNAVAILABLE: 'Paper service is temporarily unavailable', INVALID_CREDENTIAL: 'Credential is missing or revoked',
  }
  return labels[reason] ?? reason.replaceAll('_', ' ').toLowerCase()
}

function formatDate(value?: string | null): string {
  return value ? new Date(value).toLocaleString() : 'Never'
}

function friendlyStatus(value: string): string {
  return value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
}

function messageOf(reason: unknown): string {
  return reason instanceof Error ? reason.message : 'Something went wrong. Please try again.'
}
