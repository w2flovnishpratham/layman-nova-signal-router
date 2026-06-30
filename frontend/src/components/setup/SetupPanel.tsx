import { FlaskConical, Loader2, Zap } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createRazorpaySubscription, getPaymentEntitlementStatus } from '../../api'
import type { PaymentEntitlementStatus } from '../../api'
import { SetupInfoCard } from '../messages/SetupInfoCard'
import { MotionSpinner } from '../MotionPrimitives'
import { formatCurrency, sideLabel } from '../../lib/format'
import { contractsForLots } from '../../lib/trading'
import type { ClientCommand, EngineMode, ExitRules, SetupDraft, SetupFlowStep, SetupState, SideFilter } from '../../types'

const DISABLED_STOP_LOSS_PCT = 99.9
const DEFAULT_CUSTOM_STOP_LOSS_PCT = 10

interface Props {
  state: SetupState
  flowStep: SetupFlowStep
  draft: SetupDraft
  lastError: string
  verifyPending: boolean
  commandPending: boolean
  lotSize: number
  sharedMarketData?: boolean
  onSend: (command: ClientCommand) => void
  onUserReply: (text: string) => void
  onDraft: (patch: Partial<SetupDraft>) => void
  onStep: (step: SetupFlowStep) => void
}

export function SetupPanel({
  state,
  flowStep,
  draft,
  lastError,
  verifyPending,
  commandPending,
  lotSize,
  sharedMarketData = false,
  onSend,
  onUserReply,
  onDraft,
  onStep,
}: Props) {
  const pendingExitsRef = useRef<ClientCommand['data'] | null>(null)

  useEffect(() => {
    if (state !== 'RISK_CONFIGURED' || !pendingExitsRef.current) return
    const exitsPayload = pendingExitsRef.current
    pendingExitsRef.current = null
    onSend({ type: 'setup.exits', data: exitsPayload })
  }, [onSend, state])

  if (state === 'LIVE' || state === 'PAUSED' || state === 'ENDED' || flowStep === 'complete') return null
  if (flowStep === 'mode') return <ModeStep draft={draft} onSelect={(engineMode, paperStartingBalance) => {
    onDraft({ engineMode, paperStartingBalance })
    onUserReply(engineMode === 'paper' ? `Paper mode with ${formatCurrency(paperStartingBalance)} virtual balance` : 'Live mode - real money routing')
    onSend({ type: 'setup.mode', data: { engineMode, paperStartingBalance } })
  }} />
  if (flowStep === 'live_access') return <LiveAccessStep onContinue={() => {
    onUserReply('Nova Static IP entitlement and proxy verification confirmed')
    onStep('strategy')
  }} />
  if (flowStep === 'strategy') return <StrategyStep onSelect={() => {
    onUserReply('Supertrend Strategy (NSE Options)')
    onSend({ type: 'setup.select_strategy', data: { strategy: 'supertrend' } })
  }} />
  if (flowStep === 'broker' && draft.engineMode === 'paper' && sharedMarketData) {
    return <SharedDataStep onContinue={() => {
      onUserReply('Use shared live market data (no Dhan account needed)')
      onSend({ type: 'setup.use_shared_data', data: {} })
    }} />
  }
  if (flowStep === 'broker') return <BrokerStep draft={draft} mode={draft.engineMode} error={lastError} pending={verifyPending} onDraft={onDraft} onSubmit={(clientId, accessToken) => {
    onUserReply(`Dhan credentials submitted for CLIENT: ${maskClientId(clientId)}`)
    onSend({ type: 'setup.broker_creds', data: { clientId, accessToken } })
  }} />
  if (flowStep === 'side') return <SideStep value={draft.side} onSelect={(side) => {
    onDraft({ side })
    onUserReply(`Trade side: ${sideLabel(side)}`)
    onStep('lots')
  }} />
  if (flowStep === 'lots') return <LotsStep value={draft.lots} lotSize={lotSize} onSelect={(lots) => {
    onDraft({ lots })
    onUserReply(`Lot size: ${lots} lot (${contractsForLots(lots, lotSize)} contracts)`)
    onStep('exits')
  }} />
  if (flowStep === 'exits') return <ExitRulesStep draft={draft} onSubmit={(patch) => {
    onDraft(patch)
    onUserReply(exitReply(
      patch.exitMode ?? draft.exitMode,
      patch.targetPct ?? draft.targetPct,
      patch.stopLossPct ?? draft.stopLossPct,
    ))
    onStep('limits')
  }} />
  if (flowStep === 'limits') return <DailyLimitsStep draft={draft} onSubmit={(patch) => {
    const finalDraft = { ...draft, ...patch }
    onDraft(patch)
    onUserReply(limitsReply(finalDraft.maxTrades, finalDraft.maxLoss))
    pendingExitsRef.current = {
      mode: finalDraft.exitMode,
      targetProfit: finalDraft.exitMode === 'flip_only' ? null : Math.max(100, finalDraft.targetPct * 100),
      targetPct: finalDraft.targetPct,
      stopLossPct: finalDraft.stopLossPct,
    }
    onSend({
      type: 'setup.risk',
      data: {
        maxTrades: finalDraft.maxTrades === 0 ? null : finalDraft.maxTrades,
        maxLoss: finalDraft.maxLoss === 0 ? null : finalDraft.maxLoss,
        lots: finalDraft.lots,
        side: finalDraft.side,
      },
    })
  }} />
  if (flowStep === 'confirm') return <DeploymentSummary draft={draft} lotSize={lotSize} pending={commandPending} onDeploy={() => {
    onUserReply(draft.engineMode === 'paper' ? 'Start Paper Simulation' : 'Deploy Live Strategy & Start Listening')
    onSend({ type: 'setup.confirm_live', data: {} })
  }} />
  return null
}

