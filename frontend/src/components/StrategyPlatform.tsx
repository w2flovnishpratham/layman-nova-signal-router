import {
  Activity,
  BookOpenText,
  BriefcaseBusiness,
  CirclePause,
  CirclePlay,
  Clock3,
  History,
  LockKeyhole,
  RadioTower,
  RefreshCw,
  RotateCcw,
  ServerCog,
  ShieldCheck,
  Store,
  Trash2,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  getStrategies,
  getAdminWorkerQueue,
  getAdminWorkers,
  getPaperLedger,
  getPaperPositions,
  getStrategySignalJobs,
  getStrategySubscriptions,
  pausePaperStrategy,
  removePaperStrategy,
  retryWorkerQueueJob,
  resumePaperStrategy,
  startPaperStrategy,
} from '../api'
import type {
  SideFilter,
  PaperLedgerEntry,
  PaperPosition,
  StrategyCatalogItem,
  StrategySignalJob,
  StrategySubscription,
  WorkerHeartbeat,
  WorkerQueueJob,
} from '../types'
import { ConfirmationDialog } from './ConfirmationDialog'
import { LivePilotPanel } from './LivePilotPanel'

type View = 'marketplace' | 'subscriptions' | 'history' | 'positions' | 'ledger' | 'live' | 'workers'

interface Props {
  refreshNonce: number
  isAdmin: boolean
}

interface RiskDraft {
  maxQty: number
  maxDailyLoss: number
  maxTradesPerDay: number
  allowedOptionSide: SideFilter
}

const DEFAULT_RISK: RiskDraft = {
  maxQty: 65,
  maxDailyLoss: 2000,
  maxTradesPerDay: 3,
  allowedOptionSide: 'BOTH',
}

