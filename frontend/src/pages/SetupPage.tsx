import { type FormEvent, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Copy,
  KeyRound,
  Play,
  RefreshCw,
  ShieldCheck,
  Square,
  Wallet,
} from 'lucide-react'
import {
  connectDhan,
  getChatFeed,
  getSetupStatus,
  saveRiskSettings,
  saveWebhookSecret,
  startEngine,
  stopEngine,
} from '../api/dashboard'
import ConfirmModal from '../components/ConfirmModal'
import SecretInput from '../components/SecretInput'
import { DhanConnectPayload, RiskSetupPayload, SetupStatus } from '../types'

const steps = ['Connect Dhan', 'Wallet Verified', 'Webhook Secret', 'Risk Settings', 'Start Engine']
const MIN_WEBHOOK_SECRET_LENGTH = 5
const WEBHOOK_SECRET_LENGTH_ERROR = `Webhook secret must be at least ${MIN_WEBHOOK_SECRET_LENGTH} characters.`

type ChatFeedItem = {
  id?: string
  timestamp?: string | null
  sender?: string
  text?: string
  type?: string
  metadata?: Record<string, unknown>
}

function currency(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return `Rs.${Number(value).toFixed(2)}`
}

function displayTime(value: string | null | undefined) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString()
}

function textValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2)
  return String(value)
}

function cleanText(value: string | undefined) {
  return (value || '').replace(/[^\x09\x0A\x0D\x20-\x7E]/g, '').replace(/\s+/g, ' ').trim()
}

function activityPlaceholder(status: SetupStatus) {
  if (!status.engine_started) return 'Engine stopped.'
  if (status.app_state.state === 'WAITING_ENTRY') return 'Waiting for entry alert.'
  if (status.app_state.state === 'WAITING_EXIT') return 'Waiting for sell alert.'
  return ''
}

function isTradeAttentionText(value: string) {
  return /reject|blocked|failed|invalid|error|unauthor|token/i.test(value)
}

function errorMessage(error: unknown) {
  const response = (error as { response?: { data?: unknown } }).response
  const data = response?.data
  if (typeof data === 'string') return data
  if (data && typeof data === 'object') {
    const detail = (data as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => {
          if (!item || typeof item !== 'object') return ''
          const loc = (item as { loc?: unknown }).loc
          const msg = (item as { msg?: unknown }).msg
          if (Array.isArray(loc) && loc.includes('webhook_secret') && typeof msg === 'string' && msg.toLowerCase().includes('at least')) {
            return WEBHOOK_SECRET_LENGTH_ERROR
          }
          return typeof msg === 'string' ? msg : ''
        })
        .filter(Boolean)
      if (messages.length) return Array.from(new Set(messages)).join(' ')
    }
    if (detail && typeof detail === 'object') {
      const message = (detail as { message?: unknown }).message
      if (typeof message === 'string') return message
    }
  }
  return 'Request failed. Check backend logs.'
}