function ModeStep({ draft, onSelect }: { draft: SetupDraft; onSelect: (mode: EngineMode, balance: number) => void }) {
  const [paperBalance, setPaperBalance] = useState(draft.paperStartingBalance)
  return (
    <article className="setup-card mode-step">
      <div className="mode-choice-grid">
        <section className="mode-choice paper-choice">
          <div><FlaskConical size={18} /><strong>Paper</strong><span>Recommended</span></div>
          <p>Real Dhan market data and simulated fills. No real orders.</p>
          <label>
            Virtual starting balance
            <input
              type="number"
              min={10000}
              max={1000000}
              step={10000}
              value={paperBalance}
              onChange={(event) => setPaperBalance(Math.min(1000000, Math.max(10000, Number(event.target.value) || 10000)))}
            />
          </label>
          <button type="button" onClick={() => onSelect('paper', paperBalance)}>Start in Paper</button>
        </section>
        <section className="mode-choice live-choice">
          <div><Zap size={18} /><strong>Live</strong></div>
          <p>Routes real orders to Dhan. Real money is at risk and static IP is required.</p>
          <button type="button" onClick={() => onSelect('live', paperBalance)}>Configure Live</button>
        </section>
      </div>
    </article>
  )
}

function StrategyStep({ onSelect }: { onSelect: () => void }) {
  return (
    <article className="setup-card strategy-panel">
      <div className="strategy-chip-row" aria-label="Strategy choices">
        <button type="button" onClick={onSelect}>Supertrend</button>
        <button type="button" className="disabled-option" disabled>ORB</button>
        <button type="button" className="disabled-option" disabled>VWAP</button>
        <button type="button" className="disabled-option" disabled>RSI</button>
        <button type="button" className="disabled-option" disabled>Scalper</button>
      </div>
    </article>
  )
}

function SharedDataStep({ onContinue }: { onContinue: () => void }) {
  return (
    <article className="setup-card broker-panel">
      <div className="shared-data-step">
        <span className="shared-data-badge"><FlaskConical size={14} /> Paper mode</span>
        <h3>No Dhan account needed</h3>
        <p className="form-hint">
          Paper mode runs on NOVA's shared live market data feed. Your trades are simulated against
          real NIFTY option prices — no Dhan Client ID or token required, and no real money is ever at risk.
        </p>
        <button type="button" className="primary-button" onClick={onContinue}>
          Continue with shared market data
        </button>
      </div>
    </article>
  )
}

