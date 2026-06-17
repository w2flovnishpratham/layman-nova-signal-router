import { LogOut, Repeat2, ShoppingCart } from 'lucide-react'
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
  const live = engineMode === 'live'
  const marketClosed = quote?.atm?.marketOpen === false

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
    const response = action === 'entry'
      ? await postManualEntry({ side: entrySide, lots, targetProfitPct, stopLossPct })
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

      <div className="manual-controls-grid">
        <label>
          <span>Lots</span>
          <input
            type="number"
            min={1}
            max={20}
            value={lots}
            disabled={pending}
            onChange={(event) => setLots(Math.max(1, Number(event.target.value) || 1))}
            aria-label="Manual order lots"
          />
        </label>
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

      <div className="manual-button-grid">
        <ActionButton live={live} disabled={pending || marketClosed} onConfirm={() => void runOrder('entry', 'CE')} ariaLabel="Buy CE market">
          <ShoppingCart size={13} />
          Buy CE Market
        </ActionButton>
        <ActionButton live={live} disabled={pending || marketClosed} onConfirm={() => void runOrder('entry', 'PE')} ariaLabel="Buy PE market">
          <ShoppingCart size={13} />
          Buy PE Market
        </ActionButton>
        <ActionButton live={live} disabled={pending || marketClosed || !activeTrade} onConfirm={() => void runOrder('exit')} ariaLabel="Exit current position">
          <LogOut size={13} />
          Exit Current Position
        </ActionButton>
        <ActionButton live={live} disabled={pending || marketClosed || !activeTrade} onConfirm={() => void runOrder('reverse')} ariaLabel="Reverse position">
          <Repeat2 size={13} />
          Reverse Position
        </ActionButton>
      </div>

      <div className={`manual-progress stage-${stage === 'Failed' ? 'failed' : stage === 'Done' ? 'done' : 'active'}`}>
        <span>{stage === 'idle' ? 'Ready' : stage}</span>
      </div>
      {message ? <p className="manual-status-message">{message}</p> : null}
    </section>
  )
}

function ActionButton({ live, disabled, onConfirm, ariaLabel, children }: {
  live: boolean
  disabled: boolean
  onConfirm: () => void
  ariaLabel: string
  children: ReactNode
}) {
  if (!live) {
    return (
      <button type="button" className="manual-action-button" disabled={disabled} onClick={onConfirm} aria-label={ariaLabel}>
        {children}
      </button>
    )
  }
  return (
    <HoldButton disabled={disabled} onConfirm={onConfirm} ariaLabel={`${ariaLabel}, hold to confirm`}>
      {children}
    </HoldButton>
  )
}

function HoldButton({ disabled, onConfirm, ariaLabel, children }: {
  disabled: boolean
  onConfirm: () => void
  ariaLabel: string
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
      className={`manual-action-button hold-live ${holding ? 'holding' : ''}`}
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
      {children}
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
