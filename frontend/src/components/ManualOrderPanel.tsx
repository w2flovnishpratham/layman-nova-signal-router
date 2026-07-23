import { Loader2, LogOut, Repeat2, ShoppingCart, Sliders } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  getOrderQuotes,
  postManualEntry,
  postManualExit,
  postManualReverse,
  type ManualOrderResponse,
  type OrderQuote,
} from '../api'
import { formatCurrency } from '../lib/format'
import { applyManualOrderHydration } from '../state/sessionStore'
import { MotionProgressFill, softEase, useAppReducedMotion } from './MotionPrimitives'
import type { ActiveTrade, EngineMode } from '../types'
import { getContractForSide, hasOpenPositionProof } from './ManualOrderPanel.helpers'

interface Props {
  engineMode: EngineMode | null
  activeTrade: ActiveTrade | null
  runtimePositionOpen?: boolean
}

type Stage = 'idle' | 'Submitting order' | 'Waiting for fresh LTP' | 'Paper order accepted' | 'Confirming fill' | 'Opening position' | 'Position opened' | 'Position closed' | 'Reconciliation required' | 'Order not placed'

export function ManualOrderPanel({ engineMode, activeTrade, runtimePositionOpen = false }: Props) {
  const [lots, setLots] = useState(1)
  const [side, setSide] = useState<'CE' | 'PE'>('CE')
  const [targetProfitPct, setTargetProfitPct] = useState(20)
  const [stopLossPct, setStopLossPct] = useState(10)
  const [stage, setStage] = useState<Stage>('idle')
  const [pending, setPending] = useState(false)
  // Synchronous single-flight guard: blocks a rapid second click BEFORE the
  // `pending` state re-render disables the buttons (state updates are async).
  const inFlightRef = useRef(false)
  const retryOperationRef = useRef<{ key: string; id: string } | null>(null)
  const [activeAction, setActiveAction] = useState<'entry-CE' | 'entry-PE' | 'exit' | 'reverse' | null>(null)
  const [quotePending, setQuotePending] = useState(false)
  // Two-sided quote cache: Buy CE / Buy PE each resolve their own contract,
  // regardless of which side the advanced panel dropdown is set to.
  const [quotes, setQuotes] = useState<{ CE: OrderQuote | null; PE: OrderQuote | null }>({ CE: null, PE: null })
  const [quoteStatus, setQuoteStatus] = useState<'loading' | 'ready' | 'stale' | 'error'>('loading')
  const [lastQuoteError, setLastQuoteError] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [confirmed, setConfirmed] = useState<ManualOrderResponse | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [exitConfirmOpen, setExitConfirmOpen] = useState(false)
  const [closedPositionKey, setClosedPositionKey] = useState<string | null>(null)
  const advancedToggleRef = useRef<HTMLButtonElement | null>(null)
  const advancedRegionRef = useRef<HTMLDivElement | null>(null)
  const advancedScrollTimerRef = useRef<number | null>(null)
  const advancedScrollReadyRef = useRef(false)
  const reduceMotion = useAppReducedMotion()
  
  const live = engineMode === 'live'
  const quote = quotes[side]
  const marketClosed = quote?.atm?.marketOpen === false
  const ceContract = getContractForSide(quotes.CE, 'CE')
  const peContract = getContractForSide(quotes.PE, 'PE')
  const currentPositionKey = activeTrade?.orderId ?? activeTrade?.symbol ?? 'runtime-position'
  const hasExposure = (runtimePositionOpen || Boolean(activeTrade)) && closedPositionKey !== currentPositionKey
  const advancedTransition = reduceMotion ? { duration: 0 } : { duration: 0.26, ease: softEase }

  useEffect(() => {
    if (!engineMode || pending) return
    let cancelled = false
    let inFlight = false

    async function loadQuotes() {
      if (inFlight) return
      inFlight = true
      setQuotePending(true)
      const result = await Promise.allSettled([getOrderQuotes(lots)])
      if (cancelled) {
        inFlight = false
        return
      }
      const quoteResult = result[0]
      if (quoteResult.status === 'fulfilled') {
        setQuotes(quoteResult.value)
        setQuoteStatus('ready')
        setLastQuoteError(null)
      } else {
        const failure = quoteResult.reason
        // Sanitized: only the error message, never headers/tokens/payloads.
        setLastQuoteError(failure instanceof Error ? failure.message : 'Quote request failed.')
        setQuoteStatus((prev) => (prev === 'ready' || prev === 'stale' ? 'stale' : 'error'))
      }
      inFlight = false
      setQuotePending(false)
    }

    void loadQuotes()
    const interval = window.setInterval(() => void loadQuotes(), 8000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [engineMode, lots, pending])

  useEffect(() => {
    if (!advancedScrollReadyRef.current) {
      advancedScrollReadyRef.current = true
      return
    }

    if (advancedScrollTimerRef.current !== null) {
      window.clearTimeout(advancedScrollTimerRef.current)
    }
    advancedScrollTimerRef.current = window.setTimeout(() => {
      const target = showAdvanced ? advancedRegionRef.current : advancedToggleRef.current
      target?.scrollIntoView({
        behavior: reduceMotion ? 'auto' : 'smooth',
        block: showAdvanced ? 'start' : 'nearest',
      })
    }, showAdvanced ? 140 : 60)

    return () => {
      if (advancedScrollTimerRef.current !== null) {
        window.clearTimeout(advancedScrollTimerRef.current)
        advancedScrollTimerRef.current = null
      }
    }
  }, [reduceMotion, showAdvanced])

  async function runOrder(action: 'entry' | 'exit' | 'reverse', entrySide: 'CE' | 'PE' = side) {
    // Ref guard fires synchronously, so a double-click can't send two orders.
    if (inFlightRef.current || pending) return
    // Never submit an entry without a fully resolved contract for the CLICKED
    // side. The backend live guard would block it anyway; catch it here first.
    if (action === 'entry' && !getContractForSide(quotes[entrySide], entrySide)) {
      setStage('Order not placed')
      setMessage(`Quote not ready for ${entrySide}. Please wait for ATM ${entrySide} contract to resolve.`)
      return
    }
    inFlightRef.current = true
    setActiveAction(action === 'entry' ? (entrySide === 'CE' ? 'entry-CE' : 'entry-PE') : action)
    setPending(true)
    setMessage('')
    setConfirmed(null)
    const operationKey = `${action}:${entrySide}`
    const operationId = retryOperationRef.current?.key === operationKey
      ? retryOperationRef.current.id
      : newManualOperationId()
    retryOperationRef.current = { key: operationKey, id: operationId }
    try {
      const response = await withProgress(action, entrySide, operationId)
      const operationState = response.operationState
      if (operationState === 'POSITION_OPEN' && response.ok && hasOpenPositionProof(response)) {
        setStage('Position opened')
        setConfirmed(response)
        applyManualOrderHydration(response)
        setClosedPositionKey(null)
      } else if (operationState === 'POSITION_CLOSED' && response.ok) {
        setStage('Position closed')
        setConfirmed(response)
        applyManualOrderHydration(response)
        setClosedPositionKey(currentPositionKey)
        setExitConfirmOpen(false)
      } else if (operationState === 'RECONCILIATION_REQUIRED') {
        setStage('Reconciliation required')
      } else if (operationState === 'PAPER_ORDER_ACCEPTED') {
        setStage('Paper order accepted')
      } else {
        setStage('Order not placed')
      }
      if (operationState !== 'PAPER_ORDER_ACCEPTED' && operationState !== 'RECONCILIATION_REQUIRED') {
        retryOperationRef.current = null
      }
      setMessage(response.message)
    } catch (error) {
      setStage('Order not placed')
      setMessage(error instanceof Error ? error.message : 'Manual order failed.')
    } finally {
      inFlightRef.current = false
      setPending(false)
      setActiveAction(null)
    }
  }

  async function withProgress(action: 'entry' | 'exit' | 'reverse', entrySide: 'CE' | 'PE', operationId: string): Promise<ManualOrderResponse> {
    setStage('Submitting order')
    await pause(120)
    setStage('Waiting for fresh LTP')
    await pause(120)
    setStage('Paper order accepted')
    await pause(120)
    setStage('Confirming fill')
    // Resolve the contract for the CLICKED side from that side's own quote —
    // not from whichever side the panel happened to be polling for.
    const quotedContract = getContractForSide(quotes[entrySide], entrySide) ?? {}
    const response = action === 'entry'
      ? await postManualEntry({ side: entrySide, lots, targetProfitPct, stopLossPct, ...quotedContract }, operationId)
      : action === 'exit'
        ? await postManualExit(operationId)
        : await postManualReverse(lots, operationId)
    setStage('Opening position')
    await pause(120)
    return response
  }

  return (
    <section className="sidebar-card manual-order-card">
      <div className="sidebar-title">
        <span>Manual Order</span>
        <span className={`sidebar-mode-chip ${engineMode ?? 'unset'}`}>{engineMode ?? 'unset'}</span>
      </div>

      {/* Main Order Action Buttons (Side-by-side CE and PE) */}
      <div className="flex gap-2 mt-4">
        <ActionButton
          live={live}
          disabled={pending || marketClosed || !ceContract}
          onConfirm={() => void runOrder('entry', 'CE')}
          loading={activeAction === 'entry-CE'}
          loadingLabel="Buying CE…"
          ariaLabel="Buy CE market"
          className="flex-1 py-3 px-4 rounded-xl font-semibold border border-emerald-500/20 hover:border-emerald-500/50 bg-emerald-500/10 text-emerald-400 hover:text-white transition-all duration-150 flex items-center justify-center gap-2 text-sm disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
        >
          <ShoppingCart size={13} />
          Buy CE
        </ActionButton>
        <ActionButton
          live={live}
          disabled={pending || marketClosed || !peContract}
          onConfirm={() => void runOrder('entry', 'PE')}
          loading={activeAction === 'entry-PE'}
          loadingLabel="Buying PE…"
          ariaLabel="Buy PE market"
          className="flex-1 py-3 px-4 rounded-xl font-semibold border border-rose-500/20 hover:border-rose-500/50 bg-rose-500/10 text-rose-400 hover:text-white transition-all duration-150 flex items-center justify-center gap-2 text-sm disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
        >
          <ShoppingCart size={13} />
          Buy PE
        </ActionButton>
      </div>
      {!marketClosed && (!ceContract || !peContract) ? (
        <p className="manual-quote-hint mt-2" role="status">
          {quoteStatus === 'error' || quoteStatus === 'stale'
            ? 'ATM contract unavailable. Retrying quote…'
            : 'Resolving ATM contract…'}
          {lastQuoteError && (quoteStatus === 'error' || quoteStatus === 'stale') ? ` (${lastQuoteError})` : ''}
        </p>
      ) : null}

      {hasExposure ? (
        <div className="manual-exit-primary" role="region" aria-label="Open position controls">
          <div>
            <strong>Position open</strong>
            <span>{activeTrade?.symbol ?? 'Backend-tracked Paper position'} · Qty {activeTrade?.qty ?? 'confirmed by server'}</span>
          </div>
          <button
            type="button"
            className="manual-exit-button"
            disabled={pending}
            onClick={() => setExitConfirmOpen(true)}
          >
            <LogOut size={13} /> Exit Position
          </button>
        </div>
      ) : null}

      {exitConfirmOpen && hasExposure ? (
        <div className="manual-exit-confirm" role="dialog" aria-modal="true" aria-label="Confirm Paper position exit">
          <strong>Exit the tracked position?</strong>
          <span>NOVA will submit one idempotent exit and confirm the position is flat.</span>
          <div>
            <button type="button" className="manual-exit-button" disabled={pending} onClick={() => void runOrder('exit')}>
              {activeAction === 'exit' ? <Loader2 size={13} className="animate-spin" /> : <LogOut size={13} />}
              {activeAction === 'exit' ? 'Exit pending…' : 'Confirm Exit'}
            </button>
            <button type="button" className="secondary-button" disabled={pending} onClick={() => setExitConfirmOpen(false)}>Cancel</button>
          </div>
        </div>
      ) : null}

      {/* Lots Stepper Stepper Component */}
      <div className="flex flex-col gap-1.5 mt-4 select-none">
        <span className="text-xs text-white/50 font-medium">Lots</span>
        <div className="flex items-center gap-1 bg-[#161421] border border-white/5 rounded-lg w-max p-1">
          <button
            type="button"
            className="w-8 h-8 flex items-center justify-center hover:bg-white/5 active:scale-95 text-lg font-bold rounded-md transition-all border border-transparent disabled:opacity-30 cursor-pointer"
            disabled={pending || lots <= 1}
            onClick={() => setLots(prev => Math.max(1, prev - 1))}
          >
            -
          </button>
          <span className="text-sm font-semibold px-4 min-w-8 text-center text-white">{lots}</span>
          <button
            type="button"
            className="w-8 h-8 flex items-center justify-center hover:bg-white/5 active:scale-95 text-lg font-bold rounded-md transition-all border border-transparent disabled:opacity-30 cursor-pointer"
            disabled={pending || lots >= 20}
            onClick={() => setLots(prev => Math.min(20, prev + 1))}
          >
            +
          </button>
        </div>
      </div>

      {/* Collapsible Advanced Toggle */}
      <button
        ref={advancedToggleRef}
        type="button"
        className="mt-4 text-[11px] font-semibold text-white/40 hover:text-white/70 flex items-center gap-1.5 transition-all bg-transparent border-0 p-0 self-start cursor-pointer"
        onClick={() => setShowAdvanced(!showAdvanced)}
        aria-expanded={showAdvanced}
        aria-controls="manual-order-advanced"
      >
        <Sliders size={12} />
        {showAdvanced ? 'Hide Advanced Options' : 'Show Advanced Options'}
      </button>

      {/* Collapsible Content */}
      <AnimatePresence initial={false}>
        {showAdvanced ? (
          <motion.div
            id="manual-order-advanced"
            ref={advancedRegionRef}
            className="manual-advanced-motion is-open"
            initial={{ height: 0, opacity: 0, y: -6 }}
            animate={{ height: 'auto', opacity: 1, y: 0 }}
            exit={{ height: 0, opacity: 0, y: -6 }}
            transition={advancedTransition}
            aria-hidden={false}
          >
            <div className="manual-advanced-content">
              <div className="manual-controls-grid">
                <label>
                  <span>Side</span>
                  <select value={side} disabled={pending} onChange={(event) => setSide(event.target.value as 'CE' | 'PE')} aria-label="Manual order side">
                    <option value="CE">CE</option>
                    <option value="PE">PE</option>
                  </select>
                </label>
                <label>
                  <span>Target %</span>
                  <input type="number" min={0} value={targetProfitPct} disabled={pending} onChange={(event) => setTargetProfitPct(Number(event.target.value) || 0)} aria-label="Manual target profit percent" />
                </label>
                <label>
                  <span>Stop %</span>
                  <input type="number" min={0} value={stopLossPct} disabled={pending} onChange={(event) => setStopLossPct(Number(event.target.value) || 0)} aria-label="Manual stop loss percent" />
                </label>
              </div>

              <div className="manual-estimate-grid">
                <div><span>Premium</span><strong>{quote?.estimatedPremium ? formatCurrency(quote.estimatedPremium, { decimals: 2 }) : 'Pending'}</strong></div>
                <div><span>Cost/Margin</span><strong>{quote?.estimatedCost ? formatCurrency(quote.estimatedCost, { decimals: 0 }) : 'Pending'}</strong></div>
              </div>

              <div className="manual-atm-row">
                <span>{quote?.atm?.marketOpen === false ? 'Market closed' : quote?.atm?.atmStrike ? `ATM ${quote.atm.atmStrike.toLocaleString()} ${side}` : 'ATM calculating'}</span>
                <strong>{quote?.tradingSymbol ?? 'Waiting for contract'}</strong>
                <small>{quoteSourceLabel(quote)}{quotePending ? ' - updating' : ''}</small>
              </div>

              <div className="flex gap-2">
                <ActionButton
                  live={live}
                  disabled={pending || marketClosed || !hasExposure}
                  onConfirm={() => void runOrder('reverse')}
                  loading={activeAction === 'reverse'}
                  ariaLabel="Reverse position"
                  className="flex-1 py-2 px-3 rounded-lg text-white/80 hover:text-white bg-white/5 hover:bg-white/10 border border-white/10 transition-all text-xs font-semibold flex items-center justify-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
                >
                  <Repeat2 size={12} />
                  Reverse Position
                </ActionButton>
              </div>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      {/* Progress and status message */}
      {stage !== 'idle' && (
        <div className={`manual-progress mt-4 stage-${stage === 'Order not placed' || stage === 'Reconciliation required' ? 'failed' : stage === 'Position opened' || stage === 'Position closed' ? 'done' : 'active'}`}>
          <span>{stage}</span>
        </div>
      )}
      {message ? <p className="manual-status-message mt-2">{message}</p> : null}
      {confirmed?.operationState === 'POSITION_OPEN' && confirmed.position ? (
        <div className="manual-position-proof mt-3" aria-label="Confirmed open position">
          <strong>Position opened</strong>
          <span>{confirmed.position.trading_symbol || confirmed.position.security_id}</span>
          <span>Entry {formatCurrency(confirmed.position.entry_price ?? 0, { decimals: 2 })} · Qty {confirmed.position.qty}</span>
          <span>Margin {formatCurrency(confirmed.portfolio?.utilized_amount ?? 0, { decimals: 2 })}</span>
          <span>SL {confirmed.position.broker_sl_price ?? 'Server'} · TP {confirmed.position.broker_tp_price ?? 'Server'}</span>
        </div>
      ) : null}
    </section>
  )
}

function ActionButton({ live, disabled, loading = false, loadingLabel, onConfirm, ariaLabel, className, children }: {
  live: boolean
  disabled: boolean
  loading?: boolean
  loadingLabel?: string
  onConfirm: () => void
  ariaLabel: string
  className?: string
  children: ReactNode
}) {
  const content = loading
    ? (<><Loader2 size={13} className="animate-spin" /> {loadingLabel ?? 'Working…'}</>)
    : children
  if (!live) {
    return (
      <button type="button" className={className || "manual-action-button"} disabled={disabled || loading} aria-busy={loading} onClick={onConfirm} aria-label={ariaLabel}>
        {content}
      </button>
    )
  }
  return (
    <HoldButton disabled={disabled || loading} loading={loading} onConfirm={onConfirm} ariaLabel={`${ariaLabel}, hold to confirm`} className={className}>
      {content}
    </HoldButton>
  )
}

function HoldButton({ disabled, loading = false, onConfirm, ariaLabel, className, children }: {
  disabled: boolean
  loading?: boolean
  onConfirm: () => void
  ariaLabel: string
  className?: string
  children: ReactNode
}) {
  const timer = useRef<number | null>(null)
  const [holding, setHolding] = useState(false)

  function cancel() {
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
    setHolding(false)
  }

  function start() {
    if (disabled) return
    cancel()
    setHolding(true)
    timer.current = window.setTimeout(() => {
      timer.current = null
      setHolding(false)
      onConfirm()
    }, 900)
  }

  return (
    <button
      type="button"
      className={`${className || "manual-action-button hold-live"} ${holding ? 'holding' : ''}`}
      disabled={disabled}
      aria-busy={loading}
      onPointerDown={start}
      onPointerUp={cancel}
      onPointerLeave={cancel}
      onPointerCancel={cancel}
      onKeyDown={(event) => {
        if ((event.key === 'Enter' || event.key === ' ') && !event.repeat) start()
      }}
      onKeyUp={(event) => {
        if (event.key === 'Enter' || event.key === ' ') cancel()
      }}
      aria-label={ariaLabel}
      title="Hold to confirm live action"
    >
      {holding ? <MotionProgressFill durationSeconds={0.9} tone="live" /> : null}
      <span className="hold-button-content">{children}</span>
    </button>
  )
}

function pause(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function newManualOperationId(): string {
  return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `manual-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

function quoteSourceLabel(quote: OrderQuote | null): string {
  const option = quote?.atm?.options?.[quote.side]
  const source = option?.ltpSource || option?.ltpStatus
  if (!source) return 'Source pending'
  if (source.includes('marketfeed_ws') || source === 'websocket') return 'Dhan WebSocket'
  if (source.includes('rest') || source === 'rest') return 'Dhan REST fallback'
  if (source === 'market_closed') return 'Market closed'
  return source.replace(/_/g, ' ')
}

/** Resolve the tradeable ATM contract for a given side from that side's quote.
 *
 * Returns null unless securityId, strike, AND expiry are all present — a
 * partial contract must never be submitted (live mode does not auto-resolve).
 * Does NOT require `quote.side === side`: if the quote payload carries the
 * requested side in `atm.options`, that contract is used directly.
 */
