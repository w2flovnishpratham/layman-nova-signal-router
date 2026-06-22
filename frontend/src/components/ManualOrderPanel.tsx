import { LogOut, Repeat2, ShoppingCart, Sliders } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  getOrderQuote,
  postManualEntry,
  postManualExit,
  postManualReverse,
  type ManualOrderResponse,
  type OrderQuote,
} from '../api'
import { formatCurrency } from '../lib/format'
import { MotionProgressFill, softEase, useAppReducedMotion } from './MotionPrimitives'
import type { ActiveTrade, EngineMode } from '../types'

interface Props {
  engineMode: EngineMode | null
  activeTrade: ActiveTrade | null
}

type Stage = 'idle' | 'Checking LTP' | 'Checking funds' | 'Validating risk' | 'Placing order' | 'Verifying fill' | 'Done' | 'Failed'

export function ManualOrderPanel({ engineMode, activeTrade }: Props) {
  const [lots, setLots] = useState(1)
  const [side, setSide] = useState<'CE' | 'PE'>('CE')
  const [targetProfitPct, setTargetProfitPct] = useState(20)
  const [stopLossPct, setStopLossPct] = useState(10)
  const [stage, setStage] = useState<Stage>('idle')
  const [pending, setPending] = useState(false)
  const [quotePending, setQuotePending] = useState(false)
  const [quote, setQuote] = useState<OrderQuote | null>(null)
  const [message, setMessage] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const advancedToggleRef = useRef<HTMLButtonElement | null>(null)
  const advancedRegionRef = useRef<HTMLDivElement | null>(null)
  const advancedScrollTimerRef = useRef<number | null>(null)
  const reduceMotion = useAppReducedMotion()
  
  const live = engineMode === 'live'
  const marketClosed = quote?.atm?.marketOpen === false
  const advancedTransition = reduceMotion ? { duration: 0 } : { duration: 0.26, ease: softEase }

  useEffect(() => {
    if (!engineMode || pending) return
    let cancelled = false
    let inFlight = false

    async function loadQuote() {
      if (inFlight) return
      inFlight = true
      setQuotePending(true)
      try {
        const nextQuote = await getOrderQuote(side, lots)
        if (!cancelled) setQuote(nextQuote)
      } catch {
        // Keep the last usable quote visible while the auto quote poll recovers.
      } finally {
        inFlight = false
        if (!cancelled) setQuotePending(false)
      }
    }

    void loadQuote()
    const interval = window.setInterval(() => void loadQuote(), 4000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [engineMode, lots, pending, side])

  useEffect(() => {
    if (advancedScrollTimerRef.current !== null) {
      window.clearTimeout(advancedScrollTimerRef.current)
    }
    advancedScrollTimerRef.current = window.setTimeout(() => {
      const target = showAdvanced ? advancedRegionRef.current : advancedToggleRef.current
      target?.scrollIntoView({
        behavior: 'smooth',
        block: showAdvanced ? 'start' : 'nearest',
      })
    }, showAdvanced ? 140 : 60)

    return () => {
      if (advancedScrollTimerRef.current !== null) {
        window.clearTimeout(advancedScrollTimerRef.current)
        advancedScrollTimerRef.current = null
      }
    }
  }, [showAdvanced])

  async function runOrder(action: 'entry' | 'exit' | 'reverse', entrySide: 'CE' | 'PE' = side) {
    if (pending) return
    setPending(true)
    setMessage('')
    try {
      const response = await withProgress(action, entrySide)
      setStage(response.ok ? 'Done' : 'Failed')
      setMessage(response.message)
    } catch (error) {
      setStage('Failed')
      setMessage(error instanceof Error ? error.message : 'Manual order failed.')
    } finally {
      setPending(false)
    }
  }

  async function withProgress(action: 'entry' | 'exit' | 'reverse', entrySide: 'CE' | 'PE'): Promise<ManualOrderResponse> {
    setStage('Checking LTP')
    await pause(120)
    setStage('Checking funds')
    await pause(120)
    setStage('Validating risk')
    await pause(120)
    setStage('Placing order')
    const quotedContract = quote?.side === entrySide ? quoteContractPayload(quote, entrySide) : {}
    const response = action === 'entry'
      ? await postManualEntry({ side: entrySide, lots, targetProfitPct, stopLossPct, ...quotedContract })
      : action === 'exit'
        ? await postManualExit()
        : await postManualReverse(lots)
    setStage('Verifying fill')
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
          disabled={pending || marketClosed}
          onConfirm={() => void runOrder('entry', 'CE')}
          ariaLabel="Buy CE market"
          className="flex-1 py-3 px-4 rounded-xl font-semibold border border-emerald-500/20 hover:border-emerald-500/50 bg-emerald-500/10 text-emerald-400 hover:text-white transition-all duration-150 flex items-center justify-center gap-2 text-sm disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
        >
          <ShoppingCart size={13} />
          Buy CE
        </ActionButton>
        <ActionButton
          live={live}
          disabled={pending || marketClosed}
          onConfirm={() => void runOrder('entry', 'PE')}
          ariaLabel="Buy PE market"
          className="flex-1 py-3 px-4 rounded-xl font-semibold border border-rose-500/20 hover:border-rose-500/50 bg-rose-500/10 text-rose-400 hover:text-white transition-all duration-150 flex items-center justify-center gap-2 text-sm disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
        >
          <ShoppingCart size={13} />
          Buy PE
        </ActionButton>
      </div>

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
                  disabled={pending || marketClosed || !activeTrade}
                  onConfirm={() => void runOrder('exit')}
                  ariaLabel="Exit current position"
                  className="flex-1 py-2 px-3 rounded-lg text-white/80 hover:text-white bg-white/5 hover:bg-white/10 border border-white/10 transition-all text-xs font-semibold flex items-center justify-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
                >
                  <LogOut size={12} />
                  Exit Position
                </ActionButton>
                <ActionButton
                  live={live}
                  disabled={pending || marketClosed || !activeTrade}
                  onConfirm={() => void runOrder('reverse')}
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
        <div className={`manual-progress mt-4 stage-${stage === 'Failed' ? 'failed' : stage === 'Done' ? 'done' : 'active'}`}>
          <span>{stage}</span>
        </div>
      )}
      {message ? <p className="manual-status-message mt-2">{message}</p> : null}
    </section>
  )
}

