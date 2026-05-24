import api from './client'
import {
  DashboardSummary,
  DhanDebugConfig,
  EngineStatus,
  LiveFlowStep,
  LogBundle,
  OpenPosition,
  OrderEvent,
  RiskSetupPayload,
  SetupStatus,
  WalletSnapshot,
  WebhookUrlInfo,
} from '../types'

export const getHealth = () => api.get('/health')
export const getSetupStatus = () => api.get<SetupStatus>('/setup/status')
export const connectDhan = (payload: { client_id: string; access_token: string }) =>
  api.post('/setup/dhan/connect', payload)
export const disconnectDhan = () => api.post('/setup/dhan/disconnect')
export const saveWebhookSecret = (webhook_secret: string) => api.post('/setup/webhook-secret', { webhook_secret })
export const saveRiskSettings = (payload: RiskSetupPayload) => api.post('/setup/risk', payload)
export const startEngine = (confirm_live_orders = false) => api.post('/engine/start', { confirm_live_orders })
export const stopEngine = () => api.post('/engine/stop')
export const getEngineStatus = () => api.get<EngineStatus>('/engine/status')
export const getDashboardSummary = () => api.get<DashboardSummary>('/dashboard/summary')
export const getLiveFlow = () => api.get<LiveFlowStep[]>('/dashboard/live-flow')
export const getOrders = () => api.get<OrderEvent[]>('/orders')
export const getLogs = () => api.get<LogBundle>('/logs')
export const getPositions = () => api.get<OpenPosition>('/positions')
export const getWebhookUrl = () => api.get<WebhookUrlInfo>('/system/webhook-url')
export const getChatFeed = () => api.get<any[]>('/dashboard/chat-feed')
export const getDhanFunds = () => api.get<WalletSnapshot>('/broker/dhan/funds')
export const getDhanDebugConfig = () => api.get<DhanDebugConfig>('/debug/dhan/config')
export const prepareDhanOrder = (payload: Record<string, unknown>) => api.post<Record<string, unknown>>('/debug/dhan/prepare-order', payload)
export const dryRunLiveDhanOrder = (payload: Record<string, unknown>) => api.post<Record<string, unknown>>('/debug/dhan/live-order-dry-run', payload)
export const safeDhanPing = () => api.post<Record<string, unknown>>('/debug/dhan/ping-safe')

export const emergencyStop = () => api.post('/control/emergency-stop')
export const resumeTrading = () => api.post('/control/resume')
export const setGlobalKillSwitch = (enabled: boolean) => api.post('/control/global-kill-switch', { enabled })
export const resetRuntimeState = () => api.post('/control/reset-state')
export const freshStart = () => api.post<{ ok: boolean; message?: string }>('/control/fresh-start')
export const clearSeenSignals = () => api.post('/control/clear-seen-signals')
export const testDhan = () => api.post<{ success: boolean; message: string; wallet?: WalletSnapshot }>('/broker/dhan/test')

export const pauseEntries = () => api.post('/control/pause-entries')
export const resumeEntries = () => api.post('/control/resume-entries')
export const panicExit = () => api.post('/control/panic-exit')
