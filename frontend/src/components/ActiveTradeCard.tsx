import { Check, ChevronDown, ExternalLink, Loader2, Pencil, Target, X } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { clsx } from 'clsx'
import { patchActiveExitLevels, patchActiveQuantity } from '../api'
import { formatCurrency, formatPercent } from '../lib/format'
import { TickingNumber } from './TickingNumber'
import { MotionSpinner, softEase, useAppReducedMotion } from './MotionPrimitives'
import type { ActiveExitLevels, ActiveTrade } from '../types'
import { lotsForQuantity } from '../lib/trading'

interface Props {
  trade: ActiveTrade
  lotSize: number
  compact?: boolean
  onApplySrSuggestion?: () => void
}

export function ActiveTradeCard({ trade, lotSize, compact = false, onApplySrSuggestion }: Props) {
  return (
    <ActiveTradeCardContent
      key={trade.orderId}
      trade={trade}
      lotSize={lotSize}
      compact={compact}
      onApplySrSuggestion={onApplySrSuggestion}
    />
  )
}

function ActiveTradeCardContent({ trade, lotSize, compact = false, onApplySrSuggestion }: Props) {
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [editingExitLevels, setEditingExitLevels] = useState(false)
  const [draftStopLoss, setDraftStopLoss] = useState('')
  const [draftTarget, setDraftTarget] = useState('')
  const [savingExitLevels, setSavingExitLevels] = useState(false)
  const [exitLevelStatus, setExitLevelStatus] = useState('')
  const [savedExitLevels, setSavedExitLevels] = useState<ActiveExitLevels | null>(null)
  const [editingQuantity, setEditingQuantity] = useState(false)
  const [draftLots, setDraftLots] = useState('')
  const [savingQuantity, setSavingQuantity] = useState(false)
  const [quantityStatus, setQuantityStatus] = useState('')
  const [savedQty, setSavedQty] = useState<number | null>(null)
  const reduceMotion = useAppReducedMotion()
  const tone = trade.pnl > 0 ? 'up' : trade.pnl < 0 ? 'down' : 'flat'
  const pnlPct = trade.pnlPct ?? 0
  const srSuggestion = trade.srSuggestion
  const srStop = srSuggestion?.stopLossPrice
  const srTarget = srSuggestion?.targetPrice
  const srReady = trade.mode === 'paper' && Boolean(srSuggestion?.available) && srStop !== undefined && srTarget !== undefined
  const activeExitLevels = savedExitLevels ?? trade.activeExitLevels ?? null
  const displayQty = savedQty ?? trade.qty
  const displayLots = lotsForQuantity(displayQty, lotSize)
  const srAccepted = Boolean(srSuggestion?.accepted || activeExitLevels?.source === 'sr_suggestion')
  const quoteUnavailable = trade.quoteStatus != null && !['ready', 'stale'].includes(trade.quoteStatus)
  const exitLevelText = activeExitLevels?.stopLossPrice && activeExitLevels?.targetPrice
    ? `SL ${formatCurrency(activeExitLevels.stopLossPrice, { decimals: 2 })} · TP ${formatCurrency(activeExitLevels.targetPrice, { decimals: 2 })}`
    : trade.riskArmed === true
      ? 'Server managed'
      : trade.riskArmed === false
        ? 'Not armed'
        : 'Risk status unavailable'

  function openQuantityEditor() {
    setDraftLots(String(Math.max(displayLots, 1)))
    setQuantityStatus('')
    setEditingExitLevels(false)
    setEditingQuantity(true)
  }

  function openExitLevelEditor() {
    const stop = activeExitLevels?.stopLossPrice ?? Math.max(trade.ltp * 0.9, 0.05)
    const target = activeExitLevels?.targetPrice ?? trade.ltp * 1.2
    setDraftStopLoss(stop.toFixed(2))
    setDraftTarget(target.toFixed(2))
    setExitLevelStatus('')
    setEditingQuantity(false)
    setEditingExitLevels(true)
  }

  async function saveQuantity() {
    if (savingQuantity) return
    const lots = Number(draftLots)
    if (!Number.isInteger(lots) || lots < 1 || lotSize < 1) {
      setQuantityStatus('Enter a valid whole lot count.')
      return
    }
    const qty = lots * lotSize
    setSavingQuantity(true)
    setQuantityStatus('')
    try {
      const response = await patchActiveQuantity({ qty })
      if (!response.ok) {
        setQuantityStatus(response.message || 'Could not update qty.')
        return
      }
      setSavedQty(response.qty ?? qty)
      setEditingQuantity(false)
      setQuantityStatus(response.message || 'Qty updated.')
    } catch (error) {
      setQuantityStatus(error instanceof Error ? error.message : 'Could not update qty.')
    } finally {
      setSavingQuantity(false)
    }
  }

  async function saveExitLevels() {
    if (savingExitLevels) return
    const stopLossPrice = Number(draftStopLoss)
    const targetPrice = Number(draftTarget)
    if (!Number.isFinite(stopLossPrice) || !Number.isFinite(targetPrice) || stopLossPrice <= 0 || targetPrice <= 0) {
      setExitLevelStatus('Enter valid positive prices.')
      return
    }
    setSavingExitLevels(true)
    setExitLevelStatus('')
    try {
      const response = await patchActiveExitLevels({ stopLossPrice, targetPrice })
      if (!response.ok) {
        setExitLevelStatus(response.message || 'Could not update SL/TP.')
        return
      }
      if (response.activeExitLevels) setSavedExitLevels(response.activeExitLevels)
      setEditingExitLevels(false)
      setExitLevelStatus(response.message || 'SL/TP updated.')
    } catch (error) {
      setExitLevelStatus(error instanceof Error ? error.message : 'Could not update SL/TP.')
    } finally {
      setSavingExitLevels(false)
    }
  }

  return (
    <section className={compact ? 'sidebar-trade-slot' : 'active-trade-slot'} aria-label="Active trade">
      <motion.article
        className={clsx('active-trade-card', `trade-${tone}`)}
        initial={reduceMotion ? { opacity: 1 } : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={reduceMotion ? { duration: 0 } : { duration: 0.24, ease: softEase }}
      >
        <div className="trade-card-top">
          <div className="trade-title-block">
              <span className={clsx('trade-state-pill', `pill-${tone}`)}>ACTIVE {trade.optType}</span>
            {trade.mode === 'paper' ? <span className="paper-trade-pill">PAPER</span> : null}
            <div>
              <h2>{trade.symbol}</h2>
              <p>{trade.expiry ?? 'weekly expiry'} / Order {trade.orderId}</p>
            </div>
          </div>
          <div className={clsx('trade-pnl', `text-${tone}`)}>
            <TickingNumber value={trade.pnl} decimals={0} signed />
            <span>{formatPercent(pnlPct)}</span>
          </div>
        </div>

        <div className="trade-card-grid">
          <TradeCell label="Entry" value={formatCurrency(trade.avgPrice, { decimals: 2 })} />
          <motion.div
            key={`${trade.ltpDirection ?? 'flat'}-${trade.ltp}`}
            className="trade-cell ltp-cell"
            initial={false}
          >
            {trade.ltpDirection === 'up' || trade.ltpDirection === 'down' ? (
              <motion.span
                className={`ltp-flash-overlay ${trade.ltpDirection}`}
                initial={{ opacity: 1 }}
                animate={{ opacity: 0 }}
                transition={reduceMotion ? { duration: 0 } : { duration: 0.34, ease: softEase }}
                aria-hidden="true"
              />
            ) : null}
            <span>LTP</span>
            <strong>{quoteUnavailable ? 'Unavailable' : <TickingNumber value={trade.ltp} decimals={2} />}</strong>
            {quoteUnavailable || trade.quoteStale ? (
              <small>
                {trade.quoteStale ? 'Stale quote' : 'Live option quote unavailable'}
                {trade.quoteSource ? ` · ${trade.quoteSource}` : ''}
                {trade.quoteAgeSeconds != null ? ` · ${Math.round(trade.quoteAgeSeconds)}s old` : ''}
              </small>
            ) : null}
          </motion.div>
          <TradeCell
            label="Qty"
            value={`${displayQty} (${displayLots} lot)`}
            action={<button type="button" className="trade-cell-action" onClick={openQuantityEditor} title="Edit lots"><Pencil size={12} /></button>}
          />
          <TradeCell
            label="SL / TP"
            value={exitLevelText}
            action={<button type="button" className="trade-cell-action" onClick={openExitLevelEditor} title="Edit SL/TP levels"><Pencil size={12} /></button>}
          />
        </div>

        <AnimatePresence initial={false}>
          {editingQuantity ? (
            <motion.div
              className="trade-edit-panel quantity-edit-panel"
              initial={{ height: 0, opacity: 0, y: -4 }}
              animate={{ height: 'auto', opacity: 1, y: 0 }}
              exit={{ height: 0, opacity: 0, y: -4 }}
              transition={reduceMotion ? { duration: 0 } : { duration: 0.2, ease: softEase }}
            >
              <label>
                <span>Lots</span>
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={draftLots}
                  disabled={savingQuantity}
                  onChange={(event) => setDraftLots(event.target.value)}
                  aria-label="Lot count"
                />
              </label>
              <button type="button" onClick={() => void saveQuantity()} disabled={savingQuantity}>
                {savingQuantity ? <MotionSpinner><Loader2 size={13} /></MotionSpinner> : <Check size={13} />}
              </button>
              <button type="button" onClick={() => setEditingQuantity(false)} disabled={savingQuantity} aria-label="Cancel qty edit">
                <X size={13} />
              </button>
            </motion.div>
          ) : null}
        </AnimatePresence>
        {quantityStatus ? <p className="trade-edit-status">{quantityStatus}</p> : null}

        <AnimatePresence initial={false}>
          {editingExitLevels ? (
            <motion.div
              className="trade-edit-panel"
              initial={{ height: 0, opacity: 0, y: -4 }}
              animate={{ height: 'auto', opacity: 1, y: 0 }}
              exit={{ height: 0, opacity: 0, y: -4 }}
              transition={reduceMotion ? { duration: 0 } : { duration: 0.2, ease: softEase }}
            >
              <label>
                <span>SL</span>
                <input
                  type="number"
                  min="0.05"
                  step="0.05"
                  value={draftStopLoss}
                  disabled={savingExitLevels}
                  onChange={(event) => setDraftStopLoss(event.target.value)}
                  aria-label="Stop loss price"
                />
              </label>
              <label>
                <span>TP</span>
                <input
                  type="number"
                  min="0.05"
                  step="0.05"
                  value={draftTarget}
                  disabled={savingExitLevels}
                  onChange={(event) => setDraftTarget(event.target.value)}
                  aria-label="Target price"
                />
              </label>
              <button type="button" onClick={() => void saveExitLevels()} disabled={savingExitLevels}>
                {savingExitLevels ? <MotionSpinner><Loader2 size={13} /></MotionSpinner> : <Check size={13} />}
              </button>
              <button type="button" onClick={() => setEditingExitLevels(false)} disabled={savingExitLevels} aria-label="Cancel SL/TP edit">
                <X size={13} />
              </button>
            </motion.div>
          ) : null}
        </AnimatePresence>
        {exitLevelStatus ? <p className="trade-edit-status">{exitLevelStatus}</p> : null}

        {srReady ? (
          <div className={clsx('sr-suggestion-panel', srAccepted && 'sr-suggestion-armed')}>
            <div>
              <span>Suggested S/R SL/TP</span>
              <strong>{formatCurrency(srStop ?? 0, { decimals: 2 })} / {formatCurrency(srTarget ?? 0, { decimals: 2 })}</strong>
            </div>
            {srAccepted ? (
              <span className="sr-suggestion-status">Armed</span>
            ) : (
              <button type="button" onClick={onApplySrSuggestion} disabled={!onApplySrSuggestion}>
                <Target size={14} />
                Use Suggested SL/TP
              </button>
            )}
          </div>
        ) : null}

        <div className="trade-actions">
          {trade.mode === 'paper' ? (
            <button type="button" onClick={() => setDetailsOpen((current) => !current)}>
              View paper order
              <motion.span animate={{ rotate: detailsOpen ? 180 : 0 }} transition={reduceMotion ? { duration: 0 } : { duration: 0.18, ease: softEase }}>
                <ChevronDown size={14} />
              </motion.span>
            </button>
          ) : (
            <>
              <a href={trade.verifyUrl ?? 'https://web.dhan.co/'} target="_blank" rel="noreferrer">
                Verify on Dhan
                <ExternalLink size={13} />
              </a>
              <button type="button" onClick={() => setDetailsOpen((current) => !current)}>
                Details
                <motion.span animate={{ rotate: detailsOpen ? 180 : 0 }} transition={reduceMotion ? { duration: 0 } : { duration: 0.18, ease: softEase }}>
                  <ChevronDown size={14} />
                </motion.span>
              </button>
            </>
          )}
        </div>

        <AnimatePresence initial={false}>
          {detailsOpen ? (
            <motion.dl
              className="trade-details"
              initial={{ height: 0, opacity: 0, y: -4 }}
              animate={{ height: 'auto', opacity: 1, y: 0 }}
              exit={{ height: 0, opacity: 0, y: -4 }}
              transition={reduceMotion ? { duration: 0 } : { duration: 0.22, ease: softEase }}
            >
              {trade.mode === 'paper' ? <div><dt>Source LTP</dt><dd>{formatCurrency(trade.sourceLtp ?? trade.avgPrice, { decimals: 2 })}</dd></div> : null}
              {trade.mode === 'paper' && trade.slippagePercent !== undefined ? (
                <div>
                  <dt>Slippage</dt>
                  <dd>
                    {trade.slippagePercent.toFixed(2)}%
                    {trade.sourceLtp !== undefined ? ` (${formatCurrency(trade.avgPrice - trade.sourceLtp, { decimals: 2 })})` : null}
                  </dd>
                </div>
              ) : null}
              {trade.mode === 'paper' ? <div><dt>Simulated charges</dt><dd>{formatCurrency(trade.simulatedCharges ?? 0, { decimals: 2 })}</dd></div> : null}
              {activeExitLevels ? (
                <div><dt>Active SL/TP</dt><dd>{formatCurrency(activeExitLevels.stopLossPrice ?? 0, { decimals: 2 })} / {formatCurrency(activeExitLevels.targetPrice ?? 0, { decimals: 2 })} · {trade.riskSource === 'broker' ? 'Broker managed' : 'Server managed'}</dd></div>
              ) : null}
              <div><dt>Exit mode</dt><dd>{trade.exitOn ?? 'Reversal flip'}</dd></div>
              {trade.mode === 'paper' ? (
                <>
                  <div><dt>Paper fill</dt><dd>Confirmed</dd></div>
                  <div><dt>Order type</dt><dd>Simulated order · position persisted</dd></div>
                </>
              ) : (
                <>
                  <div><dt>Correlation</dt><dd>{trade.correlationId || 'pending'}</dd></div>
                  <div><dt>Exchange order</dt><dd>{trade.exchOrderId ?? 'pending'}</dd></div>
                </>
              )}
              <div><dt>Status</dt><dd>{trade.status}</dd></div>
            </motion.dl>
          ) : null}
        </AnimatePresence>
      </motion.article>
    </section>
  )
}
function TradeCell({ label, value, action }: { label: string; value: string; action?: ReactNode }) {
  return (
    <div className="trade-cell">
      <span>{label}</span>
      <strong>
        {value}
        {action ? <span className="trade-cell-action-wrap">{action}</span> : null}
      </strong>
    </div>
  )
}
