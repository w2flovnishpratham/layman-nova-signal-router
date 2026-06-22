import { ChevronDown, ExternalLink, Target } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import { clsx } from 'clsx'
import { formatCurrency, formatPercent } from '../lib/format'
import { TickingNumber } from './TickingNumber'
import { softEase, useAppReducedMotion } from './MotionPrimitives'
import type { ActiveTrade } from '../types'
import { lotsForQuantity } from '../lib/trading'

interface Props {
  trade: ActiveTrade
  lotSize: number
  compact?: boolean
  onApplySrSuggestion?: () => void
}

export function ActiveTradeCard({ trade, lotSize, compact = false, onApplySrSuggestion }: Props) {
  const [detailsOpen, setDetailsOpen] = useState(false)
  const reduceMotion = useAppReducedMotion()
  const tone = trade.pnl > 0 ? 'up' : trade.pnl < 0 ? 'down' : 'flat'
  const pnlPct = trade.pnlPct ?? 0
  const srSuggestion = trade.srSuggestion
  const srStop = srSuggestion?.stopLossPrice
  const srTarget = srSuggestion?.targetPrice
  const srReady = trade.mode === 'paper' && Boolean(srSuggestion?.available) && srStop !== undefined && srTarget !== undefined
  const srAccepted = Boolean(srSuggestion?.accepted || trade.activeExitLevels?.source === 'sr_suggestion')

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
            <strong><TickingNumber value={trade.ltp} decimals={2} /></strong>
          </motion.div>
          <TradeCell label="Qty" value={`${trade.qty} (${lotsForQuantity(trade.qty, lotSize)} lot)`} />
          <TradeCell label="Exit on" value={trade.exitOn ?? 'Reversal flip'} />
        </div>

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
              {trade.activeExitLevels ? (
                <div><dt>Active SL/TP</dt><dd>{formatCurrency(trade.activeExitLevels.stopLossPrice ?? 0, { decimals: 2 })} / {formatCurrency(trade.activeExitLevels.targetPrice ?? 0, { decimals: 2 })}</dd></div>
              ) : null}
              <div><dt>Correlation</dt><dd>{trade.correlationId || 'pending'}</dd></div>
              <div><dt>Exchange order</dt><dd>{trade.exchOrderId ?? 'pending'}</dd></div>
              <div><dt>Status</dt><dd>{trade.status}</dd></div>
            </motion.dl>
          ) : null}
        </AnimatePresence>
      </motion.article>
    </section>
  )
}
function TradeCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="trade-cell">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}