function ActionButton({ live, disabled, onConfirm, ariaLabel, className, children }: {
  live: boolean
  disabled: boolean
  onConfirm: () => void
  ariaLabel: string
  className?: string
  children: ReactNode
}) {
  if (!live) {
    return (
      <button type="button" className={className || "manual-action-button"} disabled={disabled} onClick={onConfirm} aria-label={ariaLabel}>
        {children}
      </button>
    )
  }
  return (
    <HoldButton disabled={disabled} onConfirm={onConfirm} ariaLabel={`${ariaLabel}, hold to confirm`} className={className}>
      {children}
    </HoldButton>
  )
}

function HoldButton({ disabled, onConfirm, ariaLabel, className, children }: {
  disabled: boolean
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

function quoteSourceLabel(quote: OrderQuote | null): string {
  const option = quote?.atm?.options?.[quote.side]
  const source = option?.ltpSource || option?.ltpStatus
  if (!source) return 'Source pending'
  if (source.includes('marketfeed_ws') || source === 'websocket') return 'Dhan WebSocket'
  if (source.includes('rest') || source === 'rest') return 'Dhan REST fallback'
  if (source === 'market_closed') return 'Market closed'
  return source.replace(/_/g, ' ')
}

function quoteContractPayload(quote: OrderQuote, side: 'CE' | 'PE') {
  const option = quote.atm?.options?.[side]
  return {
    securityId: quote.securityId ?? option?.securityId ?? null,
    tradingSymbol: quote.tradingSymbol ?? option?.tradingSymbol ?? null,
    strike: option?.strike ?? quote.atm?.atmStrike ?? null,
    expiry: option?.expiry ?? null,
  }
}
