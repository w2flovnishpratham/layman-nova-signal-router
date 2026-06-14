import type {
  AuthStatus,
  PaperLedgerEntry,
  PaperPosition,
  LiveReadiness,
  ExecutorNodeView,
  PilotUser,
  LivePilotApproval,
  SafetyStatus,
  SessionBootstrap,
  SessionSnapshot,
  SideFilter,
  StrategyCatalogItem,
  StrategySignalJob,
  StrategySubscription,
  WorkerHeartbeat,
  WorkerQueueJob,
} from './types'
import { apiFetch, backendHttpUrl, setCsrfToken } from './lib/backend'

export async function getAuthStatus(): Promise<AuthStatus> {
  const response = await apiFetch('/api/auth/status', { cache: 'no-store' })
  if (!response.ok) {
    throw new Error(`Could not load auth status: ${response.status}`)
  }
  const status = await response.json() as AuthStatus
  setCsrfToken(status.csrfToken)
  return status
}

export async function startSession(): Promise<SessionBootstrap> {
  const response = await apiFetch('/api/session/start', { method: 'POST' })
  if (!response.ok) {
    throw new Error(`Could not start session: ${response.status}`)
  }
  return response.json() as Promise<SessionBootstrap>
}

export async function getSession(sessionId: string): Promise<SessionSnapshot> {
  const response = await apiFetch(`/api/session/${sessionId}`)
  if (!response.ok) {
    throw new Error(`Could not load session: ${response.status}`)
  }
  return response.json() as Promise<SessionSnapshot>
}

export async function getSafetyStatus(): Promise<SafetyStatus> {
  const response = await apiFetch('/api/safety/status', { cache: 'no-store' })
  if (!response.ok) {
    throw new Error(await safeApiError(response, 'Could not load safety status'))
  }
  return response.json() as Promise<SafetyStatus>
}

export async function prepareReconfigure(): Promise<void> {
  const response = await apiFetch('/api/engine/reconfigure', { method: 'POST' })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string | { message?: string } } | null
    const detail = body?.detail
    const message = typeof detail === 'string' ? detail : detail?.message
    throw new Error(message || `Could not reconfigure: ${response.status}`)
  }
}

export async function logout(): Promise<void> {
  await apiFetch('/api/auth/logout', { method: 'POST' })
  setCsrfToken(null)
}

export async function getStrategies(): Promise<StrategyCatalogItem[]> {
  const response = await apiFetch('/api/strategies', { cache: 'no-store' })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not load strategies'))
  const body = await response.json() as { strategies: StrategyCatalogItem[] }
  return body.strategies
}

export async function getStrategySubscriptions(): Promise<{
  freeActiveLimit: number
  subscriptions: StrategySubscription[]
}> {
  const response = await apiFetch('/api/me/strategy-subscriptions', { cache: 'no-store' })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not load strategy subscriptions'))
  return response.json() as Promise<{
    freeActiveLimit: number
    subscriptions: StrategySubscription[]
  }>
}

export async function startPaperStrategy(input: {
  strategyCode: string
  maxQty: number
  maxDailyLoss: number
  maxTradesPerDay: number
  allowedOptionSide: SideFilter
}): Promise<StrategySubscription> {
  const response = await apiFetch('/api/me/strategy-subscriptions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      strategy_code: input.strategyCode,
      mode: 'paper',
      max_qty: input.maxQty,
      max_daily_loss: input.maxDailyLoss,
      max_trades_per_day: input.maxTradesPerDay,
      allowed_option_side: input.allowedOptionSide,
    }),
  })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not start Paper strategy'))
  const body = await response.json() as { subscription: StrategySubscription }
  return body.subscription
}

export async function startLivePilotStrategy(input: {
  maxQty: number
  maxDailyLoss: number
  maxTradesPerDay: number
  allowedOptionSide: SideFilter
}): Promise<StrategySubscription> {
  const response = await apiFetch('/api/me/strategy-subscriptions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      strategy_code: 'SUPERTREND_FLIP',
      mode: 'live',
      max_qty: input.maxQty,
      max_daily_loss: input.maxDailyLoss,
      max_trades_per_day: input.maxTradesPerDay,
      allowed_option_side: input.allowedOptionSide,
    }),
  })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not start live pilot strategy'))
  const body = await response.json() as { subscription: StrategySubscription }
  return body.subscription
}

export async function getLiveReadiness(): Promise<LiveReadiness> {
  const response = await apiFetch('/api/me/live-readiness?strategy_code=SUPERTREND_FLIP', { cache: 'no-store' })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not load live readiness'))
  return response.json() as Promise<LiveReadiness>
}

export async function getAdminExecutors(): Promise<ExecutorNodeView[]> {
  const response = await apiFetch('/api/admin/executors', { cache: 'no-store' })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not load executors'))
  const body = await response.json() as { executors: ExecutorNodeView[] }
  return body.executors
}

