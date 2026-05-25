import { type FormEvent, type ReactNode, useEffect, useMemo, useState } from 'react'
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
  Wallet,
} from 'lucide-react'
import {
  connectDhan,
  getChatFeed,
  getSetupStatus,
  saveRiskSettings,
  saveWebhookSecret,
  startEngine,
} from '../api/dashboard'
import { RiskSetupPayload, SetupStatus } from '../types'

const steps = ['Connect Dhan', 'Wallet Verified', 'Webhook Secret', 'Risk Settings', 'Start Engine']
const MIN_WEBHOOK_SECRET_LENGTH = 5
const WEBHOOK_SECRET_LENGTH_ERROR = `Webhook secret must be at least ${MIN_WEBHOOK_SECRET_LENGTH} characters.`

type SetupSection = 'dhan' | 'secret' | 'risk'

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

function stateMessage(status: SetupStatus) {
  if (!status.engine_started) return 'Engine stopped. Start engine when setup is ready.'
  if (status.app_state.state === 'WAITING_ENTRY') return 'Waiting for entry alert.'
  if (status.app_state.state === 'WAITING_EXIT') return 'Waiting for sell alert.'
  if (status.app_state.state === 'ERROR') return `Order rejected: ${status.app_state.last_message}`
  return status.app_state.last_message || status.app_state.state.replace(/_/g, ' ')
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

function SetupPanel({
  title,
  complete,
  open,
  icon,
  summary,
  onToggle,
  children,
}: {
  title: string
  complete: boolean
  open: boolean
  icon: ReactNode
  summary: ReactNode
  onToggle: () => void
  children: ReactNode
}) {
  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900">
      <button type="button" onClick={onToggle} className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left">
        <span className="flex min-w-0 items-center gap-3">
          {icon}
          <span>
            <span className="block text-base font-semibold">{title}</span>
            <span className={complete ? 'mt-1 block text-sm text-emerald-300' : 'mt-1 block text-sm text-amber-300'}>
              {complete ? 'Completed' : 'Needs attention'}
            </span>
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2 text-sm text-slate-300">
          {complete && !open ? 'Edit' : open ? 'Collapse' : 'Open'}
          {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </span>
      </button>
      {open ? <div className="border-t border-slate-800 px-5 py-5">{children}</div> : <div className="border-t border-slate-800 px-5 py-4">{summary}</div>}
    </section>
  )
}

function SignalCard({ item }: { item: ChatFeedItem }) {
  const meta = item.metadata || {}
  return (
    <div className="rounded-md border border-sky-800 bg-sky-950/30 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold text-sky-200">{textValue(meta.action)} alert received</p>
        <p className="text-xs text-slate-400">{displayTime(item.timestamp)}</p>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <div>
          <p className="text-slate-500">Symbol</p>
          <p>{textValue(meta.trading_symbol || meta.symbol)}</p>
        </div>
        <div>
          <p className="text-slate-500">Side</p>
          <p>{textValue(meta.side)}</p>
        </div>
        <div>
          <p className="text-slate-500">Qty</p>
          <p>{textValue(meta.qty)}</p>
        </div>
        <div>
          <p className="text-slate-500">Contract</p>
          <p>{textValue(meta.expiry)} {textValue(meta.strike)} {textValue(meta.option_side)}</p>
        </div>
      </div>
    </div>
  )
}

function OrderCard({ item, status }: { item: ChatFeedItem; status: SetupStatus }) {
  const meta = item.metadata || {}
  return (
    <div className="rounded-md border border-emerald-800 bg-emerald-950/30 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold text-emerald-200">{textValue(meta.action)} order details</p>
        <p className="text-xs text-slate-400">{displayTime(item.timestamp)}</p>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <div>
          <p className="text-slate-500">Order ID</p>
          <p className="font-mono">{textValue(meta.order_id)}</p>
        </div>
        <div>
          <p className="text-slate-500">Status</p>
          <p>{textValue(meta.status)}</p>
        </div>
        <div>
          <p className="text-slate-500">Qty</p>
          <p>{textValue(meta.qty)}</p>
        </div>
        <div>
          <p className="text-slate-500">Avg price</p>
          <p>{textValue(meta.avg_price)}</p>
        </div>
        <div>
          <p className="text-slate-500">Symbol</p>
          <p>{textValue(meta.trading_symbol)}</p>
        </div>
        <div>
          <p className="text-slate-500">Session PnL</p>
          <p>{currency(status.wallet.session_pnl)}</p>
        </div>
        <div>
          <p className="text-slate-500">{meta.action === 'EXIT' ? 'Exit source' : 'Next step'}</p>
          <p>{meta.action === 'EXIT' ? 'TradingView TP/SL' : 'Sell alert'}</p>
        </div>
        <div>
          <p className="text-slate-500">Mode</p>
          <p>{textValue(meta.dhan_mode)}</p>
        </div>
      </div>
    </div>
  )
}

function ActivityFeed({ status, feed }: { status: SetupStatus; feed: ChatFeedItem[] }) {
  const items = feed.filter((item) => item.id !== 'init-welcome').slice(-18)

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900">
      <div className="flex items-center gap-2 border-b border-slate-800 px-5 py-4">
        <Activity className="text-sky-400" size={20} />
        <h2 className="text-lg font-semibold">Live Activity</h2>
      </div>
      <div className="space-y-3 px-5 py-5">
        <div className="rounded-md border border-slate-700 bg-slate-950 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className={status.engine_started ? 'font-semibold text-emerald-300' : 'font-semibold text-slate-300'}>
              {status.engine_started ? 'Engine running' : 'Engine stopped'}
            </p>
            <p className="text-xs text-slate-500">{displayTime(status.app_state.last_alert_at)}</p>
          </div>
          <p className="mt-2 text-sm text-slate-300">{stateMessage(status)}</p>
        </div>

        {items.map((item, index) => {
          if (item.type === 'signal_card') return <SignalCard key={item.id || index} item={item} />
          if (item.type === 'order_card') return <OrderCard key={item.id || index} item={item} status={status} />

          const text = cleanText(item.text)
          if (!text) return null
          const isWarning = /reject|blocked|failed|invalid|error/i.test(text)
          const isWaiting = /waiting/i.test(text)
          return (
            <div key={item.id || index} className={isWarning ? 'rounded-md border border-red-800 bg-red-950/30 p-4' : isWaiting ? 'rounded-md border border-slate-700 bg-slate-950 p-4' : 'rounded-md border border-slate-800 bg-slate-950 p-4'}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className={isWarning ? 'text-sm font-semibold text-red-200' : 'text-sm font-semibold text-slate-200'}>{text}</p>
                <p className="text-xs text-slate-500">{displayTime(item.timestamp)}</p>
              </div>
            </div>
          )
        })}
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
    allow_entry: true,
    allow_exit: true,
  })
  const [expanded, setExpanded] = useState<Partial<Record<SetupSection, boolean>>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const loadData = async () => {
    const [statusResponse, feedResponse] = await Promise.all([
      getSetupStatus(),
      getChatFeed().catch(() => ({ data: [] as ChatFeedItem[] })),
    ])
    setStatus(statusResponse.data)
    setRisk(statusResponse.data.settings)
    setChatFeed((feedResponse.data || []) as ChatFeedItem[])
  }

  useEffect(() => {
    loadData().catch(() => setError('Backend is not reachable. Check VITE_API_BASE_URL and backend health.'))
    const interval = window.setInterval(() => {
      loadData().catch(() => undefined)
    }, 3000)
    return () => window.clearInterval(interval)
  }, [])

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
      const response = await connectDhan({ client_id: clientId, access_token: accessToken })
      setAccessToken('')
      setMessage(response.data?.message || 'Dhan connected successfully.')
      setExpanded((current) => ({ ...current, dhan: false }))
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
      setExpanded((current) => ({ ...current, secret: false }))
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
      await saveRiskSettings(risk)
      setMessage('Risk settings saved.')
      setExpanded((current) => ({ ...current, risk: false }))
      await loadData()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const runStartEngine = async () => {
    setBusy('engine')
    setError('')
    try {
      const confirmLive = Boolean(status?.mode.dhan_mode === 'REAL' && status.mode.live_orders_enabled)
      if (confirmLive && !window.confirm('LIVE ORDERS ENABLED - real money orders can be placed. Start engine?')) {
        return
      }
      await startEngine(confirmLive)
      setMessage('Engine started. Waiting for TradingView entry alert.')
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
      <div className="flex min-h-[60vh] items-center justify-center text-slate-300">
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

  const isOpen = (key: SetupSection) => expanded[key] ?? !completed[key]

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Setup</h1>
          <p className="mt-1 text-sm text-slate-400">Connect Dhan, save your webhook secret, set risk limits, then start the engine.</p>
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

      {message && <div className="rounded-md border border-emerald-700 bg-emerald-950 p-3 text-sm text-emerald-200">{message}</div>}
      {error && <div className="rounded-md border border-red-700 bg-red-950 p-3 text-sm text-red-200">{error}</div>}

      <div className="grid grid-cols-1 gap-2 md:grid-cols-5">
        {steps.map((step, index) => {
          const done = index < activeStep || (index === 4 && status.engine_started)
          const active = index === activeStep && !done
          return (
            <div key={step} className={done || active ? 'rounded-md border border-emerald-800 bg-emerald-950/40 p-3' : 'rounded-md border border-slate-800 bg-slate-900 p-3'}>
              <div className="flex items-center gap-2 text-sm font-medium">
                {done ? <CheckCircle2 className="text-emerald-400" size={16} /> : <span className={active ? 'h-4 w-4 rounded-full border border-emerald-400' : 'h-4 w-4 rounded-full border border-slate-500'} />}
                {step}
              </div>
            </div>
          )
        })}
      </div>

      <div className="grid grid-cols-1 gap-4">
        <SetupPanel
          title="Connect Dhan"
          complete={completed.dhan}
          open={isOpen('dhan')}
          onToggle={() => setExpanded((current) => ({ ...current, dhan: !isOpen('dhan') }))}
          icon={<KeyRound className="text-emerald-400" size={20} />}
          summary={
            <div className="grid grid-cols-1 gap-3 text-sm md:grid-cols-4">
              <p><span className="text-slate-400">Client</span><br />{status.dhan_client_id_masked || '-'}</p>
              <p><span className="text-slate-400">Available</span><br />{currency(status.wallet.available_balance)}</p>
              <p><span className="text-slate-400">Utilized</span><br />{currency(status.wallet.utilized_amount)}</p>
              <p><span className="text-slate-400">Backend IP</span><br /><span className="font-mono">{status.outgoing_ip || '-'}</span></p>
            </div>
          }
        >
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <form onSubmit={(event) => submitDhan(event, 'connect')} className="space-y-4">
              <label className="block text-sm">
                <span className="mb-1 block text-slate-300">Dhan Client ID</span>
                <input value={clientId} onChange={(event) => setClientId(event.target.value)} className="input" autoComplete="off" />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block text-slate-300">Dhan Access Token</span>
                <input value={accessToken} onChange={(event) => setAccessToken(event.target.value)} className="input" type="password" autoComplete="off" />
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
              {status.dhan_connected && <p className="text-sm text-emerald-300">Dhan connected successfully. Client ID: {status.dhan_client_id_masked}</p>}
            </form>

            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <Wallet className="text-sky-400" size={20} />
                <h2 className="text-lg font-semibold">Wallet / Funds</h2>
              </div>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <p><span className="text-slate-400">Available balance</span><br /><span className="font-semibold">{currency(status.wallet.available_balance)}</span></p>
                <p><span className="text-slate-400">Utilized margin</span><br /><span className="font-semibold">{currency(status.wallet.utilized_amount)}</span></p>
                <p><span className="text-slate-400">Client ID</span><br /><span className="font-mono">{status.wallet.client_id || status.dhan_client_id_masked || '-'}</span></p>
                <p><span className="text-slate-400">Dhan mode</span><br /><span className={status.mode.dhan_mode === 'REAL' ? 'font-bold text-red-300' : 'font-bold text-emerald-300'}>{status.mode.dhan_mode}</span></p>
              </div>
              <p className="rounded-md border border-slate-700 bg-slate-950 p-3 text-sm text-slate-300">{status.static_ip_note}</p>
            </div>
          </div>
        </SetupPanel>

        <SetupPanel
          title="Webhook Secret"
          complete={completed.secret}
          open={isOpen('secret')}
          onToggle={() => setExpanded((current) => ({ ...current, secret: !isOpen('secret') }))}
          icon={<ShieldCheck className="text-emerald-400" size={20} />}
          summary={
            <div className="grid grid-cols-1 gap-3 text-sm md:grid-cols-[1fr_auto] md:items-center">
              <p><span className="text-slate-400">TradingView URL</span><br /><span className="break-all font-mono">{status.webhook_url || '-'}</span></p>
              <button onClick={copyWebhook} className="btn-ghost flex items-center gap-2 justify-self-start" type="button">
                <Copy size={16} /> Copy URL
              </button>
            </div>
          }
        >
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <form onSubmit={submitSecret} className="space-y-4">
              <label className="block text-sm">
                <span className="mb-1 block text-slate-300">Webhook Secret</span>
                <input value={webhookSecret} onChange={(event) => setWebhookSecret(event.target.value)} className="input" type="password" autoComplete="off" minLength={MIN_WEBHOOK_SECRET_LENGTH} required />
              </label>
              <div className="flex flex-wrap gap-2">
                <button disabled={busy === 'secret'} className="btn-primary" type="submit">
                  Save Secret
                </button>
                <button onClick={() => setWebhookSecret(randomSecret())} className="btn-ghost" type="button">
                  Generate Random Secret
                </button>
              </div>
              {status.webhook_secret_set && <p className="text-sm text-emerald-300">Webhook secret saved.</p>}
            </form>

            <div className="space-y-4">
              <h2 className="text-lg font-semibold">TradingView Setup</h2>
              <div>
                <p className="mb-1 text-sm text-slate-400">Webhook URL</p>
                <div className="flex items-center gap-2 rounded-md border border-slate-800 bg-slate-950 p-3">
                  <p className="min-w-0 flex-1 break-all font-mono text-sm">{status.webhook_url || '-'}</p>
                  <button onClick={copyWebhook} className="btn-ghost p-2" title="Copy webhook URL" type="button">
                    <Copy size={16} />
                  </button>
                </div>
              </div>
              <div>
                <p className="mb-1 text-sm text-slate-400">Alert Message</p>
                <p className="rounded-md border border-slate-800 bg-slate-950 p-3 font-mono text-sm">{'{{strategy.order.alert_message}}'}</p>
              </div>
            </div>
          </div>
        </SetupPanel>

        <SetupPanel
          title="Risk Settings"
          complete={completed.risk}
          open={isOpen('risk')}
          onToggle={() => setExpanded((current) => ({ ...current, risk: !isOpen('risk') }))}
          icon={<CheckCircle2 className="text-emerald-400" size={20} />}
          summary={
            <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-5">
              <p><span className="text-slate-400">Max qty</span><br />{risk.max_qty_per_order}</p>
              <p><span className="text-slate-400">Max trades</span><br />{risk.max_trades_per_day}</p>
              <p><span className="text-slate-400">Loss limit</span><br />{currency(risk.daily_loss_limit)}</p>
              <p><span className="text-slate-400">Entry</span><br />{risk.allow_entry ? 'Allowed' : 'Paused'}</p>
              <p><span className="text-slate-400">Exit</span><br />{risk.allow_exit ? 'Allowed' : 'Paused'}</p>
            </div>
          }
        >
          <form onSubmit={submitRisk} className="space-y-4">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <label className="block text-sm">
                <span className="mb-1 block text-slate-300">Max quantity per order</span>
                <input className="input" type="number" min={1} value={risk.max_qty_per_order} onChange={(event) => setRisk({ ...risk, max_qty_per_order: Number(event.target.value) })} />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block text-slate-300">Max trades per day</span>
                <input className="input" type="number" min={1} value={risk.max_trades_per_day} onChange={(event) => setRisk({ ...risk, max_trades_per_day: Number(event.target.value) })} />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block text-slate-300">Daily loss limit</span>
                <input className="input" type="number" min={1} value={risk.daily_loss_limit} onChange={(event) => setRisk({ ...risk, daily_loss_limit: Number(event.target.value) })} />
              </label>
            </div>
            <div className="flex flex-wrap gap-4 text-sm">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={risk.allow_entry} onChange={(event) => setRisk({ ...risk, allow_entry: event.target.checked })} />
                Allow entry
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={risk.allow_exit} onChange={(event) => setRisk({ ...risk, allow_exit: event.target.checked })} />
                Allow exit
              </label>
            </div>
            <button disabled={busy === 'risk'} className="btn-primary" type="submit">
              Save Risk Settings
            </button>
          </form>
        </SetupPanel>
      </div>

      <section className="rounded-lg border border-slate-800 bg-slate-900 p-5">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-lg font-semibold">Start Engine</h2>
            <p className="mt-1 text-sm text-slate-400">Webhook trading is enabled only after this setup check passes.</p>
          </div>
          <button onClick={runStartEngine} disabled={busy === 'engine' || status.engine_started} className="btn-primary flex items-center gap-2" type="button">
            <Play size={16} /> Start Engine
          </button>
        </div>
        {status.readiness.issues.length > 0 && (
          <div className="mt-4 rounded-md border border-amber-800 bg-amber-950 p-3 text-sm text-amber-100">
            {status.readiness.issues.map((issue) => (
              <p key={issue}>{issue}</p>
            ))}
          </div>
        )}
        {status.engine_started && <p className="mt-4 text-sm text-emerald-300">Engine started. Waiting for TradingView entry alert.</p>}
      </section>

      <ActivityFeed status={status} feed={chatFeed} />
    </div>
  )
}
