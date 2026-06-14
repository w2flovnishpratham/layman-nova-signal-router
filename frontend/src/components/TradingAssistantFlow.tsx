import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { toast } from 'sonner'
import {
  getLiveReadiness,
  getPaperPositions,
  getStrategies,
  getStrategySignalJobs,
  getStrategySubscriptions,
  pausePaperStrategy,
  removePaperStrategy,
  resumePaperStrategy,
  startLivePilotStrategy,
  startPaperStrategy,
} from '../api'
import type {
  EngineMode,
  LiveReadiness,
  PaperPosition,
  SafetyStatus,
  SideFilter,
  StrategyCatalogItem,
  StrategySignalJob,
  StrategySubscription,
} from '../types'
import { BotBubble } from './messages/BotBubble'
import { UserBubble } from './messages/UserBubble'

type Step = 'welcome' | 'choose_mode' | 'choose_strategy' | 'configure_risk' | 'review' | 'running_monitor'

interface ChatItem {
  id: string
  role: 'bot' | 'user'
  text?: string
  node?: ReactNode
  tone?: 'normal' | 'error'
}

interface RiskDraft {
  maxQty: number
  maxTradesPerDay: number
  maxDailyLoss: number
  side: SideFilter
  stopLossPct: number
  targetPct: number
  trailingPct: number
}

const DEFAULT_RISK: RiskDraft = {
  maxQty: 65,
  maxTradesPerDay: 3,
  maxDailyLoss: 2000,
  side: 'BOTH',
  stopLossPct: 30,
  targetPct: 20,
  trailingPct: 0,
}

interface Props {
  refreshNonce: number
  safety: SafetyStatus | null
  onOpenOperatorConsole?: () => void
}

let chatSeq = 0
function nextId(): string {
  chatSeq += 1
  return `assist-${chatSeq}-${Date.now()}`
}