function LiveAccessStep({ onContinue }: { onContinue: () => void }) {
  const [staticIpReady, setStaticIpReady] = useState(false)
  const [entitlement, setEntitlement] = useState<PaymentEntitlementStatus | null>(null)
  const [paymentPolling, setPaymentPolling] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const autoContinuedRef = useRef(false)

  const refreshEntitlement = useCallback(async (): Promise<PaymentEntitlementStatus | null> => {
    try {
      const nextEntitlement = await getPaymentEntitlementStatus()
      setEntitlement(nextEntitlement)
      if (nextEntitlement.static_ip_enabled) {
        setRefreshKey((current) => current + 1)
      }
      return nextEntitlement
    } catch {
      setEntitlement(null)
      return null
    }
  }, [])

  useEffect(() => {
    void refreshEntitlement()
  }, [refreshEntitlement])

  useEffect(() => {
    if (!paymentPolling || staticIpReady || entitlement?.static_ip_enabled) return
    const interval = window.setInterval(() => {
      void refreshEntitlement()
      setRefreshKey((current) => current + 1)
    }, 2500)
    return () => window.clearInterval(interval)
  }, [entitlement?.static_ip_enabled, paymentPolling, refreshEntitlement, staticIpReady])

  useEffect(() => {
    if (!paymentPolling) return
    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') {
        void refreshEntitlement()
      }
    }
    const refreshWhenFocused = () => {
      void refreshEntitlement()
    }
    document.addEventListener('visibilitychange', refreshWhenVisible)
    window.addEventListener('focus', refreshWhenFocused)
    return () => {
      document.removeEventListener('visibilitychange', refreshWhenVisible)
      window.removeEventListener('focus', refreshWhenFocused)
    }
  }, [paymentPolling, refreshEntitlement])

  const staticIpEntitled = Boolean(entitlement?.valid && entitlement.static_ip_enabled)
  useEffect(() => {
    if (!staticIpEntitled) {
      setStaticIpReady(false)
    }
  }, [staticIpEntitled])

  useEffect(() => {
    if (!staticIpReady || !staticIpEntitled || autoContinuedRef.current) return
    autoContinuedRef.current = true
    window.setTimeout(() => {
      onContinue()
    }, 600)
  }, [onContinue, staticIpEntitled, staticIpReady])

  return (
    <article className="setup-card live-access-step">
      <div className="live-access-heading">
        <Zap size={16} />
        <div>
          <h3>{staticIpEntitled ? 'Verify Nova Static IP' : 'Activate Nova Premium'}</h3>
          <p>{staticIpEntitled ? 'Whitelist the assigned Nova Static IP in Dhan, then verify it before strategy setup.' : 'Complete Razorpay checkout first. NOVA reveals the assigned Static IP only after webhook-confirmed payment.'}</p>
        </div>
      </div>
      {staticIpEntitled ? (
        <>
          <NovaPremiumActiveNotice />
          <SetupInfoCard onReadyChange={setStaticIpReady} refreshKey={refreshKey} />
        </>
      ) : (
        <RazorpayTestCheckoutCard
          onCheckoutCreated={() => {
            setPaymentPolling(true)
            void refreshEntitlement()
          }}
        />
      )}
      {staticIpEntitled ? (
        <>
          <button type="button" className="primary-button" disabled={!staticIpReady} onClick={onContinue}>
            {staticIpReady ? 'Opening Strategy Setup...' : 'Continue to Strategy'}
          </button>
          {!staticIpReady ? <p className="form-hint">Whitelist and verify the assigned IP before strategy setup. Live and strategy access are checked again before deployment.</p> : null}
        </>
      ) : null}
    </article>
  )
}