export function StrategyPlatform({ refreshNonce, isAdmin }: Props) {
  const [view, setView] = useState<View>('marketplace')
  const [strategies, setStrategies] = useState<StrategyCatalogItem[]>([])
  const [subscriptions, setSubscriptions] = useState<StrategySubscription[]>([])
  const [jobs, setJobs] = useState<StrategySignalJob[]>([])
  const [positions, setPositions] = useState<PaperPosition[]>([])
  const [ledger, setLedger] = useState<PaperLedgerEntry[]>([])
  const [workerJobs, setWorkerJobs] = useState<WorkerQueueJob[]>([])
  const [workers, setWorkers] = useState<WorkerHeartbeat[]>([])
  const [queueCounts, setQueueCounts] = useState<Record<string, number>>({})
  const [freeLimit, setFreeLimit] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [pendingKey, setPendingKey] = useState('')
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null)
  const [risk, setRisk] = useState<RiskDraft>(DEFAULT_RISK)
  const [removeTarget, setRemoveTarget] = useState<StrategySubscription | null>(null)

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    try {
      const adminRequests = isAdmin
        ? Promise.all([getAdminWorkerQueue(), getAdminWorkers()])
        : Promise.resolve(null)
      const [catalog, subscriptionData, history, paperPositions, paperLedger, adminData] = await Promise.all([
        getStrategies(),
        getStrategySubscriptions(),
        getStrategySignalJobs(),
        getPaperPositions(),
        getPaperLedger(),
        adminRequests,
      ])
      setStrategies(catalog)
      setSubscriptions(subscriptionData.subscriptions)
      setFreeLimit(subscriptionData.freeActiveLimit)
      setJobs(history)
      setPositions(paperPositions)
      setLedger(paperLedger)
      if (adminData) {
        setQueueCounts(adminData[0].counts)
        setWorkerJobs(adminData[0].jobs)
        setWorkers(adminData[1])
      } else {
        setQueueCounts({})
        setWorkerJobs([])
        setWorkers([])
      }
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Strategy data is unavailable.')
    } finally {
      if (!quiet) setLoading(false)
    }
  }, [isAdmin])

  useEffect(() => {
    const initialTimer = window.setTimeout(() => void refresh(), 0)
    const timer = window.setInterval(() => void refresh(true), 15_000)
    return () => {
      window.clearTimeout(initialTimer)
      window.clearInterval(timer)
    }
  }, [refresh])

  useEffect(() => {
    if (refreshNonce <= 0) return
    const timer = window.setTimeout(() => void refresh(true), 0)
    return () => window.clearTimeout(timer)
  }, [refreshNonce, refresh])

  const activeCount = useMemo(
    () => subscriptions.filter((subscription) => subscription.status === 'active').length,
    [subscriptions],
  )
  const subscriptionByCode = useMemo(
    () => new Map(subscriptions.map((subscription) => [subscription.strategyCode, subscription])),
    [subscriptions],
  )

  async function activate(strategy: StrategyCatalogItem) {
    const key = `activate-${strategy.strategyCode}`
    if (pendingKey) return
    setPendingKey(key)
    try {
      await startPaperStrategy({ strategyCode: strategy.strategyCode, ...risk })
      setSelectedStrategy(null)
      setRisk(DEFAULT_RISK)
      await refresh(true)
      setView('subscriptions')
      toast.success(`${strategy.name} is active in Paper mode.`)
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not start Paper strategy.'
      setError(message)
      toast.error(message)
    } finally {
      setPendingKey('')
    }
  }

  async function updateSubscription(subscription: StrategySubscription, action: 'pause' | 'resume') {
    const key = `${action}-${subscription.id}`
    if (pendingKey) return
    setPendingKey(key)
    try {
      if (action === 'pause') await pausePaperStrategy(subscription.id)
      else await resumePaperStrategy(subscription.id)
      await refresh(true)
      toast.success(`${subscription.strategyName} ${action === 'pause' ? 'paused' : 'resumed'}.`)
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : `Could not ${action} strategy.`
      setError(message)
      toast.error(message)
    } finally {
      setPendingKey('')
    }
  }

  async function removeSubscription() {
    if (!removeTarget || pendingKey) return
    setPendingKey(`remove-${removeTarget.id}`)
    try {
      await removePaperStrategy(removeTarget.id)
      setRemoveTarget(null)
      await refresh(true)
      toast.success('Paper strategy removed.')
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not remove strategy.'
      setError(message)
      toast.error(message)
    } finally {
      setPendingKey('')
    }
  }

  async function retryQueueJob(job: WorkerQueueJob) {
    if (pendingKey) return
    const key = `retry-worker-${job.id}`
    setPendingKey(key)
    try {
      await retryWorkerQueueJob(job.id)
      await refresh(true)
      toast.success(`Worker job ${job.id} was requeued.`)
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not retry worker job.'
      setError(message)
      toast.error(message)
    } finally {
      setPendingKey('')
    }
  }

  return (
    <section className="strategy-platform" aria-label="Paper strategy platform">
      <div className="strategy-platform-heading">
        <div>
          <span className="section-kicker">Paper automation</span>
          <h2>Nova Strategies</h2>
        </div>
        <div className="strategy-heading-actions">
          <span className="plan-limit"><ShieldCheck size={15} /> {activeCount}/{freeLimit} active</span>
          <button
            type="button"
            className="icon-button"
            aria-label="Refresh strategy data"
            title="Refresh strategy data"
            disabled={loading}
            onClick={() => void refresh()}
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      <div className="strategy-tabs" role="tablist" aria-label="Strategy views">
        <TabButton active={view === 'marketplace'} icon={<Store size={16} />} onClick={() => setView('marketplace')}>
          Marketplace
        </TabButton>
        <TabButton active={view === 'subscriptions'} icon={<Activity size={16} />} onClick={() => setView('subscriptions')}>
          My Strategies
        </TabButton>
        <TabButton active={view === 'history'} icon={<History size={16} />} onClick={() => setView('history')}>
          Signal History
        </TabButton>
        <TabButton active={view === 'positions'} icon={<BriefcaseBusiness size={16} />} onClick={() => setView('positions')}>
          Positions
        </TabButton>
        <TabButton active={view === 'ledger'} icon={<BookOpenText size={16} />} onClick={() => setView('ledger')}>
          Ledger
        </TabButton>
        <TabButton active={view === 'live'} icon={<RadioTower size={16} />} onClick={() => setView('live')}>
          Live Pilot
        </TabButton>
        {isAdmin ? (
          <TabButton active={view === 'workers'} icon={<ServerCog size={16} />} onClick={() => setView('workers')}>
            Workers
          </TabButton>
        ) : null}
      </div>

      <p className="plan-message">
        Free plan allows {freeLimit} active Paper strategy. Pause the current strategy before activating another.
      </p>
      {error ? <div className="strategy-error" role="alert">{error}</div> : null}
      {loading ? <div className="strategy-empty"><span className="button-spinner" /> Loading strategy data</div> : null}

      {!loading && view === 'marketplace' ? (
        <div className="strategy-market-grid">
          {strategies.map((strategy) => {
            const subscription = subscriptionByCode.get(strategy.strategyCode)
            const configuring = selectedStrategy === strategy.strategyCode
            const blockedByPlan = activeCount >= freeLimit && subscription?.status !== 'active'
            return (
              <article className="strategy-market-card" key={strategy.strategyCode}>
                <div className="strategy-card-topline">
                  <span className={`risk-chip risk-${strategy.riskLevel}`}>{strategy.riskLevel} risk</span>
                  <span className="timeframe-chip"><Clock3 size={13} /> {strategy.timeframe}</span>
                </div>
                <div>
                  <h3>{strategy.name}</h3>
                  <p>{strategy.description}</p>
                </div>
                <div className="strategy-availability">
                  <span className="paper-available">Paper available</span>
                  <span className="live-locked"><LockKeyhole size={13} /> Live locked</span>
                </div>
                {configuring ? (
                  <div className="strategy-risk-form">
                    <label>
                      Max quantity
                      <input
                        type="number"
                        min="1"
                        value={risk.maxQty}
                        onChange={(event) => setRisk({ ...risk, maxQty: Number(event.target.value) })}
                      />
                    </label>
                    <label>
                      Daily loss limit
                      <input
                        type="number"
                        min="0"
                        value={risk.maxDailyLoss}
                        onChange={(event) => setRisk({ ...risk, maxDailyLoss: Number(event.target.value) })}
                      />
                    </label>
                    <label>
                      Trades per day
                      <input
                        type="number"
                        min="0"
                        value={risk.maxTradesPerDay}
                        onChange={(event) => setRisk({ ...risk, maxTradesPerDay: Number(event.target.value) })}
                      />
                    </label>
                    <label>
                      Option side
                      <select
                        value={risk.allowedOptionSide}
                        onChange={(event) => setRisk({ ...risk, allowedOptionSide: event.target.value as SideFilter })}
                      >
                        <option value="BOTH">CE and PE</option>
                        <option value="CE">CE only</option>
                        <option value="PE">PE only</option>
                      </select>
                    </label>
                    <div className="strategy-form-actions">
                      <button type="button" className="secondary-button" onClick={() => setSelectedStrategy(null)}>
                        Cancel
                      </button>
                      <button
                        type="button"
                        className="paper-action"
                        disabled={pendingKey !== '' || risk.maxQty <= 0}
                        onClick={() => void activate(strategy)}
                      >
                        {pendingKey === `activate-${strategy.strategyCode}` ? <span className="button-spinner" /> : <CirclePlay size={16} />}
                        Start Paper
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="strategy-card-actions">
                    <button
                      type="button"
                      className="paper-action"
                      disabled={Boolean(subscription) || blockedByPlan || pendingKey !== ''}
                      onClick={() => {
                        setError('')
                        setSelectedStrategy(strategy.strategyCode)
                      }}
                    >
                      <CirclePlay size={16} />
                      {subscription ? subscription.status : 'Start Paper'}
                    </button>
                    <button type="button" className="live-lock-button" disabled title="Live execution is not available">
                      <LockKeyhole size={15} /> Live
                    </button>
                  </div>
                )}
                <small>Live execution is locked until executor IP and signing relay are verified.</small>
              </article>
            )
          })}
        </div>
      ) : null}

      {!loading && view === 'subscriptions' ? (
        subscriptions.length ? (
          <div className="subscription-list">
            {subscriptions.map((subscription) => (
              <article className="subscription-row" key={subscription.id}>
                <div className="subscription-identity">
                  <span className={`subscription-status status-${subscription.status}`}>{subscription.status}</span>
                  <h3>{subscription.strategyName}</h3>
                  <p>Paper v{subscription.strategyVersion} / {subscription.risk.allowedOptionSide} / Qty {subscription.risk.maxQty ?? 'not set'}</p>
                </div>
                <div className="subscription-metrics">
                  <Metric label="Signals today" value={subscription.signalsToday} />
                  <Metric label="Paper fills" value={subscription.paperTradesToday} />
                  <Metric label="Skipped" value={subscription.skippedToday} />
                  <Metric label="Last signal" value={subscription.lastSignalAt ? formatTime(subscription.lastSignalAt) : 'None'} />
                </div>
                <div className="subscription-actions">
                  {subscription.status === 'active' ? (
                    <button
                      type="button"
                      className="secondary-button"
                      disabled={pendingKey !== ''}
                      onClick={() => void updateSubscription(subscription, 'pause')}
                    >
                      <CirclePause size={16} /> {pendingKey === `pause-${subscription.id}` ? 'Pausing...' : 'Pause'}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="paper-action"
                      disabled={pendingKey !== ''}
                      onClick={() => void updateSubscription(subscription, 'resume')}
                    >
                      <CirclePlay size={16} /> {pendingKey === `resume-${subscription.id}` ? 'Resuming...' : 'Resume'}
                    </button>
                  )}
                  <button
                    type="button"
                    className="icon-button remove-strategy"
                    aria-label={`Remove ${subscription.strategyName}`}
                    title={`Remove ${subscription.strategyName}`}
                    disabled={pendingKey !== ''}
                    onClick={() => setRemoveTarget(subscription)}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : <div className="strategy-empty">No Paper strategies configured.</div>
      ) : null}

      {!loading && view === 'history' ? (
        jobs.length ? (
          <div className="signal-history" role="table" aria-label="Paper signal history">
            <div className="signal-history-header" role="row">
              <span>Time</span><span>Strategy / signal</span><span>Instrument</span><span>Result</span>
            </div>
            {jobs.map((job) => (
              <article className="signal-history-row" role="row" key={job.id}>
                <time>{formatDateTime(job.createdAt)}</time>
                <div>
                  <strong>{job.strategyName ?? job.strategyCode ?? 'Strategy'}</strong>
                  <span>{job.signal?.action} / {job.signal?.signalId}</span>
                </div>
                <div>
                  <strong>{instrumentLabel(job)}</strong>
                  <span>{job.signal?.expiry ?? 'No expiry'}</span>
                </div>
                <div>
                  <span className={`job-status job-${job.status}`}>{statusLabel(job.status)}</span>
                  <strong>{job.reasonMessage ?? 'No result detail was recorded.'}</strong>
                  {job.paperOrder ? <span>Filled {job.paperOrder.qty} @ {job.paperOrder.fillPrice.toFixed(2)}</span> : null}
                </div>
              </article>
            ))}
          </div>
        ) : <div className="strategy-empty">No strategy signals have been assigned to this user yet.</div>
      ) : null}

      {!loading && view === 'positions' ? (
        positions.length ? (
          <div className="paper-state-table" role="table" aria-label="Paper positions">
            <div className="paper-state-header position-grid" role="row">
              <span>Opened</span><span>Instrument</span><span>Status</span><span>Quantity / entry</span><span>Realized P&amp;L</span>
            </div>
            {positions.map((position) => (
              <article className="paper-state-row position-grid" role="row" key={position.id}>
                <time>{formatDateTime(position.openedAt)}</time>
                <div>
                  <strong>{position.symbol} {position.strike} {position.optionType}</strong>
                  <span>{position.expiry}</span>
                </div>
                <span className={`job-status job-${position.status}`}>{position.status}</span>
                <div>
                  <strong>{position.quantity} @ {position.avgEntryPrice.toFixed(2)}</strong>
                  <span>{position.closedAt ? `Closed ${formatDateTime(position.closedAt)}` : 'Open position'}</span>
                </div>
                <strong className={position.realizedPnl < 0 ? 'negative-value' : 'positive-value'}>
                  {formatMoney(position.realizedPnl)}
                </strong>
              </article>
            ))}
          </div>
        ) : <div className="strategy-empty">No durable Paper positions recorded.</div>
      ) : null}

      {!loading && view === 'ledger' ? (
        ledger.length ? (
          <div className="paper-state-table" role="table" aria-label="Paper ledger">
            <div className="paper-state-header ledger-grid" role="row">
              <span>Time</span><span>Entry</span><span>Quantity / price</span><span>Amount</span><span>Realized P&amp;L</span>
            </div>
            {ledger.map((entry) => (
              <article className="paper-state-row ledger-grid" role="row" key={entry.id}>
                <time>{formatDateTime(entry.createdAt)}</time>
                <div>
                  <strong>{entry.entryType.replaceAll('_', ' ')}</strong>
                  <span>{entry.description}</span>
                </div>
                <strong>{entry.quantity ?? '-'}{entry.price !== null ? ` @ ${entry.price.toFixed(2)}` : ''}</strong>
                <strong>{formatMoney(entry.amount)}</strong>
                <strong className={entry.realizedPnl < 0 ? 'negative-value' : 'positive-value'}>
                  {formatMoney(entry.realizedPnl)}
                </strong>
              </article>
            ))}
          </div>
        ) : <div className="strategy-empty">No durable Paper ledger entries recorded.</div>
      ) : null}

      {!loading && view === 'live' ? <LivePilotPanel isAdmin={isAdmin} /> : null}

      {!loading && view === 'workers' && isAdmin ? (
        <div className="worker-console">
          <div className="queue-summary" aria-label="Worker queue counts">
            {['queued', 'processing', 'failed', 'dead', 'succeeded'].map((status) => (
              <Metric key={status} label={status} value={queueCounts[status] ?? 0} />
            ))}
          </div>
          <div className="worker-heartbeats">
            {workers.length ? workers.map((worker) => (
              <span key={worker.workerId}>
                <strong>{worker.hostname}</strong>
                <small>{worker.role} / {worker.status} / {formatDateTime(worker.lastSeenAt)}</small>
              </span>
            )) : <span><strong>No worker heartbeat</strong><small>The paper worker has not registered yet.</small></span>}
          </div>
          {workerJobs.length ? (
            <div className="paper-state-table" role="table" aria-label="Worker queue">
              <div className="paper-state-header worker-grid" role="row">
                <span>Created</span><span>Job</span><span>Status</span><span>Attempts</span><span>Error</span><span>Action</span>
              </div>
              {workerJobs.map((job) => (
                <article className="paper-state-row worker-grid" role="row" key={job.id}>
                  <time>{formatDateTime(job.createdAt)}</time>
                  <div><strong>#{job.id} {job.jobType}</strong><span>{job.dedupeKey ?? 'No dedupe key'}</span></div>
                  <span className={`job-status job-${job.status}`}>{statusLabel(job.status)}</span>
                  <strong>{job.attemptCount}/{job.maxAttempts}</strong>
                  <span>{job.lastError ?? '-'}</span>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={`Retry worker job ${job.id}`}
                    title="Retry failed worker job"
                    disabled={!['failed', 'dead'].includes(job.status) || pendingKey !== ''}
                    onClick={() => void retryQueueJob(job)}
                  >
                    <RotateCcw size={15} />
                  </button>
                </article>
              ))}
            </div>
          ) : <div className="strategy-empty">The worker queue is empty.</div>}
        </div>
      ) : null}

      <ConfirmationDialog
        open={Boolean(removeTarget)}
        title="Remove Paper strategy?"
        consequence="This disables the subscription. Future central signals will not create jobs or Paper orders for it."
        confirmLabel="Remove strategy"
        confirmPhrase="REMOVE"
        mode="paper"
        affectsRealOrders={false}
        pending={pendingKey.startsWith('remove-')}
        error={error}
        details={removeTarget ? <span>{removeTarget.strategyName} / Paper mode</span> : null}
        onConfirm={() => void removeSubscription()}
        onClose={() => setRemoveTarget(null)}
      />
    </section>
  )
}

function TabButton({
  active,
  icon,
  children,
  onClick,
}: {
  active: boolean
  icon: React.ReactNode
  children: React.ReactNode
  onClick: () => void
}) {
  return (
    <button type="button" role="tab" aria-selected={active} className={active ? 'active' : ''} onClick={onClick}>
      {icon}{children}
    </button>
  )
}

function Metric({ label, value }: { label: string, value: string | number }) {
  return <span><small>{label}</small><strong>{value}</strong></span>
}

function instrumentLabel(job: StrategySignalJob): string {
  const signal = job.signal
  if (!signal) return 'Unavailable'
  const strike = signal.strike ? ` ${signal.strike}` : ''
  const side = signal.optionType ? ` ${signal.optionType}` : ''
  return `${signal.symbol}${strike}${side}`
}

function statusLabel(status: string): string {
  if (status === 'paper_filled') return 'Paper filled'
  if (status === 'paper_rejected') return 'Paper rejected'
  if (status.startsWith('skipped_')) return 'Skipped'
  return status.replaceAll('_', ' ')
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

function formatMoney(value: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 2,
  }).format(value)
}