export function TradingAssistantFlow({ refreshNonce, safety, onOpenOperatorConsole }: Props) {
  const [step, setStep] = useState<Step>('welcome')
  const [messages, setMessages] = useState<ChatItem[]>([])
  const [strategies, setStrategies] = useState<StrategyCatalogItem[]>([])
  const [subscriptions, setSubscriptions] = useState<StrategySubscription[]>([])
  const [freeLimit, setFreeLimit] = useState(1)
  const [jobs, setJobs] = useState<StrategySignalJob[]>([])
  const [positions, setPositions] = useState<PaperPosition[]>([])
  const [readiness, setReadiness] = useState<LiveReadiness | null>(null)
  const [mode, setMode] = useState<EngineMode | null>(null)
  const [strategyCode, setStrategyCode] = useState<string | null>(null)
  const [risk, setRisk] = useState<RiskDraft>(DEFAULT_RISK)
  const [pending, setPending] = useState(false)
  const [booted, setBooted] = useState(false)
  const logRef = useRef<HTMLDivElement | null>(null)
  const greetedRef = useRef(false)

  const pushBot = useCallback((text: string, tone: 'normal' | 'error' = 'normal') => {
    setMessages((current) => [...current, { id: nextId(), role: 'bot', text, tone }])
  }, [])
  const pushUser = useCallback((text: string) => {
    setMessages((current) => [...current, { id: nextId(), role: 'user', text }])
  }, [])

  const loadData = useCallback(async (quiet = true) => {
    try {
      const [catalog, subs, history, paperPositions] = await Promise.all([
        getStrategies(),
        getStrategySubscriptions(),
        getStrategySignalJobs(),
        getPaperPositions(),
      ])
      setStrategies(catalog)
      setSubscriptions(subs.subscriptions)
      setFreeLimit(subs.freeActiveLimit)
      setJobs(history)
      setPositions(paperPositions)
      try {
        setReadiness(await getLiveReadiness())
      } catch {
        setReadiness(null)
      }
    } catch (cause) {
      if (!quiet) pushBot(cause instanceof Error ? cause.message : 'Could not load your strategies.', 'error')
    }
  }, [pushBot])

  // Initial bootstrap: load data, then greet or resume into the monitor.
  useEffect(() => {
    let active = true
    void (async () => {
      await loadData(false)
      if (!active) return
      setBooted(true)
    })()
    return () => { active = false }
  }, [loadData])

  useEffect(() => {
    if (!booted || greetedRef.current) return
    greetedRef.current = true
    const activeSubs = subscriptions.filter((sub) => sub.status === 'active')
    if (activeSubs.length > 0) {
      pushBot('Welcome back. Your strategy is running — here is the latest status.')
      setStep('running_monitor')
    } else {
      pushBot('Hi, I am your Nova trading assistant. I will help you start a strategy in a few steps.')
      pushBot('Do you want to run Paper mode (simulated, no real money) or Live mode (real orders, policy-gated)?')
      setStep('choose_mode')
    }
  }, [booted, subscriptions, pushBot])

  // Refresh data while monitoring (interval + server-event nonce).
  useEffect(() => {
    if (step !== 'running_monitor') return
    const timer = window.setInterval(() => void loadData(true), 8000)
    return () => window.clearInterval(timer)
  }, [step, loadData])

  useEffect(() => {
    if (step === 'running_monitor' && refreshNonce > 0) void loadData(true)
  }, [refreshNonce, step, loadData])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, step])

  const liveBlockers = useMemo(() => buildLiveBlockers(readiness, safety), [readiness, safety])
  const liveReady = Boolean(readiness?.ready) && liveBlockers.length === 0
  const activeCount = useMemo(
    () => subscriptions.filter((sub) => sub.status === 'active').length,
    [subscriptions],
  )

  function chooseMode(next: EngineMode) {
    setMode(next)
    pushUser(next === 'paper' ? 'Paper mode' : 'Live mode')
    if (next === 'live') {
      pushBot('Live mode places real orders and is locked until your broker, a verified execution IP, the signing relay, and an operator approval are all ready. You can still review it now.')
      if (liveBlockers.length > 0) {
        pushBot(`Right now Live is locked because: ${liveBlockers.join('; ')}.`, 'error')
      }
      // Live pilot supports SUPERTREND_FLIP only.
      pushBot('For Live, the controlled pilot supports Supertrend Flip. Pick a strategy to continue.')
    } else {
      if (activeCount >= freeLimit) {
        pushBot(`Your plan allows ${freeLimit} active Paper strategy at a time. You can still review, but you may need to pause an existing one before starting another.`)
      }
      pushBot('Great. Which strategy would you like to run?')
    }
    setStep('choose_strategy')
  }

  function chooseStrategy(item: StrategyCatalogItem) {
    setStrategyCode(item.strategyCode)
    pushUser(item.name)
    pushBot(`${item.name}: ${item.description}`)
    pushBot('Now set your risk limits. These caps are enforced on every order.')
    setStep('configure_risk')
  }

  function submitRisk() {
    pushUser(`Qty ${risk.maxQty} / ${risk.maxTradesPerDay} trades per day / max loss ${risk.maxDailyLoss} / ${risk.side}`)
    pushBot('Here is your setup. Review and start when ready.')
    setStep('review')
  }

  async function startStrategy() {
    if (pending || !strategyCode || !mode) return
    setPending(true)
    try {
      if (mode === 'paper') {
        const sub = await startPaperStrategy({
          strategyCode,
          maxQty: risk.maxQty,
          maxDailyLoss: risk.maxDailyLoss,
          maxTradesPerDay: risk.maxTradesPerDay,
          allowedOptionSide: risk.side,
        })
        pushUser('Start Paper')
        pushBot(`${sub.strategyName} is now running in Paper mode. I will watch for signals and show fills, skips, and positions here.`)
        toast.success(`${sub.strategyName} is active in Paper mode.`)
      } else {
        const sub = await startLivePilotStrategy({
          maxQty: risk.maxQty,
          maxDailyLoss: risk.maxDailyLoss,
          maxTradesPerDay: risk.maxTradesPerDay,
          allowedOptionSide: risk.side,
        })
        pushUser('Start Live')
        pushBot(`${sub.strategyName} live subscription created. Live orders only fire once every readiness gate passes.`)
        toast.success('Live pilot subscription created.')
      }
      await loadData(true)
      setStep('running_monitor')
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not start the strategy.'
      pushBot(message, 'error')
      toast.error(message)
    } finally {
      setPending(false)
    }
  }

  async function subscriptionAction(sub: StrategySubscription, action: 'pause' | 'resume' | 'remove') {
    if (pending) return
    setPending(true)
    try {
      if (action === 'pause') {
        await pausePaperStrategy(sub.id)
        pushBot(`${sub.strategyName} paused. No new entries until you resume.`)
      } else if (action === 'resume') {
        await resumePaperStrategy(sub.id)
        pushBot(`${sub.strategyName} resumed. Watching for signals again.`)
      } else {
        await removePaperStrategy(sub.id)
        pushBot(`${sub.strategyName} removed.`)
      }
      await loadData(true)
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not update the strategy.'
      pushBot(message, 'error')
      toast.error(message)
    } finally {
      setPending(false)
    }
  }

  function restart() {
    setMode(null)
    setStrategyCode(null)
    setRisk(DEFAULT_RISK)
    pushBot('Let us set up another strategy. Paper or Live?')
    setStep('choose_mode')
  }

  const selectableStrategies = useMemo(() => {
    if (mode === 'live') return strategies.filter((item) => item.liveAllowed)
    return strategies.filter((item) => item.paperAllowed)
  }, [strategies, mode])

  return (
    <section className="assistant-shell" aria-label="Nova trading assistant">
      <div className="assistant-log" ref={logRef}>
        {messages.map((item) =>
          item.role === 'user'
            ? <UserBubble key={item.id} text={item.text ?? ''} />
            : <BotBubble key={item.id} tone={item.tone}>{item.node ?? <p>{item.text}</p>}</BotBubble>,
        )}
      </div>

      <div className="assistant-control">
        {step === 'choose_mode' ? (
          <div className="choice-grid" role="group" aria-label="Choose mode">
            <button type="button" className="paper-action" onClick={() => chooseMode('paper')}>Paper mode</button>
            <button type="button" className="secondary-button" onClick={() => chooseMode('live')}>Live mode</button>
          </div>
        ) : null}

        {step === 'choose_strategy' ? (
          <div className="assistant-strategy-grid" role="group" aria-label="Choose strategy">
            {selectableStrategies.length === 0
              ? <p className="assistant-note">No strategies are available for this mode yet.</p>
              : selectableStrategies.map((item) => (
                <button
                  type="button"
                  key={item.strategyCode}
                  className="assistant-strategy-card"
                  onClick={() => chooseStrategy(item)}
                >
                  <span className={`risk-chip risk-${item.riskLevel}`}>{item.riskLevel} risk</span>
                  <strong>{item.name}</strong>
                  <small>{item.timeframe} · {item.market}</small>
                </button>
              ))}
            <button type="button" className="assistant-link" onClick={() => { setStep('choose_mode'); pushBot('Paper or Live?') }}>
              ← Change mode
            </button>
          </div>
        ) : null}

        {step === 'configure_risk' ? (
          <RiskForm risk={risk} setRisk={setRisk} onSubmit={submitRisk} />
        ) : null}

        {step === 'review' ? (
          <ReviewCard
            mode={mode}
            strategyName={strategies.find((s) => s.strategyCode === strategyCode)?.name ?? strategyCode ?? ''}
            risk={risk}
            liveBlockers={liveBlockers}
            liveReady={liveReady}
            pending={pending}
            onBack={() => setStep('configure_risk')}
            onStart={() => void startStrategy()}
          />
        ) : null}

        {step === 'running_monitor' ? (
          <MonitorPanel
            subscriptions={subscriptions}
            jobs={jobs}
            positions={positions}
            liveBlockers={liveBlockers}
            pending={pending}
            onAction={(sub, action) => void subscriptionAction(sub, action)}
            onRestart={restart}
          />
        ) : null}
      </div>

      {onOpenOperatorConsole ? (
        <button type="button" className="assistant-operator-link" onClick={onOpenOperatorConsole}>
          Operator console
        </button>
      ) : null}
    </section>
  )
}