function BrokerStep({
  draft,
  mode,
  error,
  pending,
  onDraft,
  onSubmit,
}: {
  draft: SetupDraft
  mode: EngineMode | null
  error: string
  pending: boolean
  onDraft: (patch: Partial<SetupDraft>) => void
  onSubmit: (clientId: string, accessToken: string) => void
}) {
  const [clientId, setClientId] = useState(draft.clientId)
  const [accessToken, setAccessToken] = useState(draft.accessToken)

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onDraft({ clientId, accessToken })
    onSubmit(clientId, accessToken)
  }

  return (
    <form className="setup-card form-card" onSubmit={submit}>
      <p className="form-hint">{mode === 'paper' ? 'Dhan credentials are used only for real market data. Paper mode never sends Dhan orders.' : 'Credentials are used for live market data and live order routing.'}</p>
      <label>
        Dhan Client ID
        <input value={clientId} onChange={(event) => setClientId(event.target.value)} required minLength={3} autoComplete="off" disabled={pending} />
      </label>
      <label>
        Access Token
        <input
          value={accessToken}
          onChange={(event) => setAccessToken(event.target.value)}
          required
          minLength={12}
          type="password"
          autoComplete="off"
          disabled={pending}
        />
      </label>
      {error ? <p className="form-error">{error}</p> : null}
      <button type="submit" disabled={pending}>
        {pending ? (
          <MotionSpinner>
            <Loader2 size={14} />
          </MotionSpinner>
        ) : null}
        {pending ? 'Verifying Dhan Account' : 'Connect & Verify Dhan Account'}
      </button>
    </form>
  )
}

function SideStep({ value, onSelect }: { value: SideFilter; onSelect: (side: SideFilter) => void }) {
  return (
    <article className="setup-card">
      <div className="choice-grid">
        {(['CE', 'PE', 'BOTH'] as SideFilter[]).map((side) => (
          <button key={side} className={value === side ? 'selected' : ''} type="button" onClick={() => onSelect(side)}>
            {sideLabel(side)}
          </button>
        ))}
      </div>
    </article>
  )
}

function LotsStep({ value, lotSize, onSelect }: { value: number; lotSize: number; onSelect: (lots: number) => void }) {
  const [lots, setLots] = useState(value)
  return (
    <article className="setup-card">
      <div className="counter-row large-counter">
        <button type="button" onClick={() => setLots((current) => Math.max(1, current - 1))}>-</button>
        <strong>{lots}</strong>
        <button type="button" onClick={() => setLots((current) => current + 1)}>+</button>
        <small>{contractsForLots(lots, lotSize)} contracts</small>
      </div>
      <button type="button" onClick={() => onSelect(lots)}>Use {lots} lot</button>
    </article>
  )
}

function ExitRulesStep({
  draft,
  onSubmit,
}: {
  draft: SetupDraft
  onSubmit: (patch: Partial<SetupDraft>) => void
}) {
  const [exitMode, setExitMode] = useState<ExitRules['mode']>(draft.exitMode)
  const [targetPct, setTargetPct] = useState(draft.targetPct)
  const [stopLossPct, setStopLossPct] = useState(draft.stopLossPct)
  const boundedTargetPct = clampPercent(targetPct)
  const boundedStopLossPct = clampStopLossPercent(stopLossPct)
  const effectiveStopLossPct = exitMode === 'custom' ? boundedStopLossPct : DISABLED_STOP_LOSS_PCT

  function selectExitMode(nextMode: ExitRules['mode']) {
    setExitMode(nextMode)
    if (nextMode === 'custom' && stopLossPct >= 80) {
      setStopLossPct(DEFAULT_CUSTOM_STOP_LOSS_PCT)
    }
    if (nextMode !== 'custom') {
      setStopLossPct(DISABLED_STOP_LOSS_PCT)
    }
  }

  return (
    <article className="setup-card">
      <div className="choice-grid three">
        <button className={exitMode === 'flip_only' ? 'selected' : ''} type="button" onClick={() => selectExitMode('flip_only')}>Flips Only</button>
        <button className={exitMode === 'flip_tp' ? 'selected' : ''} type="button" onClick={() => selectExitMode('flip_tp')}>Target Profit</button>
        <button className={exitMode === 'custom' ? 'selected' : ''} type="button" onClick={() => selectExitMode('custom')}>Custom SL & TP</button>
      </div>
      {exitMode !== 'flip_only' ? (
        <div className="tp-control">
          <label>
            Target profit
            <span className="percent-input">
              <input
                type="number"
                min={1}
                max={100}
                step={1}
                value={boundedTargetPct}
                onChange={(event) => setTargetPct(clampPercent(Number(event.target.value)))}
              />
              <span>%</span>
            </span>
          </label>
          <input type="range" min={1} max={100} value={boundedTargetPct} onChange={(event) => setTargetPct(Number(event.target.value))} />
        </div>
      ) : null}
      {exitMode === 'custom' ? (
        <div className="tp-control">
          <label>
            Stop loss
            <span className="percent-input">
              <input
                type="number"
                min={1}
                max={79}
                step={1}
                value={boundedStopLossPct}
                onChange={(event) => setStopLossPct(clampStopLossPercent(Number(event.target.value)))}
              />
              <span>%</span>
            </span>
          </label>
          <input type="range" min={1} max={79} value={boundedStopLossPct} onChange={(event) => setStopLossPct(Number(event.target.value))} />
        </div>
      ) : null}
      <button
        type="button"
        onClick={() => onSubmit({ exitMode, targetPct: boundedTargetPct, stopLossPct: effectiveStopLossPct })}
      >
        Confirm Exits Rules -&gt;
      </button>
    </article>
  )
}

