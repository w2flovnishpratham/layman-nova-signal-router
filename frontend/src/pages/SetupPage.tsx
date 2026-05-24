import { FormEvent, useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Copy, KeyRound, Play, RefreshCw, ShieldCheck, Wallet } from 'lucide-react'
import {
  connectDhan,
  getSetupStatus,
  saveRiskSettings,
  saveWebhookSecret,
  startEngine,
} from '../api/dashboard'
import { RiskSetupPayload, SetupStatus } from '../types'

const steps = ['Connect Dhan', 'Wallet Verified', 'Webhook Secret', 'Risk Settings', 'Start Engine']

function currency(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return `Rs.${Number(value).toFixed(2)}`
}

function errorMessage(error: unknown) {
  const response = (error as { response?: { data?: unknown } }).response
  const data = response?.data
  if (typeof data === 'string') return data
  if (data && typeof data === 'object') {
    const detail = (data as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
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

export default function SetupPage() {
  const [status, setStatus] = useState<SetupStatus | null>(null)
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
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const loadStatus = async () => {
    const response = await getSetupStatus()
    setStatus(response.data)
    setRisk(response.data.settings)
  }

  useEffect(() => {
    loadStatus().catch(() => setError('Backend is not reachable. Check VITE_API_BASE_URL and backend health.'))
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
      await loadStatus()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const submitSecret = async (event: FormEvent) => {
    event.preventDefault()
    setBusy('secret')
    setError('')
    try {
      await saveWebhookSecret(webhookSecret)
      setWebhookSecret('')
      setMessage('Webhook secret saved.')
      await loadStatus()
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
      await loadStatus()
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
      setMessage('Engine started.')
      await loadStatus()
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

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Setup</h1>
          <p className="mt-1 text-sm text-slate-400">Connect Dhan, save your webhook secret, set risk limits, then start the engine.</p>
        </div>
        <button onClick={() => loadStatus()} className="btn-ghost flex items-center gap-2 self-start">
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
        {steps.map((step, index) => (
          <div key={step} className={index <= activeStep ? 'rounded-md border border-emerald-800 bg-emerald-950/40 p-3' : 'rounded-md border border-slate-800 bg-slate-900 p-3'}>
            <div className="flex items-center gap-2 text-sm font-medium">
              {index < activeStep ? <CheckCircle2 className="text-emerald-400" size={16} /> : <span className="h-4 w-4 rounded-full border border-slate-500" />}
              {step}
            </div>
          </div>
        ))}
      </div>

      <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <form onSubmit={(event) => submitDhan(event, 'connect')} className="card space-y-4">
          <div className="flex items-center gap-2">
            <KeyRound className="text-emerald-400" size={20} />
            <h2 className="text-lg font-semibold">Connect your Dhan account</h2>
          </div>
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

        <div className="card space-y-4">
          <div className="flex items-center gap-2">
            <Wallet className="text-sky-400" size={20} />
            <h2 className="text-lg font-semibold">Wallet / Funds</h2>
          </div>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <p className="text-slate-400">Available balance</p>
              <p className="font-semibold">{currency(status.wallet.available_balance)}</p>
            </div>
            <div>
              <p className="text-slate-400">Utilized margin</p>
              <p className="font-semibold">{currency(status.wallet.utilized_amount)}</p>
            </div>
            <div>
              <p className="text-slate-400">Client ID</p>
              <p className="font-mono">{status.wallet.client_id || status.dhan_client_id_masked || '-'}</p>
            </div>
            <div>
              <p className="text-slate-400">Dhan mode</p>
              <p className={status.mode.dhan_mode === 'REAL' ? 'font-bold text-red-300' : 'font-bold text-emerald-300'}>{status.mode.dhan_mode}</p>
            </div>
            <div className="col-span-2">
              <p className="text-slate-400">Outgoing backend IP</p>
              <p className="font-mono">{status.outgoing_ip || '-'}</p>
            </div>
          </div>
          <p className="rounded-md border border-slate-700 bg-slate-950 p-3 text-sm text-slate-300">{status.static_ip_note}</p>
          {!status.wallet.success && <p className="text-sm text-amber-300">{status.wallet.message}</p>}
        </div>
      </section>

      <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <form onSubmit={submitSecret} className="card space-y-4">
          <div className="flex items-center gap-2">
            <ShieldCheck className="text-emerald-400" size={20} />
            <h2 className="text-lg font-semibold">Create your TradingView webhook secret</h2>
          </div>
          <label className="block text-sm">
            <span className="mb-1 block text-slate-300">Webhook Secret</span>
            <input value={webhookSecret} onChange={(event) => setWebhookSecret(event.target.value)} className="input" type="password" autoComplete="off" />
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

        <div className="card space-y-4">
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
          <div className="grid grid-cols-1 gap-2 text-sm text-slate-300 sm:grid-cols-2">
            <p className="rounded-md border border-slate-800 bg-slate-950 p-3">Existing Pine multi_leg_order supported.</p>
            <p className="rounded-md border border-slate-800 bg-slate-950 p-3">New NOVA payload supported.</p>
          </div>
        </div>
      </section>

      <form onSubmit={submitRisk} className="card space-y-4">
        <h2 className="text-lg font-semibold">Risk Setup</h2>
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

      <section className="card space-y-4">
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
          <div className="rounded-md border border-amber-800 bg-amber-950 p-3 text-sm text-amber-100">
            {status.readiness.issues.map((issue) => (
              <p key={issue}>{issue}</p>
            ))}
          </div>
        )}
        {status.engine_started && <p className="text-sm text-emerald-300">Engine started. Waiting for TradingView entry alert.</p>}
      </section>
    </div>
  )
}