function RiskForm({ risk, setRisk, onSubmit }: { risk: RiskDraft, setRisk: (r: RiskDraft) => void, onSubmit: () => void }) {
  return (
    <div className="assistant-risk-form">
      <label>
        Quantity per order
        <input type="number" min="1" value={risk.maxQty} onChange={(e) => setRisk({ ...risk, maxQty: Number(e.target.value) })} />
        <small>65 = 1 NIFTY lot</small>
      </label>
      <label>
        Max trades per day
        <input type="number" min="1" value={risk.maxTradesPerDay} onChange={(e) => setRisk({ ...risk, maxTradesPerDay: Number(e.target.value) })} />
      </label>
      <label>
        Max daily loss (₹)
        <input type="number" min="0" value={risk.maxDailyLoss} onChange={(e) => setRisk({ ...risk, maxDailyLoss: Number(e.target.value) })} />
      </label>
      <label>
        Option side
        <select value={risk.side} onChange={(e) => setRisk({ ...risk, side: e.target.value as SideFilter })}>
          <option value="BOTH">CE and PE</option>
          <option value="CE">CE only</option>
          <option value="PE">PE only</option>
        </select>
      </label>
      <label>
        Stop loss %
        <input type="number" min="0" value={risk.stopLossPct} onChange={(e) => setRisk({ ...risk, stopLossPct: Number(e.target.value) })} />
      </label>
      <label>
        Target %
        <input type="number" min="0" value={risk.targetPct} onChange={(e) => setRisk({ ...risk, targetPct: Number(e.target.value) })} />
      </label>
      <label>
        Trailing stop % (0 = off)
        <input type="number" min="0" value={risk.trailingPct} onChange={(e) => setRisk({ ...risk, trailingPct: Number(e.target.value) })} />
      </label>
      <p className="assistant-note">
        Quantity, trades/day, daily loss and option side are enforced as hard caps per order. Stop-loss, target and trailing are applied by the strategy engine.
      </p>
      <button type="button" className="paper-action" disabled={risk.maxQty <= 0 || risk.maxTradesPerDay <= 0} onClick={onSubmit}>
        Review setup
      </button>
    </div>
  )
}