function DailyLimitsStep({
  draft,
  onSubmit,
}: {
  draft: SetupDraft
  onSubmit: (patch: Pick<SetupDraft, 'maxTrades' | 'maxLoss'>) => void
}) {
  const [maxTrades, setMaxTrades] = useState(draft.maxTrades)
  const [maxLoss, setMaxLoss] = useState(draft.maxLoss)

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onSubmit({ maxTrades, maxLoss })
  }

  return (
    <form className="setup-card form-card launch-card" onSubmit={submit}>
      <label>
        Max trades per day
        <input type="number" min={0} max={50} value={maxTrades} onChange={(event) => setMaxTrades(Number(event.target.value))} />
      </label>
      <label>
        Max daily loss in INR
        <input type="number" min={0} step={100} value={maxLoss} onChange={(event) => setMaxLoss(Number(event.target.value))} />
      </label>
      <p className="form-hint">Use 0 for no limit.</p>
      <div className="trust-grid">
        <div><span>Strike source</span><strong>Live Dhan option chain</strong></div>
        <div><span>Margin check</span><strong>{draft.engineMode === 'paper' ? 'Virtual balance before fill' : 'Dhan margin API before order'}</strong></div>
        <div><span>Charges</span><strong>{draft.engineMode === 'paper' ? 'Simulated Dhan charge formula' : 'From real Dhan fills/reporting'}</strong></div>
      </div>
      <button className="live-confirm" type="submit">Save Limits & Finish</button>
    </form>
  )
}

function exitReply(mode: ExitRules['mode'], targetPct: number, stopLossPct: number): string {
  if (mode === 'flip_tp') return `Exit rule: Target Profit ${targetPct}% with SL disabled at ${DISABLED_STOP_LOSS_PCT}%`
  if (mode === 'custom') return `Exit rule: Custom SL ${stopLossPct}% & TP ${targetPct}%`
  return 'Exit rule: Flips Only'
}

function limitsReply(maxTrades: number, maxLoss: number): string {
  const trades = maxTrades === 0 ? 'No trade limit' : `${maxTrades} trades/day`
  const loss = maxLoss === 0 ? 'No daily loss limit' : `${formatCurrency(maxLoss)} max daily loss`
  return `Daily limits: ${trades} / ${loss}`
}