function randomSecret() {
  const bytes = new Uint8Array(24)
  window.crypto.getRandomValues(bytes)
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function formatMasked(value: string | null | undefined): string {
  if (!value) return ''
  if (value.includes('*')) {
    const firstAsterisk = value.indexOf('*')
    if (firstAsterisk > 0) {
      return value.slice(0, firstAsterisk) + '.....'
    }
    return '.....'
  }
  return value
}

function SignalCard({ item }: { item: ChatFeedItem }) {
  const meta = item.metadata || {}
  return (
    <div className="rounded-md border border-[#2b2a26] bg-[#151513] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold text-[#d3cec5]">{textValue(meta.action)} alert received</p>
        <p className="text-xs text-[#9a968f]">{displayTime(item.timestamp)}</p>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <div>
          <p className="text-[#77736c]">Symbol</p>
          <p>{textValue(meta.trading_symbol || meta.symbol)}</p>
        </div>
        <div>
          <p className="text-[#77736c]">Side</p>
          <p>{textValue(meta.side)}</p>
        </div>
        <div>
          <p className="text-[#77736c]">Qty</p>
          <p>{textValue(meta.qty)}</p>
        </div>
        <div>
          <p className="text-[#77736c]">Contract</p>
          <p>{textValue(meta.expiry)} {textValue(meta.strike)} {textValue(meta.option_side)}</p>
        </div>
      </div>
    </div>
  )
}

function OrderCard({ item, status }: { item: ChatFeedItem; status: SetupStatus }) {
  const meta = item.metadata || {}
  const isExit = meta.action === 'EXIT'
  return (
    <div className="rounded-md border border-[#26331d] bg-[#11170d] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold text-[#c8f68f]">{isExit ? 'Sell executed' : 'Buy executed'}</p>
        <p className="text-xs text-[#9a968f]">{displayTime(item.timestamp)}</p>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <div>
          <p className="text-[#77736c]">Order ID</p>
          <p className="font-mono">{textValue(meta.order_id)}</p>
        </div>
        <div>
          <p className="text-[#77736c]">Status</p>
          <p>{textValue(meta.status)}</p>
        </div>
        <div>
          <p className="text-[#77736c]">Qty</p>
          <p>{textValue(meta.qty)}</p>
        </div>
        <div>
          <p className="text-[#77736c]">Avg price</p>
          <p>{textValue(meta.avg_price)}</p>
        </div>
        <div>
          <p className="text-[#77736c]">Symbol</p>
          <p>{textValue(meta.trading_symbol)}</p>
        </div>
        <div>
          <p className="text-[#77736c]">Session PnL</p>
          <p>{currency(status.wallet.session_pnl)}</p>
        </div>
        <div>
          <p className="text-[#77736c]">{meta.action === 'EXIT' ? 'Exit source' : 'Next step'}</p>
          <p>{isExit ? 'TradingView TP/SL' : 'Sell alert'}</p>
        </div>
        <div>
          <p className="text-[#77736c]">Mode</p>
          <p>{textValue(meta.dhan_mode)}</p>
        </div>
      </div>
    </div>
  )
}

function ActivityFeed({ status, feed }: { status: SetupStatus; feed: ChatFeedItem[] }) {
  const waitingText = activityPlaceholder(status)
  const items = feed
    .filter((item) => item.id !== 'init-welcome')
    .filter((item) => {
      if (item.type === 'signal_card' || item.type === 'order_card') return true
      const text = cleanText(item.text)
      if (!text) return false
      return isTradeAttentionText(text)
    })
    .slice(-12)

  return (
    <section className="rounded-lg border border-[#24231f] bg-[#151513]">
      <div className="flex items-center gap-2 border-b border-[#24231f] px-5 py-4">
        <Activity className="text-[#d3cec5]" size={20} />
        <h2 className="text-lg font-semibold">Live Activity</h2>
      </div>
      <div className="space-y-3 px-5 py-5">
        {items.map((item, index) => {
          if (item.type === 'signal_card') return <SignalCard key={item.id || index} item={item} />
          if (item.type === 'order_card') return <OrderCard key={item.id || index} item={item} status={status} />

          const text = cleanText(item.text)
          if (!text) return null
          return (
            <div key={item.id || index} className="rounded-md border border-red-800 bg-red-950/30 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold text-red-200">{text}</p>
                <p className="text-xs text-[#77736c]">{displayTime(item.timestamp)}</p>
              </div>
            </div>
          )
        })}

        {waitingText && (
          <div className="rounded-md border border-[#2b2a26] bg-[#090908] p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className={status.engine_started ? 'text-sm font-semibold text-[#d3cec5]' : 'text-sm font-semibold text-[#d8d3c8]'}>{waitingText}</p>
              {status.engine_started && <span className="h-2 w-2 rounded-full bg-[#98e94d]" />}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

export default function SetupPage() {
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [chatFeed, setChatFeed] = useState<ChatFeedItem[]>([])
  const [clientId, setClientId] = useState('')
  const [accessToken, setAccessToken] = useState('')
  const [webhookSecret, setWebhookSecret] = useState('')
  const [risk, setRisk] = useState<RiskSetupPayload>({
    max_qty_per_order: 1,
    max_trades_per_day: 1,
    daily_loss_limit: 500,
    server_side_exit_enabled: true,
    marketfeed_ws_enabled: true,
    option_ltp_source: 'AUTO',
    option_exit_mode: 'DHAN_SUPER',
    option_ws_stale_seconds: 5,
    option_rest_fallback_enabled: true,
    option_rest_fallback_cooldown_seconds: 15,
    option_sl_percent: 10,
    option_tp_percent: 20,
    option_ltp_poll_seconds: 1,
    allow_entry: true,
    allow_exit: true,
  })
  const [setupFormsOpen, setSetupFormsOpen] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [confirmModal, setConfirmModal] = useState<{
    isOpen: boolean
    title: string
    message: string
    variant: 'danger' | 'warning' | 'primary'
    onConfirm: () => void
  }>({
    isOpen: false,
    title: '',
    message: '',
    variant: 'primary',
    onConfirm: () => {},
  })

  // Polls engine/status only — never touches risk form state so user edits persist
  const pollStatus = async () => {
    const [statusResponse, feedResponse] = await Promise.all([
      getSetupStatus(),
      getChatFeed().catch(() => ({ data: [] as ChatFeedItem[] })),
    ])
    setStatus(statusResponse.data)
    setChatFeed((feedResponse.data || []) as ChatFeedItem[])
  }

  // Loads risk settings — only on mount and after a successful save
  const loadRisk = async () => {
    const statusResponse = await getSetupStatus()
    setRisk(statusResponse.data.settings)
  }

  const loadData = async () => { await pollStatus(); await loadRisk() }

  useEffect(() => {
    loadData().catch(() => setError('Backend is not reachable. Check VITE_API_BASE_URL and backend health.'))
    const interval = window.setInterval(() => {
      pollStatus().catch(() => undefined)
    }, 3000)
    return () => window.clearInterval(interval)
  }, [])

  useEffect(() => {
    if (status?.engine_started) setSetupFormsOpen(false)
  }, [status?.engine_started])

  const activeStep = useMemo(() => {
    if (!status?.dhan_connected) return 0
    if (!status.wallet?.success) return 1
    if (!status.webhook_secret_set) return 2
    if (!status.risk_configured) return 3
    return 4
  }, [status])

  const submitDhan = async (event: { preventDefault: () => void }, action: 'connect' | 'test') => {
    event.preventDefault()
    setBusy(action)
    setError('')
    setMessage('')
    try {
      const payload: DhanConnectPayload = {}
      const trimmedClientId = clientId.trim()
      const trimmedAccessToken = accessToken.trim()
      if (trimmedClientId) payload.client_id = trimmedClientId
      if (trimmedAccessToken) payload.access_token = trimmedAccessToken
      if (!status?.dhan_client_id_masked && !payload.client_id) {
        setError('Dhan Client ID is required.')
        return
      }
      if (!status?.access_token_present && !payload.access_token) {
        setError('Dhan Access Token is required.')
        return
      }
      const response = await connectDhan(payload)
      setClientId('')
      setAccessToken('')
      setMessage(response.data?.message || 'Dhan connected successfully.')
      await loadData()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const submitSecret = async (event: FormEvent) => {
    event.preventDefault()
    const trimmedSecret = webhookSecret.trim()
    if (trimmedSecret.length < MIN_WEBHOOK_SECRET_LENGTH) {
      setMessage('')
      setError(WEBHOOK_SECRET_LENGTH_ERROR)
      return
    }
    setBusy('secret')
    setError('')
    try {
      await saveWebhookSecret(trimmedSecret)
      setWebhookSecret('')
      setMessage('Webhook secret saved.')
      await loadData()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const submitRisk = async (event: FormEvent) => {
    event.preventDefault()
    setBusy('risk')
    setError('')
    try {
      const payload: RiskSetupPayload = {
        ...risk,
        server_side_exit_enabled: true,
        marketfeed_ws_enabled: true,
        option_ltp_source: 'AUTO',
        option_exit_mode: 'DHAN_SUPER',
        option_ltp_poll_seconds: 1,
        option_ws_stale_seconds: 5,
        option_rest_fallback_enabled: true,
        option_rest_fallback_cooldown_seconds: 15,
      }
      await saveRiskSettings(payload)
      setMessage('Risk settings saved.')
      await loadRisk()
      await pollStatus()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const runStartEngine = async () => {
    const confirmLive = Boolean(status?.mode.dhan_mode === 'REAL' && status.mode.live_orders_enabled)
    if (confirmLive) {
      setConfirmModal({
        isOpen: true,
        title: 'Start Engine (LIVE MODE)',
        message: 'LIVE ORDERS ARE ENABLED — real money orders will be placed on Dhan. Are you sure you want to start the signal routing engine?',
        variant: 'danger',
        onConfirm: () => {
          setConfirmModal((prev) => ({ ...prev, isOpen: false }))
          executeStartEngine(true)
        },
      })
    } else {
      executeStartEngine(false)
    }
  }

  const executeStartEngine = async (live: boolean) => {
    setBusy('engine')
    setError('')
    try {
      await startEngine(live)
      setMessage('Engine started. Waiting for TradingView entry alert.')
      setSetupFormsOpen(false)
      await loadData()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const runStopEngine = async () => {
    setBusy('stop-engine')
    setError('')
    try {
      await stopEngine()
      setMessage('Engine stopped. New TradingView entry alerts are blocked.')
      setSetupFormsOpen(true)
      await loadData()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const copyWebhook = () => {
    if (!status?.webhook_url) return
    navigator.clipboard.writeText(status.webhook_url)
    setMessage('Webhook URL copied.')
  }

  if (!status) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center text-[#d8d3c8]">
        <RefreshCw className="mr-3 animate-spin" size={20} />
        Loading setup status
      </div>
    )
  }

  const completed = {
    dhan: Boolean(status.dhan_connected && status.wallet?.success),
    secret: Boolean(status.webhook_secret_set),
    risk: Boolean(status.risk_configured),
  }
  const setupComplete = completed.dhan && completed.secret && completed.risk

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Setup</h1>
          <p className="mt-1 text-sm text-[#9a968f]">Connect Dhan, save your webhook secret, set risk limits, then start the engine.</p>
        </div>
        <button onClick={() => loadData()} className="btn-ghost flex items-center gap-2 self-start">
          <RefreshCw size={16} /> Refresh
        </button>
      </div>

      {status.mode.live_orders_enabled && (
        <div className="rounded-lg border-2 border-red-500 bg-red-950 p-4 text-red-100">
          <p className="font-bold">LIVE ORDERS ENABLED - real money orders can be placed.</p>
        </div>
      )}

      {message && <div className="rounded-md border border-[#26331d] bg-[#11170d] p-3 text-sm text-[#c8f68f]">{message}</div>}
      {error && <div className="rounded-md border border-red-700 bg-red-950 p-3 text-sm text-red-200">{error}</div>}

      <div className="grid grid-cols-1 gap-2 md:grid-cols-5">
        {steps.map((step, index) => {
          const done = index < activeStep || (index === 4 && status.engine_started)
          const active = index === activeStep && !done
          return (
            <div key={step} className={done || active ? 'rounded-md border border-[#26331d] bg-[#11170d] p-3' : 'rounded-md border border-[#24231f] bg-[#151513] p-3'}>
              <div className="flex items-center gap-2 text-sm font-medium">
                {done ? <CheckCircle2 className="text-[#98e94d]" size={16} /> : <span className={active ? 'h-4 w-4 rounded-full border border-[#98e94d]' : 'h-4 w-4 rounded-full border border-[#77736c]'} />}
                {step}
              </div>
            </div>
          )
        })}
      </div>

      <section className="rounded-lg border border-[#24231f] bg-[#151513]">
        <button type="button" onClick={() => setSetupFormsOpen((open) => !open)} className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left">
          <span className="flex min-w-0 items-center gap-3">
            <KeyRound className={setupComplete ? 'text-[#98e94d]' : 'text-amber-400'} size={20} />
            <span>
              <span className="block text-base font-semibold">Setup Forms</span>
              <span className={setupComplete ? 'mt-1 block text-sm text-[#98e94d]' : 'mt-1 block text-sm text-amber-300'}>
                {status.engine_started ? 'Collapsed while engine is running' : setupComplete ? 'Completed' : 'Open to finish Dhan, webhook, and risk setup'}
              </span>
            </span>
          </span>
          <span className="flex shrink-0 items-center gap-2 text-sm text-[#d8d3c8]">
            {setupFormsOpen ? 'Collapse' : 'Open'}
            {setupFormsOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </span>
        </button>

        {setupFormsOpen ? (
          <div className="border-t border-[#24231f] px-5 py-5">
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div className="space-y-4">
                <div className="flex items-center gap-2">
                  <KeyRound className="text-[#98e94d]" size={20} />
                  <h2 className="text-lg font-semibold">Connect Dhan</h2>
                </div>
                <form onSubmit={(event) => submitDhan(event, 'connect')} className="space-y-4">
                  <div className="grid grid-cols-1 gap-2 text-sm text-[#9a968f] sm:grid-cols-2">
                    <p>Saved client<br /><span className="font-mono text-[#d8d3c8]">{status.dhan_client_id_masked || '-'}</span></p>
                    <p>Saved token<br /><span className="font-mono text-[#d8d3c8] break-all">{formatMasked(status.access_token_masked) || (status.access_token_present ? 'present' : '-')}</span></p>
                  </div>
                  <label className="block text-sm">
                    <span className="mb-1 block text-[#d8d3c8]">Dhan Client ID</span>
                    <SecretInput value={clientId} onChange={(event) => setClientId(event.target.value)} autoComplete="off" placeholder="Enter or update Dhan Client ID" required={!status.dhan_client_id_masked} revealLabel="Dhan Client ID" />
                  </label>
                  <label className="block text-sm">
                    <span className="mb-1 block text-[#d8d3c8]">Dhan Access Token</span>
                    <SecretInput value={accessToken} onChange={(event) => setAccessToken(event.target.value)} autoComplete="new-password" placeholder="Enter or update Dhan Access Token" required={!status.access_token_present} revealLabel="Dhan Access Token" />
                  </label>
                  {!status.vault.ready && (
                    <p className="rounded-md border border-amber-800 bg-amber-950 p-3 text-sm text-amber-200">{status.vault.error}</p>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <button disabled={busy === 'connect'} className="btn-primary" type="submit">
                      Connect Dhan
                    </button>
                    <button disabled={busy === 'test'} onClick={(event) => submitDhan(event, 'test')} className="btn-ghost" type="button">
                      Test Connection
                    </button>
                  </div>
                  {status.dhan_connected && <p className="text-sm text-[#98e94d]">Dhan connected successfully. Client ID: {status.dhan_client_id_masked}</p>}
                </form>
              </div>

              <div className="space-y-4">
                <div className="flex items-center gap-2">
                  <Wallet className="text-[#d3cec5]" size={20} />
                  <h2 className="text-lg font-semibold">Wallet / Funds</h2>
                </div>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <p><span className="text-[#9a968f]">Available balance</span><br /><span className="font-semibold">{currency(status.wallet.available_balance)}</span></p>
                  <p><span className="text-[#9a968f]">Utilized margin</span><br /><span className="font-semibold">{currency(status.wallet.utilized_amount)}</span></p>
                  <p><span className="text-[#9a968f]">Client ID</span><br /><span className="font-mono">{status.wallet.client_id || status.dhan_client_id_masked || '-'}</span></p>
                  <p><span className="text-[#9a968f]">Dhan mode</span><br /><span className={status.mode.dhan_mode === 'REAL' ? 'font-bold text-red-300' : 'font-bold text-[#98e94d]'}>{status.mode.dhan_mode}</span></p>
                </div>
                <p className="rounded-md border border-[#2b2a26] bg-[#090908] p-3 text-sm text-[#d8d3c8]">{status.static_ip_note}</p>
              </div>
            </div>

            <div className="mt-6 grid grid-cols-1 gap-6 border-t border-[#24231f] pt-6 lg:grid-cols-2">
              <div className="space-y-4">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="text-[#98e94d]" size={20} />
                  <h2 className="text-lg font-semibold">Webhook Secret</h2>
                </div>
                <form onSubmit={submitSecret} className="space-y-4">
                  <label className="block text-sm">
                    <span className="mb-1 block text-[#d8d3c8]">Webhook Secret</span>
                    <SecretInput value={webhookSecret} onChange={(event) => setWebhookSecret(event.target.value)} autoComplete="new-password" minLength={MIN_WEBHOOK_SECRET_LENGTH} placeholder="Enter or update webhook secret" required={!status.webhook_secret_set} revealLabel="webhook secret" />
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <button disabled={busy === 'secret'} className="btn-primary" type="submit">
                      Save Secret
                    </button>
                    <button onClick={() => setWebhookSecret(randomSecret())} className="btn-ghost" type="button">
                      Generate Random Secret
                    </button>
                  </div>
                  {status.webhook_secret_set && <p className="text-sm text-[#98e94d]">Webhook secret saved: <span className="font-mono break-all">{formatMasked(status.webhook_secret_masked) || 'present'}</span></p>}
                </form>
              </div>

              <div className="space-y-4">
                <h2 className="text-lg font-semibold">TradingView Setup</h2>
                <div>
                  <p className="mb-1 text-sm text-[#9a968f]">Webhook URL</p>
                  <div className="flex items-center gap-2 rounded-md border border-[#24231f] bg-[#090908] p-3">
                    <p className="min-w-0 flex-1 break-all font-mono text-sm">{status.webhook_url || '-'}</p>
                    <button onClick={copyWebhook} className="btn-ghost p-2" title="Copy webhook URL" type="button">
                      <Copy size={16} />
                    </button>
                  </div>
                </div>
                <div>
                  <p className="mb-1 text-sm text-[#9a968f]">Alert Message</p>
                  <p className="rounded-md border border-[#24231f] bg-[#090908] p-3 font-mono text-sm">{'{{strategy.order.alert_message}}'}</p>
                </div>
              </div>
            </div>

            <form onSubmit={submitRisk} className="mt-6 space-y-4 border-t border-[#24231f] pt-6">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="text-[#98e94d]" size={20} />
                <h2 className="text-lg font-semibold">Risk Settings</h2>
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                <label className="block text-sm">
                  <span className="mb-1 block" style={{ color: 'var(--c-text-2)' }}>Max quantity per order</span>
                  <input className="input no-spinner" type="number" min={1} value={risk.max_qty_per_order || ''} onChange={(event) => setRisk({ ...risk, max_qty_per_order: Number(event.target.value) })} required />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block" style={{ color: 'var(--c-text-2)' }}>Max trades per day</span>
                  <input className="input no-spinner" type="number" min={1} value={risk.max_trades_per_day || ''} onChange={(event) => setRisk({ ...risk, max_trades_per_day: Number(event.target.value) })} required />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block" style={{ color: 'var(--c-text-2)' }}>Daily loss limit (INR)</span>
                  <input className="input no-spinner" type="number" min={1} value={risk.daily_loss_limit || ''} onChange={(event) => setRisk({ ...risk, daily_loss_limit: Number(event.target.value) })} required />
                </label>
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <label className="block text-sm">
                  <span className="mb-1 block" style={{ color: 'var(--c-text-2)' }}>Option SL %</span>
                  <input className="input no-spinner" type="number" min={0.1} step={0.1} value={risk.option_sl_percent || ''} onChange={(event) => setRisk({ ...risk, option_sl_percent: Number(event.target.value) })} required />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block" style={{ color: 'var(--c-text-2)' }}>Option TP %</span>
                  <input className="input no-spinner" type="number" min={0.1} step={0.1} value={risk.option_tp_percent || ''} onChange={(event) => setRisk({ ...risk, option_tp_percent: Number(event.target.value) })} required />
                </label>
              </div>
              <div className="flex flex-wrap gap-3">
                <label className="nova-toggle">
                  <input type="checkbox" checked={risk.allow_entry} onChange={(e) => setRisk({ ...risk, allow_entry: e.target.checked })} />
                  <span className="nova-toggle__track"><span className="nova-toggle__thumb" /></span>
                  <span className="nova-toggle__label">Allow entry</span>
                </label>
                <label className="nova-toggle">
                  <input type="checkbox" checked={risk.allow_exit} onChange={(e) => setRisk({ ...risk, allow_exit: e.target.checked })} />
                  <span className="nova-toggle__track"><span className="nova-toggle__thumb" /></span>
                  <span className="nova-toggle__label">Allow exit</span>
                </label>
              </div>
              <button disabled={busy === 'risk'} className="btn-primary" type="submit">
                Save Risk Settings
              </button>
            </form>
          </div>
        ) : (
          <div className="border-t border-[#24231f] px-5 py-4">
            <div className="grid grid-cols-1 gap-4 text-sm md:grid-cols-4">
              <p><span className="text-[#9a968f]">Dhan</span><br />{completed.dhan ? `Connected ${status.dhan_client_id_masked || ''}` : 'Needs attention'}</p>
              <p><span className="text-[#9a968f]">Wallet</span><br />{currency(status.wallet.available_balance)}</p>
              <p><span className="text-[#9a968f]">Webhook</span><br />{completed.secret ? 'Secret saved' : 'Needs secret'}</p>
              <p><span className="text-[#9a968f]">Risk</span><br />Qty {risk.max_qty_per_order} / SL {risk.option_sl_percent ?? 10}% / TP {risk.option_tp_percent ?? 20}%</p>
            </div>
            <div className="mt-4 flex flex-col gap-3 text-sm md:flex-row md:items-center md:justify-between">
              <p className="min-w-0 break-all font-mono text-[#d8d3c8]">{status.webhook_url || '-'}</p>
              <button onClick={copyWebhook} className="btn-ghost flex items-center gap-2 self-start" type="button">
                <Copy size={16} /> Copy URL
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="rounded-lg border border-[#24231f] bg-[#151513] p-5">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-lg font-semibold">Start Engine</h2>
            <p className="mt-1 text-sm text-[#9a968f]">Webhook trading is enabled only after this setup check passes.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button onClick={runStartEngine} disabled={busy === 'engine' || status.engine_started} className="btn-primary flex items-center gap-2" type="button">
              <Play size={16} /> Start Engine
            </button>
            <button onClick={runStopEngine} disabled={busy === 'stop-engine' || !status.engine_started} className="btn-ghost btn-stop-engine flex items-center gap-2" type="button">
              <Square size={16} /> Stop Engine
            </button>
          </div>
        </div>
        {status.readiness.issues.length > 0 && (
          <div className="mt-4 rounded-md border border-amber-800 bg-amber-950 p-3 text-sm text-amber-100">
            {status.readiness.issues.map((issue) => (
              <p key={issue}>{issue}</p>
            ))}
          </div>
        )}
        {status.engine_started && <p className="mt-4 text-sm text-[#98e94d]">Engine started. Waiting for TradingView entry alert.</p>}
      </section>

      <ActivityFeed status={status} feed={chatFeed} />

      <ConfirmModal
        isOpen={confirmModal.isOpen}
        title={confirmModal.title}
        message={confirmModal.message}
        variant={confirmModal.variant}
        onConfirm={confirmModal.onConfirm}
        onCancel={() => setConfirmModal((prev) => ({ ...prev, isOpen: false }))}
      />
    </div>
  )
}
