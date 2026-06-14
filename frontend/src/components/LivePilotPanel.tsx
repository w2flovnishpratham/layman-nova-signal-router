import { CircleCheck, CircleX, Network, Play, RefreshCw, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import {
  approveLivePilot,
  assignExecutor,
  getAdminExecutors,
  getAdminPilotUsers,
  getLiveReadiness,
  startLivePilotStrategy,
  verifyExecutor,
  verifyExecutorAssignment,
} from '../api'
import type {
  ExecutorNodeView,
  LivePilotApproval,
  LiveReadiness,
  PilotUser,
  SideFilter,
} from '../types'

export function LivePilotPanel({ isAdmin }: { isAdmin: boolean }) {
  const [readiness, setReadiness] = useState<LiveReadiness | null>(null)
  const [executors, setExecutors] = useState<ExecutorNodeView[]>([])
  const [users, setUsers] = useState<PilotUser[]>([])
  const [approvals, setApprovals] = useState<LivePilotApproval[]>([])
  const [selectedUser, setSelectedUser] = useState('')
  const [selectedExecutor, setSelectedExecutor] = useState('')
  const [pending, setPending] = useState('')
  const [error, setError] = useState('')
  const [risk, setRisk] = useState({
    maxQty: 1,
    maxDailyLoss: 500,
    maxTradesPerDay: 2,
    allowedOptionSide: 'BOTH' as SideFilter,
  })

  const refresh = useCallback(async () => {
    try {
      const userReadiness = await getLiveReadiness()
      setReadiness(userReadiness)
      if (isAdmin) {
        const [nodes, pilotData] = await Promise.all([getAdminExecutors(), getAdminPilotUsers()])
        setExecutors(nodes)
        setUsers(pilotData.users)
        setApprovals(pilotData.approvals)
      }
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Live pilot data is unavailable.')
    }
  }, [isAdmin])

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0)
    return () => window.clearTimeout(timer)
  }, [refresh])

  async function run(key: string, action: () => Promise<unknown>, success: string) {
    if (pending) return
    setPending(key)
    try {
      await action()
      await refresh()
      toast.success(success)
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Live pilot operation failed.'
      setError(message)
      toast.error(message)
    } finally {
      setPending('')
    }
  }

  return (
    <div className="live-pilot-panel">
      <div className="live-pilot-heading">
        <div>
          <span className="section-kicker">Controlled two-account pilot</span>
          <h3>Live Readiness</h3>
        </div>
        <button type="button" className="icon-button" title="Refresh live readiness" onClick={() => void refresh()}>
          <RefreshCw size={16} />
        </button>
      </div>

      {error ? <div className="strategy-error" role="alert">{error}</div> : null}
      {readiness ? (
        <>
          <div className="readiness-check-grid">
            <ReadinessItem label="Broker connected" ready={readiness.brokerConnected} />
            <ReadinessItem label="Admin approval" ready={readiness.approvalActive} />
            <ReadinessItem label="Executor assigned" ready={readiness.executorAssigned} />
            <ReadinessItem label="Reserved IP verified" ready={readiness.executorVerified} />
            <ReadinessItem label="Dhan IP whitelist" ready={readiness.whitelistStatus === 'verified'} />
            <ReadinessItem label="Risk saved" ready={readiness.riskSaved} />
          </div>
          <div className="live-route-summary">
            <Network size={17} />
            <span>
              {readiness.executorCode ?? 'No executor'} / {readiness.reservedIp ?? 'No reserved IP'}
            </span>
            <strong>{readiness.dryRunOnly ? 'Dry run only' : readiness.liveOrdersEnabled ? 'Real pilot enabled' : 'Real orders blocked'}</strong>
          </div>
          {!readiness.ready ? <p className="live-block-reason">{readiness.reasonMessage}</p> : null}
          <div className="pilot-risk-controls">
            <label>Max quantity<input type="number" min="1" value={risk.maxQty} onChange={(event) => setRisk({ ...risk, maxQty: Number(event.target.value) })} /></label>
            <label>Daily loss<input type="number" min="0" value={risk.maxDailyLoss} onChange={(event) => setRisk({ ...risk, maxDailyLoss: Number(event.target.value) })} /></label>
            <label>Trades per day<input type="number" min="1" value={risk.maxTradesPerDay} onChange={(event) => setRisk({ ...risk, maxTradesPerDay: Number(event.target.value) })} /></label>
            <label>Option side<select value={risk.allowedOptionSide} onChange={(event) => setRisk({ ...risk, allowedOptionSide: event.target.value as SideFilter })}><option value="BOTH">CE and PE</option><option value="CE">CE only</option><option value="PE">PE only</option></select></label>
          </div>
          <button
            type="button"
            className="paper-action"
            disabled={!readiness.ready || pending !== ''}
            onClick={() => void run('subscribe-live', () => startLivePilotStrategy(risk), 'Supertrend live pilot subscription activated.')}
          >
            <Play size={16} /> Start verified dry run
          </button>
        </>
      ) : <div className="strategy-empty">Loading live readiness</div>}

      {isAdmin ? (
        <div className="pilot-admin">
          <div className="pilot-admin-form">
            <h3><ShieldCheck size={17} /> Pilot Approval</h3>
            <select value={selectedUser} onChange={(event) => setSelectedUser(event.target.value)}>
              <option value="">Select user</option>
              {users.map((user) => <option key={user.id} value={user.id}>{user.email}</option>)}
            </select>
            <button
              type="button"
              className="secondary-button"
              disabled={!selectedUser || pending !== ''}
              onClick={() => void run(
                'approve',
                () => approveLivePilot({
                  userId: selectedUser,
                  ...risk,
                  expiresAt: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
                }),
                'Live pilot approval saved.',
              )}
            >
              Approve for 24 hours
            </button>
          </div>
          <div className="pilot-admin-form">
            <h3><Network size={17} /> Executor Assignment</h3>
            <select value={selectedExecutor} onChange={(event) => setSelectedExecutor(event.target.value)}>
              <option value="">Select executor</option>
              {executors.map((node) => <option key={node.id} value={node.id}>{node.executorCode} / {node.reservedIp}</option>)}
            </select>
            <button
              type="button"
              className="secondary-button"
              disabled={!selectedUser || !selectedExecutor || pending !== ''}
              onClick={() => void run('assign', () => assignExecutor(Number(selectedExecutor), selectedUser), 'Executor assigned; verify after Dhan whitelist is complete.')}
            >
              Assign selected user
            </button>
          </div>
          <div className="executor-list">
            {executors.map((node) => (
              <article className="executor-row" key={node.id}>
                <div><strong>{node.executorCode}</strong><span>{node.dropletName} / {node.reservedIp}</span></div>
                <span className={`job-status job-${node.status}`}>{node.status}</span>
                <span>{node.lastHealthStatus ?? 'health pending'} / {node.lastEgressIp ?? 'egress pending'}</span>
                <span>{node.assignment?.userEmail ?? 'Unassigned'} / {node.assignment?.status ?? '-'}</span>
                <div className="executor-actions">
                  <button type="button" className="icon-button" title="Verify health" onClick={() => void run(`health-${node.id}`, () => verifyExecutor(node.id, 'health'), 'Executor health verified.')}><RefreshCw size={15} /></button>
                  <button type="button" className="icon-button" title="Verify egress IP" onClick={() => void run(`egress-${node.id}`, () => verifyExecutor(node.id, 'egress'), 'Executor egress IP verified.')}><Network size={15} /></button>
                  {node.assignment?.status === 'pending_whitelist' ? <button type="button" className="icon-button" title="Confirm Dhan IP whitelist" onClick={() => void run(`verify-${node.assignment?.id}`, () => verifyExecutorAssignment(node.assignment!.id), 'User executor assignment verified.')}><CircleCheck size={15} /></button> : null}
                </div>
              </article>
            ))}
          </div>
          <small>{approvals.filter((approval) => approval.status === 'approved').length} active or recorded pilot approvals.</small>
        </div>
      ) : null}
    </div>
  )
}

function ReadinessItem({ label, ready }: { label: string, ready: boolean }) {
  return <span className={ready ? 'ready' : 'blocked'}>{ready ? <CircleCheck size={15} /> : <CircleX size={15} />}<strong>{label}</strong></span>
}
