import { FormEvent, useEffect, useState } from 'react'
import { AlertTriangle, RefreshCw, ShieldAlert, Square } from 'lucide-react'
import {
  connectDhan,
  disconnectDhan,
  emergencyStop,
  getDhanDebugConfig,
  getSetupStatus,
  resumeTrading,
  saveWebhookSecret,
  setGlobalKillSwitch,
  stopEngine,
  updateRiskSettings,
} from '../api/dashboard'
import { DhanConnectPayload, DhanDebugConfig, RiskSettingsPatchPayload, RiskSetupPayload, SetupStatus } from '../types'

const MIN_WEBHOOK_SECRET_LENGTH = 5
const WEBHOOK_SECRET_LENGTH_ERROR = `Webhook secret must be at least ${MIN_WEBHOOK_SECRET_LENGTH} characters.`

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
    option_sl_percent: 10,
    option_tp_percent: 20,
    server_side_exit_enabled: true,
    marketfeed_ws_enabled: true,
    option_ltp_source: 'AUTO',
    option_exit_mode: 'DHAN_SUPER',
    option_ws_stale_seconds: 5,
    option_rest_fallback_enabled: true,
    option_rest_fallback_cooldown_seconds: 15,
    option_ltp_poll_seconds: 1,
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
    const payload: DhanConnectPayload = {}
    const trimmedClientId = clientId.trim()
    const trimmedAccessToken = accessToken.trim()
    if (trimmedClientId) payload.client_id = trimmedClientId
    if (trimmedAccessToken) payload.access_token = trimmedAccessToken

    if (!status?.dhan_client_id_masked && !payload.client_id) {
      setMessage('')
      setError('Dhan Client ID is required.')
      return
    }
    if (!status?.access_token_present && !payload.access_token) {
      setMessage('')
      setError('Dhan Access Token is required.')
      return
    }
    if (!payload.client_id && !payload.access_token) {
      setMessage('Saved Dhan credentials are already present. Enter a new value to update them.')
      setError('')
      return
    }

    await run('dhan', connectDhan(payload), 'Dhan credentials updated.')
    setClientId('')
    setAccessToken('')
  }

  const updateSecret = async (event: FormEvent) => {
    event.preventDefault()
    const trimmedSecret = webhookSecret.trim()
    if (trimmedSecret.length < MIN_WEBHOOK_SECRET_LENGTH) {
      setMessage('')
      setError(WEBHOOK_SECRET_LENGTH_ERROR)
      return
    }
    await run('secret', saveWebhookSecret(trimmedSecret), 'Webhook secret updated.')
    setWebhookSecret('')
  }

  const updateRisk = async (event: FormEvent) => {
    event.preventDefault()
    const payload: RiskSettingsPatchPayload = {
      max_qty_per_order: risk.max_qty_per_order,
      max_trades_per_day: risk.max_trades_per_day,
      daily_loss_limit: risk.daily_loss_limit,
      option_sl_percent: risk.option_sl_percent,
      option_tp_percent: risk.option_tp_percent,
      allow_entry: risk.allow_entry,
      allow_exit: risk.allow_exit,
    }
    await run('risk', updateRiskSettings(payload), 'Risk settings updated.')
  }

  if (!status) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center text-[#d8d3c8]">
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
          <p className="mt-1 text-sm text-[#9a968f]">Reconnect Dhan, rotate webhook secret, update risk, and control safety switches.</p>
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

      <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <form onSubmit={reconnectDhan} className="card space-y-4">
          <h2 className="text-lg font-semibold">Reconnect Dhan</h2>
          <div className="grid grid-cols-1 gap-2 text-sm text-[#9a968f] sm:grid-cols-2">
            <p>Saved client: <span className="font-mono text-[#d8d3c8]">{status.dhan_client_id_masked || '-'}</span></p>
            <p>Saved token: <span className="font-mono text-[#d8d3c8] break-all">{status.access_token_masked || (status.access_token_present ? 'present' : '-')}</span></p>
          </div>
          <label className="block text-sm">
            <span className="mb-1 block text-[#d8d3c8]">Dhan Client ID</span>
            <input className="input" value={clientId} onChange={(event) => setClientId(event.target.value)} autoComplete="off" placeholder={status.dhan_client_id_masked ? `Saved ${status.dhan_client_id_masked}` : 'Required'} required={!status.dhan_client_id_masked} />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-[#d8d3c8]">Dhan Access Token</span>
            <input className="input" type="password" value={accessToken} onChange={(event) => setAccessToken(event.target.value)} autoComplete="off" placeholder={status.access_token_masked ? `Saved ${status.access_token_masked}` : 'Required'} required={!status.access_token_present} />
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
          <p className="text-sm text-[#9a968f]">Saved secret: <span className="font-mono text-[#d8d3c8] break-all">{status.webhook_secret_masked || (status.webhook_secret_set ? 'present' : '-')}</span></p>
          <label className="block text-sm">
            <span className="mb-1 block text-[#d8d3c8]">New Webhook Secret</span>
            <input className="input" type="password" value={webhookSecret} onChange={(event) => setWebhookSecret(event.target.value)} autoComplete="off" minLength={MIN_WEBHOOK_SECRET_LENGTH} placeholder={status.webhook_secret_masked ? `Saved ${status.webhook_secret_masked}` : 'Required'} required />
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
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold">Risk Settings</h2>
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <label className="block text-sm">
            <span className="mb-1 block" style={{ color: 'var(--c-text-2)' }}>Max quantity per order</span>
            <input className="input no-spinner" type="number" min={1} value={risk.max_qty_per_order} onChange={(e) => setRisk({ ...risk, max_qty_per_order: Number(e.target.value) })} required />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block" style={{ color: 'var(--c-text-2)' }}>Max trades per day</span>
            <input className="input no-spinner" type="number" min={1} value={risk.max_trades_per_day} onChange={(e) => setRisk({ ...risk, max_trades_per_day: Number(e.target.value) })} required />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block" style={{ color: 'var(--c-text-2)' }}>Daily loss limit (INR)</span>
            <input className="input no-spinner" type="number" min={1} value={risk.daily_loss_limit} onChange={(e) => setRisk({ ...risk, daily_loss_limit: Number(e.target.value) })} required />
          </label>
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <label className="block text-sm">
            <span className="mb-1 block" style={{ color: 'var(--c-text-2)' }}>Option SL %</span>
            <input className="input no-spinner" type="number" min={0.1} step={0.1} value={risk.option_sl_percent ?? 10} onChange={(e) => setRisk({ ...risk, option_sl_percent: Number(e.target.value) })} required />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block" style={{ color: 'var(--c-text-2)' }}>Option TP %</span>
            <input className="input no-spinner" type="number" min={0.1} step={0.1} value={risk.option_tp_percent ?? 20} onChange={(e) => setRisk({ ...risk, option_tp_percent: Number(e.target.value) })} required />
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
          <p className="text-sm text-[#9a968f]">Engine started: {String(status.engine_started)}</p>
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
          <p className="text-sm text-[#9a968f]">Emergency stop: {String(status.settings.emergency_stop)}. Kill switch: {String(status.settings.global_kill_switch)}.</p>
        </div>
      </section>

      {status.debug_enabled && debug && (
        <section className="card space-y-4">
          <h2 className="text-lg font-semibold">Dhan Debug</h2>
          <div className="grid grid-cols-1 gap-3 text-sm md:grid-cols-3">
            <p><span className="text-[#9a968f]">Outgoing IP:</span> {debug.outgoing_ip || '-'}</p>
            <p><span className="text-[#9a968f]">Mode:</span> {debug.dhan.mode}</p>
            <p><span className="text-[#9a968f]">Live orders:</span> {String(debug.dhan.live_orders_enabled)}</p>
            <p><span className="text-[#9a968f]">Client:</span> {debug.dhan.client_id_masked || '-'}</p>
            <p><span className="text-[#9a968f]">Token:</span> {debug.dhan.access_token_present ? 'present' : 'missing'}</p>
            <p><span className="text-[#9a968f]">Last Dhan status:</span> {debug.last_dhan_status_code ?? '-'}</p>
          </div>
          {debug.issues.length > 0 && <p className="text-sm text-red-300">{debug.issues.join(' ')}</p>}
          {debug.warnings.length > 0 && <p className="text-sm text-amber-300">{debug.warnings.join(' ')}</p>}
        </section>
      )}
    </div>
  )
}
