import { FormEvent, useEffect, useState } from 'react'
import { AlertTriangle, RefreshCw, ShieldAlert, Square } from 'lucide-react'
import {
  connectDhan,
  disconnectDhan,
  emergencyStop,
  getDhanDebugConfig,
  getSetupStatus,
  resumeTrading,
  saveRiskSettings,
  saveWebhookSecret,
  setGlobalKillSwitch,
  stopEngine,
} from '../api/dashboard'
import { DhanDebugConfig, RiskSetupPayload, SetupStatus } from '../types'

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

export default function SettingsPage() {
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [debug, setDebug] = useState<DhanDebugConfig | null>(null)
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
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  const loadData = async () => {
    const response = await getSetupStatus()
    setStatus(response.data)
    setRisk(response.data.settings)
    if (response.data.debug_enabled) {
      getDhanDebugConfig().then((debugResponse) => setDebug(debugResponse.data)).catch(() => setDebug(null))
    }
  }

  useEffect(() => {
    loadData().catch(() => setError('Backend settings APIs are not reachable.'))
  }, [])

  const run = async (key: string, action: Promise<unknown>, done: string) => {
    setBusy(key)
    setError('')
    try {
      await action
      setMessage(done)
      await loadData()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const reconnectDhan = async (event: FormEvent) => {
    event.preventDefault()
    await run('dhan', connectDhan({ client_id: clientId, access_token: accessToken }), 'Dhan credentials updated.')
    setAccessToken('')
  }

  const updateSecret = async (event: FormEvent) => {
    event.preventDefault()
    await run('secret', saveWebhookSecret(webhookSecret), 'Webhook secret updated.')
    setWebhookSecret('')
  }

  const updateRisk = async (event: FormEvent) => {
    event.preventDefault()
    await run('risk', saveRiskSettings(risk), 'Risk settings updated.')
  }

  if (!status) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center text-slate-300">
        <RefreshCw className="mr-3 animate-spin" size={20} />
        Loading settings
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Settings</h1>
          <p className="mt-1 text-sm text-slate-400">Reconnect Dhan, rotate webhook secret, update risk, and control safety switches.</p>
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

      <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <form onSubmit={reconnectDhan} className="card space-y-4">
          <h2 className="text-lg font-semibold">Reconnect Dhan</h2>
          <p className="text-sm text-slate-400">Current client: {status.dhan_client_id_masked || '-'}</p>
          <label className="block text-sm">
            <span className="mb-1 block text-slate-300">Dhan Client ID</span>
            <input className="input" value={clientId} onChange={(event) => setClientId(event.target.value)} autoComplete="off" />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-slate-300">Dhan Access Token</span>
            <input className="input" type="password" value={accessToken} onChange={(event) => setAccessToken(event.target.value)} autoComplete="off" />
          </label>
          <div className="flex flex-wrap gap-2">
            <button disabled={busy === 'dhan'} className="btn-primary" type="submit">
              Save Dhan
            </button>
            <button disabled={busy === 'disconnect'} className="btn-ghost" type="button" onClick={() => run('disconnect', disconnectDhan(), 'Dhan disconnected and engine stopped.')}>
              Disconnect
            </button>
          </div>
        </form>

        <form onSubmit={updateSecret} className="card space-y-4">
          <h2 className="text-lg font-semibold">Reset Webhook Secret</h2>
          <p className="text-sm text-slate-400">Saved: {status.webhook_secret_set ? 'yes' : 'no'}</p>
          <label className="block text-sm">
            <span className="mb-1 block text-slate-300">New Webhook Secret</span>
            <input className="input" type="password" value={webhookSecret} onChange={(event) => setWebhookSecret(event.target.value)} autoComplete="off" />
          </label>
          <div className="flex flex-wrap gap-2">
            <button disabled={busy === 'secret'} className="btn-primary" type="submit">
              Save Secret
            </button>
            <button onClick={() => setWebhookSecret(randomSecret())} className="btn-ghost" type="button">
              Generate Random Secret
            </button>
          </div>
        </form>
      </section>

      <form onSubmit={updateRisk} className="card space-y-4">
        <h2 className="text-lg font-semibold">Risk Settings</h2>
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
          Update Risk
        </button>
      </form>

      <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="card space-y-4">
          <h2 className="text-lg font-semibold">Engine Control</h2>
          <div className="flex flex-wrap gap-2">
            <button onClick={() => run('stop', stopEngine(), 'Engine stopped.')} className="btn-ghost flex items-center gap-2" type="button">
              <Square size={16} /> Stop Engine
            </button>
            <button onClick={() => run('resume', resumeTrading(), 'Emergency stop cleared.')} className="btn-primary" type="button">
              Resume
            </button>
          </div>
          <p className="text-sm text-slate-400">Engine started: {String(status.engine_started)}</p>
        </div>

        <div className="card space-y-4">
          <h2 className="text-lg font-semibold">Safety</h2>
          <div className="flex flex-wrap gap-2">
            <button onClick={() => run('emergency', emergencyStop(), 'Emergency stop activated.')} className="btn-danger flex items-center gap-2" type="button">
              <ShieldAlert size={16} /> Emergency Stop
            </button>
            <button onClick={() => run('kill-on', setGlobalKillSwitch(true), 'Global kill switch enabled.')} className="btn-danger flex items-center gap-2" type="button">
              <AlertTriangle size={16} /> Kill Switch On
            </button>
            <button onClick={() => run('kill-off', setGlobalKillSwitch(false), 'Global kill switch cleared.')} className="btn-ghost" type="button">
              Kill Switch Off
            </button>
          </div>
          <p className="text-sm text-slate-400">Emergency stop: {String(status.settings.emergency_stop)}. Kill switch: {String(status.settings.global_kill_switch)}.</p>
        </div>
      </section>

      {status.debug_enabled && debug && (
        <section className="card space-y-4">
          <h2 className="text-lg font-semibold">Dhan Debug</h2>
          <div className="grid grid-cols-1 gap-3 text-sm md:grid-cols-3">
            <p><span className="text-slate-400">Outgoing IP:</span> {debug.outgoing_ip || '-'}</p>
            <p><span className="text-slate-400">Mode:</span> {debug.dhan.mode}</p>
            <p><span className="text-slate-400">Live orders:</span> {String(debug.dhan.live_orders_enabled)}</p>
            <p><span className="text-slate-400">Client:</span> {debug.dhan.client_id_masked || '-'}</p>
            <p><span className="text-slate-400">Token:</span> {debug.dhan.access_token_present ? 'present' : 'missing'}</p>
            <p><span className="text-slate-400">Last Dhan status:</span> {debug.last_dhan_status_code ?? '-'}</p>
          </div>
          {debug.issues.length > 0 && <p className="text-sm text-red-300">{debug.issues.join(' ')}</p>}
          {debug.warnings.length > 0 && <p className="text-sm text-amber-300">{debug.warnings.join(' ')}</p>}
        </section>
      )}
    </div>
  )
}