export async function getAdminPilotUsers(): Promise<{
  users: PilotUser[]
  approvals: LivePilotApproval[]
}> {
  const response = await apiFetch('/api/admin/live-pilot/users', { cache: 'no-store' })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not load pilot users'))
  return response.json() as Promise<{ users: PilotUser[], approvals: LivePilotApproval[] }>
}

export async function verifyExecutor(executorId: number, check: 'health' | 'egress'): Promise<void> {
  const response = await apiFetch(`/api/admin/executors/${executorId}/verify-${check}`, { method: 'POST' })
  if (!response.ok) throw new Error(await safeApiError(response, `Could not verify executor ${check}`))
}

export async function assignExecutor(executorId: number, userId: string): Promise<void> {
  const response = await apiFetch(`/api/admin/executors/${executorId}/assign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId }),
  })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not assign executor'))
}

export async function verifyExecutorAssignment(assignmentId: number): Promise<void> {
  const response = await apiFetch(`/api/admin/executor-assignments/${assignmentId}/verify`, { method: 'POST' })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not verify executor assignment'))
}

export async function approveLivePilot(input: {
  userId: string
  maxQty: number
  maxDailyLoss: number
  maxTradesPerDay: number
  allowedOptionSide: SideFilter
  expiresAt: string
}): Promise<void> {
  const response = await apiFetch('/api/admin/live-pilot/approvals', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: input.userId,
      strategy_code: 'SUPERTREND_FLIP',
      max_qty: input.maxQty,
      max_daily_loss: input.maxDailyLoss,
      max_trades_per_day: input.maxTradesPerDay,
      allowed_option_side: input.allowedOptionSide,
      expires_at: input.expiresAt,
    }),
  })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not approve live pilot user'))
}

export async function pausePaperStrategy(subscriptionId: number): Promise<StrategySubscription> {
  return updatePaperSubscription(`/api/me/strategy-subscriptions/${subscriptionId}/pause`)
}

export async function resumePaperStrategy(subscriptionId: number): Promise<StrategySubscription> {
  return updatePaperSubscription(`/api/me/strategy-subscriptions/${subscriptionId}/resume`)
}

export async function removePaperStrategy(subscriptionId: number): Promise<void> {
  const response = await apiFetch(`/api/me/strategy-subscriptions/${subscriptionId}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not remove Paper strategy'))
}

export async function getStrategySignalJobs(): Promise<StrategySignalJob[]> {
  const response = await apiFetch('/api/me/signal-jobs?limit=100', { cache: 'no-store' })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not load signal history'))
  const body = await response.json() as { jobs: StrategySignalJob[] }
  return body.jobs
}

export async function getPaperPositions(): Promise<PaperPosition[]> {
  const response = await apiFetch('/api/me/paper-positions?include_closed=true&limit=100', { cache: 'no-store' })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not load Paper positions'))
  const body = await response.json() as { positions: PaperPosition[] }
  return body.positions
}

export async function getPaperLedger(): Promise<PaperLedgerEntry[]> {
  const response = await apiFetch('/api/me/paper-ledger?limit=200', { cache: 'no-store' })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not load Paper ledger'))
  const body = await response.json() as { entries: PaperLedgerEntry[] }
  return body.entries
}

export async function getAdminWorkerQueue(): Promise<{
  counts: Record<string, number>
  jobs: WorkerQueueJob[]
}> {
  const response = await apiFetch('/api/admin/worker-queue?limit=100', { cache: 'no-store' })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not load worker queue'))
  return response.json() as Promise<{ counts: Record<string, number>, jobs: WorkerQueueJob[] }>
}

export async function getAdminWorkers(): Promise<WorkerHeartbeat[]> {
  const response = await apiFetch('/api/admin/workers', { cache: 'no-store' })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not load workers'))
  const body = await response.json() as { workers: WorkerHeartbeat[] }
  return body.workers
}

export async function retryWorkerQueueJob(workerJobId: number): Promise<WorkerQueueJob> {
  const response = await apiFetch(`/api/admin/worker-queue/${workerJobId}/retry`, { method: 'POST' })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not retry worker job'))
  const body = await response.json() as { job: WorkerQueueJob }
  return body.job
}

export function googleLoginUrl(): string {
  return backendHttpUrl('/api/auth/google')
}

async function safeApiError(response: Response, fallback: string): Promise<string> {
  if (response.status === 401) return 'Session expired. Please sign in again.'
  if (response.status === 403) return 'Your session safety token expired. Refresh and try again.'
  const body = await response.json().catch(() => null) as { detail?: unknown } | null
  return typeof body?.detail === 'string' ? body.detail : `${fallback}: ${response.status}`
}

async function updatePaperSubscription(path: `/${string}`): Promise<StrategySubscription> {
  const response = await apiFetch(path, { method: 'POST' })
  if (!response.ok) throw new Error(await safeApiError(response, 'Could not update Paper strategy'))
  const body = await response.json() as { subscription: StrategySubscription }
  return body.subscription
}
