import { useEffect, useState } from 'react'
import { AlertTriangle, Copy, Pause, RefreshCw, ShieldAlert, Square, Wallet } from 'lucide-react'
import {
  emergencyStop,
  getDashboardSummary,
  getLiveFlow,
  getOrders,
  getSetupStatus,
  stopEngine,
} from '../api/dashboard'
import { DashboardSummary, LiveFlowStep, OrderEvent, SetupStatus } from '../types'

function currency(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return `Rs.${Number(value).toFixed(2)}`
}

function stateLabel(value: string | undefined) {
  return (value || 'WAITING_ENTRY').replace(/_/g, ' ').toLowerCase()
}

export default function DashboardPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [setup, setSetup] = useState<SetupStatus | null>(null)
  const [flow, setFlow] = useState<LiveFlowStep[]>([])
  const [orders, setOrders] = useState<OrderEvent[]>([])
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const loadData = async () => {
    const [summaryResponse, setupResponse, flowResponse, orderResponse] = await Promise.all([
      getDashboardSummary(),
      getSetupStatus(),
      getLiveFlow(),
      getOrders(),
    ])
    setSummary(summaryResponse.data)
    setSetup(setupResponse.data)
    setFlow(flowResponse.data)
    setOrders(orderResponse.data.slice(0, 8))
  }

  useEffect(() => {
    loadData().catch(() => setError('Backend dashboard APIs are not reachable.'))
    const interval = window.setInterval(() => {
      loadData().catch(() => undefined)
    }, 2000)
    return () => window.clearInterval(interval)
  }, [])

  const copyWebhook = () => {
    if (!setup?.webhook_url) return
    navigator.clipboard.writeText(setup.webhook_url)
    setMessage('Webhook URL copied.')
    window.setTimeout(() => setMessage(''), 2500)
  }

  const runEmergencyStop = async () => {
    if (!window.confirm('Activate emergency stop? New entry alerts will be blocked immediately.')) return
    await emergencyStop()
    setMessage('Emergency stop activated.')
    await loadData()
  }

  const runStopEngine = async () => {
    await stopEngine()
    setMessage('Engine stopped.')
    await loadData()
  }

  if (!summary || !setup) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center text-slate-300">
        <RefreshCw className="mr-3 animate-spin" size={20} />
        Loading dashboard
      </div>
    )
  }

  const latestAlert = summary.last_logs?.webhook as Record<string, unknown> | undefined
  const openPosition = summary.open_position
  const engineStarted = setup.engine_started

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Dashboard</h1>
          <p className="mt-1 text-sm text-slate-400">Alert lifecycle, current position, and order logs.</p>
        </div>
        <button onClick={() => loadData()} className="btn-ghost flex items-center gap-2 self-start">
          <RefreshCw size={16} /> Refresh
        </button>
      </div>

      {setup.mode.live_orders_enabled && (
        <div className="rounded-lg border-2 border-red-500 bg-red-950 p-4 text-red-100">
          <p className="font-bold">LIVE ORDERS ENABLED - real money orders can be placed.</p>
        </div>
      )}

      {message && <div className="rounded-md border border-emerald-700 bg-emerald-950 p-3 text-sm text-emerald-200">{message}</div>}
      {error && <div className="rounded-md border border-red-700 bg-red-950 p-3 text-sm text-red-200">{error}</div>}

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <div className="card">
          <p className="text-sm text-slate-400">Engine</p>
          <p className={engineStarted ? 'mt-2 text-xl font-semibold text-emerald-300' : 'mt-2 text-xl font-semibold text-amber-300'}>
            {engineStarted ? 'Started' : 'Stopped'}
          </p>
        </div>
        <div className="card">
          <p className="text-sm text-slate-400">Dhan</p>
          <p className={setup.dhan_connected ? 'mt-2 text-xl font-semibold text-emerald-300' : 'mt-2 text-xl font-semibold text-red-300'}>
            {setup.dhan_connected ? 'Connected' : 'Not connected'}
          </p>
          <p className="mt-1 font-mono text-xs text-slate-400">{setup.dhan_client_id_masked || '-'}</p>
        </div>
        <div className="card">
          <p className="text-sm text-slate-400">Current State</p>
          <p className="mt-2 text-xl font-semibold capitalize">{stateLabel(summary.app_state.state)}</p>
        </div>
        <div className="card">
          <p className="text-sm text-slate-400">Available Balance</p>
          <p className="mt-2 text-xl font-semibold">{currency(summary.wallet.available_balance)}</p>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="card space-y-4 lg:col-span-2">
          <h2 className="text-lg font-semibold">Alert Lifecycle</h2>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
            {flow.map((step) => (
              <div key={step.step} className="rounded-md border border-slate-800 bg-slate-950 p-3">
                <p className="text-xs uppercase text-slate-500">{step.step.replace(/_/g, ' ')}</p>
                <p className={step.status === 'done' ? 'mt-1 font-semibold text-emerald-300' : step.status === 'active' ? 'mt-1 font-semibold text-sky-300' : step.status === 'blocked' || step.status === 'error' ? 'mt-1 font-semibold text-red-300' : 'mt-1 font-semibold text-slate-300'}>
                  {step.status}
                </p>
                {step.message && <p className="mt-2 text-xs text-slate-400">{step.message}</p>}
              </div>
            ))}
          </div>
        </div>

        <div className="card space-y-4">
          <h2 className="text-lg font-semibold">Controls</h2>
          <button onClick={runStopEngine} className="btn-ghost flex w-full items-center justify-center gap-2">
            <Square size={16} /> Stop Engine
          </button>
          <button onClick={runEmergencyStop} className="btn-danger flex w-full items-center justify-center gap-2">
            <ShieldAlert size={16} /> Emergency Stop
          </button>
          <p className="text-sm text-slate-400">Stop Engine blocks new entries. Emergency Stop immediately blocks new entry risk checks.</p>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="card space-y-4">
          <div className="flex items-center gap-2">
            <Wallet className="text-sky-400" size={20} />
            <h2 className="text-lg font-semibold">Wallet</h2>
          </div>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <p className="text-slate-400">Available</p>
              <p className="font-semibold">{currency(summary.wallet.available_balance)}</p>
            </div>
            <div>
              <p className="text-slate-400">Utilized</p>
              <p className="font-semibold">{currency(summary.wallet.utilized_amount)}</p>
            </div>
            <div>
              <p className="text-slate-400">Mode</p>
              <p className={setup.mode.dhan_mode === 'REAL' ? 'font-semibold text-red-300' : 'font-semibold text-emerald-300'}>{setup.mode.dhan_mode}</p>
            </div>
            <div>
              <p className="text-slate-400">Outgoing IP</p>
              <p className="font-mono">{setup.outgoing_ip || '-'}</p>
            </div>
          </div>
        </div>

        <div className="card space-y-4">
          <h2 className="text-lg font-semibold">TradingView Webhook</h2>
          <div className="flex items-center gap-2 rounded-md border border-slate-800 bg-slate-950 p-3">
            <p className="min-w-0 flex-1 break-all font-mono text-sm">{setup.webhook_url || '-'}</p>
            <button onClick={copyWebhook} className="btn-ghost p-2" title="Copy webhook URL">
              <Copy size={16} />
            </button>
          </div>
          <p className="rounded-md border border-slate-800 bg-slate-950 p-3 font-mono text-sm">{'{{strategy.order.alert_message}}'}</p>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="card">
          <h2 className="mb-4 text-lg font-semibold">Open Position</h2>
          {openPosition.has_open_position ? (
            <div className="grid grid-cols-2 gap-3 text-sm">
              <p className="text-slate-400">Symbol</p>
              <p>{openPosition.trading_symbol || '-'}</p>
              <p className="text-slate-400">Security ID</p>
              <p>{openPosition.security_id || '-'}</p>
              <p className="text-slate-400">Quantity</p>
              <p>{openPosition.qty}</p>
              <p className="text-slate-400">Entry order</p>
              <p>{openPosition.entry_order_id || '-'}</p>
            </div>
          ) : (
            <p className="text-sm text-slate-400">No open position tracked.</p>
          )}
        </div>

        <div className="card">
          <h2 className="mb-4 text-lg font-semibold">Latest Alert</h2>
          {latestAlert ? (
            <div className="space-y-2 text-sm">
              <p><span className="text-slate-400">Type:</span> {String(latestAlert.event_type || '-')}</p>
              <p><span className="text-slate-400">Signal:</span> {String(latestAlert.signal_id || '-')}</p>
              <p><span className="text-slate-400">Payload:</span> {String(latestAlert.payload_format || '-')}</p>
              <p><span className="text-slate-400">Message:</span> {String(latestAlert.message || '-')}</p>
            </div>
          ) : (
            <p className="text-sm text-slate-400">Waiting for alert.</p>
          )}
        </div>
      </section>

      <section className="card">
        <h2 className="mb-4 text-lg font-semibold">Orders</h2>
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead className="border-b border-slate-800 text-slate-400">
              <tr>
                <th className="py-2 pr-4">Time</th>
                <th className="py-2 pr-4">Action</th>
                <th className="py-2 pr-4">Symbol</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Reason</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id} className="border-b border-slate-900">
                  <td className="py-2 pr-4 text-slate-400">{order.created_at ? new Date(order.created_at).toLocaleString() : '-'}</td>
                  <td className="py-2 pr-4">{order.action || order.normalized_action || '-'}</td>
                  <td className="py-2 pr-4">{order.trading_symbol || order.normalized_symbol || '-'}</td>
                  <td className="py-2 pr-4">{order.status || order.phase || '-'}</td>
                  <td className="py-2 pr-4 text-slate-400">{order.reason || '-'}</td>
                </tr>
              ))}
              {orders.length === 0 && (
                <tr>
                  <td className="py-4 text-slate-400" colSpan={5}>No order events yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