function ReviewCard({
  mode, strategyName, risk, liveBlockers, liveReady, pending, onBack, onStart,
}: {
  mode: EngineMode | null
  strategyName: string
  risk: RiskDraft
  liveBlockers: string[]
  liveReady: boolean
  pending: boolean
  onBack: () => void
  onStart: () => void
}) {
  const isLive = mode === 'live'
  const startDisabled = pending || (isLive && !liveReady)
  return (
    <div className="assistant-summary">
      <h3>Review</h3>
      <dl>
        <div><dt>Mode</dt><dd>{isLive ? 'Live (real money)' : 'Paper (simulated)'}</dd></div>
        <div><dt>Strategy</dt><dd>{strategyName}</dd></div>
        <div><dt>Quantity</dt><dd>{risk.maxQty}</dd></div>
        <div><dt>Max trades/day</dt><dd>{risk.maxTradesPerDay}</dd></div>
        <div><dt>Max daily loss</dt><dd>₹{risk.maxDailyLoss}</dd></div>
        <div><dt>Option side</dt><dd>{risk.side}</dd></div>
        <div><dt>Stop loss / Target</dt><dd>{risk.stopLossPct}% / {risk.targetPct}%{risk.trailingPct > 0 ? ` · trail ${risk.trailingPct}%` : ''}</dd></div>
      </dl>
      {isLive && liveBlockers.length > 0 ? (
        <div className="assistant-blockers" role="alert">
          <strong>Live is locked:</strong>
          <ul>{liveBlockers.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        </div>
      ) : null}
      <div className="assistant-form-actions">
        <button type="button" className="secondary-button" onClick={onBack} disabled={pending}>Back</button>
        <button type="button" className="paper-action" onClick={onStart} disabled={startDisabled}>
          {pending ? <span className="button-spinner" /> : null}
          {isLive ? 'Start Live' : 'Start Paper'}
        </button>
      </div>
      {isLive && !liveReady ? <p className="assistant-note">Resolve the blockers above to enable Start Live. Paper mode is always available.</p> : null}
    </div>
  )
}

function MonitorPanel({
  subscriptions, jobs, positions, liveBlockers, pending, onAction, onRestart,
}: {
  subscriptions: StrategySubscription[]
  jobs: StrategySignalJob[]
  positions: PaperPosition[]
  liveBlockers: string[]
  pending: boolean
  onAction: (sub: StrategySubscription, action: 'pause' | 'resume' | 'remove') => void
  onRestart: () => void
}) {
  const openPositions = positions.filter((p) => p.status === 'open')
  const recentJobs = jobs.slice(0, 4)
  if (subscriptions.length === 0) {
    return (
      <div className="assistant-summary">
        <p>No strategy is running right now.</p>
        <button type="button" className="paper-action" onClick={onRestart}>Set up a strategy</button>
      </div>
    )
  }
  return (
    <div className="assistant-monitor">
      {subscriptions.map((sub) => {
        const lastSignal = sub.lastSignalAt ? formatTime(sub.lastSignalAt) : 'None yet'
        return (
          <article className="assistant-monitor-card" key={sub.id}>
            <header>
              <div>
                <span className={`subscription-status status-${sub.status}`}>{sub.status}</span>
                <strong>{sub.strategyName}</strong>
                <small>{sub.mode === 'live' ? 'Live' : 'Paper'} · {sub.risk.allowedOptionSide} · qty {sub.risk.maxQty ?? '—'}</small>
              </div>
              <div className="assistant-monitor-actions">
                {sub.status === 'active'
                  ? <button type="button" className="secondary-button" disabled={pending} onClick={() => onAction(sub, 'pause')}>Pause</button>
                  : <button type="button" className="paper-action" disabled={pending} onClick={() => onAction(sub, 'resume')}>Resume</button>}
                <button type="button" className="assistant-link" disabled={pending} onClick={() => onAction(sub, 'remove')}>Remove</button>
              </div>
            </header>
            <div className="assistant-monitor-metrics">
              <span><small>Last signal</small><strong>{lastSignal}</strong></span>
              <span><small>Signals today</small><strong>{sub.signalsToday}</strong></span>
              <span><small>Fills today</small><strong>{sub.paperTradesToday}</strong></span>
              <span><small>Skipped</small><strong>{sub.skippedToday}</strong></span>
            </div>
            {sub.mode === 'live' && liveBlockers.length > 0 ? (
              <div className="assistant-blockers" role="status">
                <strong>Live locked:</strong> {liveBlockers.join('; ')}
              </div>
            ) : null}
          </article>
        )
      })}

      {openPositions.length > 0 ? (
        <div className="assistant-monitor-section">
          <h4>Open position</h4>
          {openPositions.map((p) => (
            <p key={p.id} className="assistant-position">
              {p.symbol} {p.strike} {p.optionType} · {p.quantity} @ {p.avgEntryPrice.toFixed(2)}
            </p>
          ))}
        </div>
      ) : null}

      <div className="assistant-monitor-section">
        <h4>Recent signals</h4>
        {recentJobs.length === 0
          ? <p className="assistant-note">No signals routed to you yet. The bot is watching.</p>
          : recentJobs.map((job) => (
            <p key={job.id} className="assistant-history-row">
              <span className={`job-status job-${job.status}`}>{statusLabel(job.status)}</span>
              {' '}{job.signal ? `${job.signal.action} ${job.signal.symbol}${job.signal.strike ? ` ${job.signal.strike}` : ''}${job.signal.optionType ? ` ${job.signal.optionType}` : ''}` : 'Signal'}
              {job.reasonMessage ? ` — ${job.reasonMessage}` : ''}
            </p>
          ))}
      </div>

      <button type="button" className="assistant-link" onClick={onRestart}>+ Set up another strategy</button>
    </div>
  )
}

function buildLiveBlockers(readiness: LiveReadiness | null, safety: SafetyStatus | null): string[] {
  const blockers: string[] = []
  if (!readiness) {
    blockers.push('Live readiness is unavailable')
    return blockers
  }
  if (!readiness.brokerConnected) blockers.push('Broker (Dhan) not connected')
  if (!readiness.riskSaved) blockers.push('Risk limits not saved')
  if (!readiness.executorAssigned) blockers.push('Execution IP not assigned')
  if (!readiness.executorVerified) blockers.push('Executor IP not verified')
  if (!readiness.approvalActive) blockers.push('Operator live approval missing or expired')
  if (safety && !safety.signing_relay_configured) blockers.push('Signing relay not configured')
  if (readiness.dryRunOnly) blockers.push('Live is in dry-run-only mode (operator)')
  if (!readiness.liveOrdersEnabled) blockers.push('Live trading disabled by operator')
  if (readiness.reasonMessage && blockers.length === 0) blockers.push(readiness.reasonMessage)
  return blockers
}

function statusLabel(status: string): string {
  if (status === 'paper_filled') return 'Filled'
  if (status === 'paper_rejected') return 'Rejected'
  if (status.startsWith('skipped_')) return 'Skipped'
  return status.replaceAll('_', ' ')
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}