function DeploymentSummary({ draft, lotSize, pending, onDeploy }: { draft: SetupDraft; lotSize: number; pending: boolean; onDeploy: () => void }) {
  return (
    <article className="setup-card deployment-summary">
      <h3>Deployment Configuration Summary</h3>
      <div className={`deployment-mode ${draft.engineMode ?? 'unset'}`}>
        <strong>{draft.engineMode === 'paper' ? 'Paper simulation' : 'Live trading'}</strong>
        <span>{draft.engineMode === 'paper' ? `${formatCurrency(draft.paperStartingBalance)} virtual balance` : 'Real money will be at risk'}</span>
      </div>
      <div className="summary-grid">
        <div>
          <span>Dhan Connected</span>
          <strong>ID: {maskClientId(draft.clientId || 'pending')}</strong>
        </div>
        <div>
          <span>Strategy Route</span>
          <strong>Supertrend Options</strong>
        </div>
        <div>
          <span>Trade Volume</span>
          <strong>{draft.lots} Lot ({contractsForLots(draft.lots, lotSize)} contracts)</strong>
        </div>
        <div>
          <span>Allowed Sides</span>
          <strong>{allowedSideSummary(draft.side)}</strong>
        </div>
        <div>
          <span>Exit Rules</span>
          <strong>{exitReply(draft.exitMode, draft.targetPct, draft.stopLossPct).replace('Exit rule: ', '')}</strong>
        </div>
        <div>
          <span>Safety Limits</span>
          <strong>{draft.maxTrades ? `${draft.maxTrades} trades` : 'None'} | {draft.maxLoss ? formatCurrency(draft.maxLoss) : 'None'}</strong>
        </div>
      </div>
      <button className="live-confirm" type="button" onClick={onDeploy} disabled={pending}>
        {pending ? 'Starting Engine...' : draft.engineMode === 'paper' ? 'Start Paper Simulation' : 'Trade Real Money - Confirm'}
      </button>
    </article>
  )
}

function RazorpayTestCheckoutCard({
  onCheckoutCreated,
}: {
  onCheckoutCreated?: () => void
}) {
  const [pending, setPending] = useState(false)
  const [status, setStatus] = useState<'not_active' | 'pending_confirmation' | 'confirmed'>('not_active')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  async function startCheckout() {
    setPending(true)
    setError('')
    setMessage('')
    try {
      const checkout = await createRazorpaySubscription('premium_monthly')
      const checkoutUrl = checkout.checkout_url || checkout.short_url
      if (!checkoutUrl) {
        throw new Error('Checkout link was not returned.')
      }
      const opened = window.open(checkoutUrl, '_blank', 'noopener,noreferrer')
      if (!opened) {
        throw new Error('Allow pop-ups to open Razorpay checkout.')
      }
      setStatus('pending_confirmation')
      setMessage('Complete checkout, then return here. NOVA checks payment confirmation automatically.')
      onCheckoutCreated?.()
    } catch (checkoutError) {
      setError(checkoutError instanceof Error ? checkoutError.message : 'Could not start Razorpay checkout.')
    } finally {
      setPending(false)
    }
  }

  return (
    <section className="subscription-access-card" aria-live="polite">
      <div className="subscription-access-header">
        <h4>Activate Nova Premium</h4>
        <span className={status === 'pending_confirmation' ? 'subscription-pending' : ''}>
          {status === 'pending_confirmation' ? 'Pending payment confirmation' : 'Not active'}
        </span>
      </div>
      <p>Premium includes live orders, Nova Static IP, and strategy access. Test mode only; access activates after Razorpay webhook confirms payment.</p>
      <button className="checkout-button" type="button" disabled={pending} onClick={startCheckout}>
        {pending ? (
          <MotionSpinner>
            <Loader2 size={14} />
          </MotionSpinner>
        ) : null}
        {pending ? 'Creating Checkout' : 'Start Razorpay Test Checkout'}
      </button>
      {message ? <p className="subscription-status-row">{message}</p> : null}
      {error ? <p className="subscription-error">{error}</p> : null}
    </section>
  )
}

function NovaPremiumActiveNotice() {
  return (
    <section className="subscription-active-note" aria-live="polite">
      <div>
        <h4>Nova Premium active</h4>
        <p>Payment is confirmed. Verify the assigned Nova Static IP, then continue to strategy setup.</p>
      </div>
      <span>Active</span>
    </section>
  )
}

function allowedSideSummary(side: SideFilter): string {
  if (side === 'BOTH') return 'CE & PE Only'
  return `${side} Only`
}

function maskClientId(clientId: string): string {
  const suffix = clientId.slice(-4).padStart(4, '*')
  return `******${suffix}`
}

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) return 1
  return Math.min(100, Math.max(1, Math.round(value)))
}

function clampStopLossPercent(value: number): number {
  if (!Number.isFinite(value)) return 1
  return Math.min(79, Math.max(1, Math.round(value)))
}
