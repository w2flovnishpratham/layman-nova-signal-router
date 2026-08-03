import { Input } from "@/components/ui/input"
import { NativeSelect } from "@/components/ui/native-select"
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Check, ChevronDown, ExternalLink, Loader2, Pencil, Target, X } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { clsx } from 'clsx'
import {
  patchActiveExitLevels,
  postActiveAddLots,
  postActivePartialExit,
  postManualExit,
} from '../api'
import { formatCurrency, formatPercent } from '../lib/format'
import { TickingNumber } from './TickingNumber'
import { MotionSpinner, softEase, useAppReducedMotion } from './MotionPrimitives'
import type { ActiveExitLevels, ActiveTrade } from '../types'
import { lotsForQuantity } from '../lib/trading'
import { exitLotOptions } from './ActiveTradeCard.helpers'

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
  const [exitLevelUnit, setExitLevelUnit] = useState<'PERCENTAGE' | 'POINTS' | 'ABSOLUTE_TRIGGER'>('ABSOLUTE_TRIGGER')
  const [savingExitLevels, setSavingExitLevels] = useState(false)
  const [exitLevelStatus, setExitLevelStatus] = useState('')
  const [savedExitLevels, setSavedExitLevels] = useState<ActiveExitLevels | null>(null)
  const [editingQuantity, setEditingQuantity] = useState(false)
  const [draftLots, setDraftLots] = useState('')
  const [protectionMode, setProtectionMode] = useState<'KEEP_EXISTING' | 'RECALCULATE_FROM_AVERAGE' | 'CUSTOM'>('KEEP_EXISTING')
  const [customStopLoss, setCustomStopLoss] = useState('')
  const [customTarget, setCustomTarget] = useState('')
  const [exitOptionsOpen, setExitOptionsOpen] = useState(false)
  const [savingQuantity, setSavingQuantity] = useState(false)
  const [positionOperation, setPositionOperation] = useState<'partial' | 'exit' | null>(null)
  const [quantityStatus, setQuantityStatus] = useState('')
  const [savedQty, setSavedQty] = useState<number | null>(null)
  const [savedPositionVersion, setSavedPositionVersion] = useState<number | null>(null)
  const reduceMotion = useAppReducedMotion()
  const tone = trade.pnl > 0 ? 'up' : trade.pnl < 0 ? 'down' : 'flat'
  const pnlPct = trade.pnlPct ?? 0
  const srSuggestion = trade.srSuggestion
  const srStop = srSuggestion?.stopLossPrice
  const srTarget = srSuggestion?.targetPrice
  const srReady = trade.mode === 'paper' && Boolean(srSuggestion?.available) && srStop !== undefined && srTarget !== undefined
  const sizingOperationsAvailable = trade.mode === 'paper'
  const activeExitLevels = savedExitLevels ?? trade.activeExitLevels ?? null
  const displayQty = savedQty ?? trade.qty
  const displayLots = lotsForQuantity(displayQty, lotSize)
  const riskPoints = trade.avgPrice - (activeExitLevels?.stopLossPrice ?? Number.NaN)
  const rewardPoints = (activeExitLevels?.targetPrice ?? Number.NaN) - trade.avgPrice
  const riskRewardRatio = riskPoints > 0 && rewardPoints > 0 ? rewardPoints / riskPoints : null
  const positionVersion = savedPositionVersion ?? trade.positionVersion
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
    setDraftLots('1')
    setProtectionMode(activeExitLevels ? 'KEEP_EXISTING' : 'RECALCULATE_FROM_AVERAGE')
    setCustomStopLoss(activeExitLevels?.stopLossPrice?.toFixed(2) ?? '')
    setCustomTarget(activeExitLevels?.targetPrice?.toFixed(2) ?? '')
    setQuantityStatus('')
    setEditingExitLevels(false)
    setEditingQuantity(true)
  }

  function openExitLevelEditor() {
    const stop = activeExitLevels?.stopLossPrice ?? Math.max(trade.ltp * 0.9, 0.05)
    const target = activeExitLevels?.targetPrice ?? trade.ltp * 1.2
    setDraftStopLoss(stop.toFixed(2))
    setDraftTarget(target.toFixed(2))
    setExitLevelUnit('ABSOLUTE_TRIGGER')
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
    if (!positionVersion) {
      setQuantityStatus('Position version is unavailable. Refresh before adding lots.')
      return
    }
    setSavingQuantity(true)
    setQuantityStatus('')
    try {
      const customStop = Number(customStopLoss)
      const customTp = Number(customTarget)
      if (
        protectionMode === 'CUSTOM'
        && (!Number.isFinite(customStop) || customStop <= 0 || !Number.isFinite(customTp) || customTp <= 0)
      ) {
        setQuantityStatus('Custom protection requires valid positive SL and TP trigger prices.')
        return
      }
      const response = await postActiveAddLots({
        lots,
        expectedPositionVersion: positionVersion,
        protectionMode,
        ...(protectionMode === 'CUSTOM'
          ? { customStopLossPrice: customStop, customTargetPrice: customTp }
          : {}),
      })
      if (!response.ok) {
        setQuantityStatus(response.message || 'Could not update qty.')
        return
      }
      setSavedQty(response.qty ?? displayQty + (lots * lotSize))
      if (response.activeExitLevels) setSavedExitLevels(response.activeExitLevels)
      if (response.positionVersion) setSavedPositionVersion(response.positionVersion)
      setEditingQuantity(false)
      setQuantityStatus(response.message || 'Lots added.')
    } catch (error) {
      setQuantityStatus(error instanceof Error ? error.message : 'Could not add lots.')
    } finally {
      setSavingQuantity(false)
    }
  }

  async function saveExitLevels() {
    if (savingExitLevels) return
    const stopLossValue = Number(draftStopLoss)
    const targetValue = Number(draftTarget)
    if (!Number.isFinite(stopLossValue) || !Number.isFinite(targetValue) || stopLossValue <= 0 || targetValue <= 0) {
      setExitLevelStatus('Enter valid positive prices.')
      return
    }
    if (!positionVersion) {
      setExitLevelStatus('Position version is unavailable. Refresh before editing SL/TP.')
      return
    }
    setSavingExitLevels(true)
    setExitLevelStatus('')
    try {
      const response = await patchActiveExitLevels({
        unit: exitLevelUnit,
        stopLossValue,
        targetValue,
        expectedPositionVersion: positionVersion,
      })
      if (!response.ok) {
        setExitLevelStatus(response.message || 'Could not update SL/TP.')
        return
      }
      if (response.activeExitLevels) setSavedExitLevels(response.activeExitLevels)
      if (response.positionVersion) setSavedPositionVersion(response.positionVersion)
      setEditingExitLevels(false)
      setExitLevelStatus(response.message || 'SL/TP updated.')
    } catch (error) {
      setExitLevelStatus(error instanceof Error ? error.message : 'Could not update SL/TP.')
    } finally {
      setSavingExitLevels(false)
    }
  }

  async function runPositionOperation(operation: 'partial' | 'exit', exitLots?: number) {
    if (positionOperation || savingQuantity || savingExitLevels) return
    if (!positionVersion) {
      setQuantityStatus('Position version is unavailable. Refresh before changing exposure.')
      return
    }
    setPositionOperation(operation)
    setQuantityStatus('')
    try {
      if (operation === 'partial') {
        if (!exitLots || exitLots >= displayLots) {
          setQuantityStatus('Partial exit must leave at least one whole lot open.')
          return
        }
        const response = await postActivePartialExit({
          lots: exitLots,
          expectedPositionVersion: positionVersion,
        })
        if (!response.ok) {
          setQuantityStatus(response.message || 'Partial exit could not be completed.')
          return
        }
        if (response.qty) setSavedQty(response.qty)
        if (response.positionVersion) setSavedPositionVersion(response.positionVersion)
        setExitOptionsOpen(false)
        setQuantityStatus(response.message || 'Partial exit completed.')
      } else {
        const response = await postManualExit()
        setExitOptionsOpen(false)
        setQuantityStatus(response.message || (response.ok ? 'Exit completed.' : 'Exit could not be completed.'))
      }
    } catch (error) {
      setQuantityStatus(error instanceof Error ? error.message : 'Position operation failed.')
    } finally {
      setPositionOperation(null)
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
        {compact ? (
          <>
            <div className="compact-position-header">
              <h2>Active Position</h2>
              <Badge variant="unstyled" className={clsx('compact-position-status', `pill-${tone}`)}>{trade.status}</Badge>
            </div>
            <div className="compact-position-instrument">
              <Badge variant="unstyled" className={clsx('compact-side-pill', `pill-${tone}`)}>BUY {trade.optType}</Badge>
              <strong>{trade.symbol}</strong>
              {trade.mode ? <Badge variant="unstyled" className={clsx('paper-trade-pill', trade.mode)}>{trade.mode}</Badge> : null}
            </div>
            <div className="compact-position-metrics">
              <TradeCell label="Qty / Lots" value={`${displayQty} / ${displayLots}`} />
              <TradeCell label="Avg Price" value={trade.avgPrice.toFixed(2)} />
              <TradeCell label="LTP" value={quoteUnavailable ? 'Unavailable' : trade.ltp.toFixed(2)} />
            </div>
            <div className="compact-position-pnl">
              <div>
                <span>Unrealized P&amp;L</span>
                <strong className={`text-${tone}`}>
                  <TickingNumber value={trade.pnl} decimals={2} signed />
                  {' ('}<TickingNumber value={pnlPct} kind="percent" decimals={2} signed />{')'}
                </strong>
              </div>
              <div>
                <span>R:R</span>
                <strong>{riskRewardRatio === null ? '—' : `1:${riskRewardRatio.toFixed(2)}`}</strong>
              </div>
            </div>
            <div className="position-operation-actions compact-position-actions" aria-label="Active position actions">
              <Button variant="unstyled" type="button" onClick={openExitLevelEditor} disabled={Boolean(positionOperation)}>Edit SL/TP</Button>
              <Button variant="unstyled" type="button" onClick={() => setExitOptionsOpen((current) => !current)} disabled={Boolean(positionOperation)}>
                {positionOperation ? 'Exiting…' : 'Exit Position'}
              </Button>
            </div>
          </>
        ) : (
          <>
        <div className="trade-card-top">
          <div className="trade-title-block">
              <Badge variant="unstyled" className={clsx('trade-state-pill', `pill-${tone}`)}>ACTIVE {trade.optType}</Badge>
            {trade.mode === 'paper' ? <Badge variant="unstyled" className="paper-trade-pill">PAPER</Badge> : null}
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
          />
          <TradeCell
            label="SL / TP"
            value={exitLevelText}
            action={<Button variant="unstyled" type="button" className="trade-cell-action" onClick={openExitLevelEditor} title="Edit SL/TP levels"><Pencil size={12} /></Button>}
          />
        </div>

        <div className="position-operation-actions" aria-label="Active position actions">
          <Button variant="unstyled"
            type="button"
            onClick={openQuantityEditor}
            disabled={!sizingOperationsAvailable || Boolean(positionOperation)}
            title={sizingOperationsAvailable ? undefined : 'Unavailable until broker verification'}
          >
            {sizingOperationsAvailable ? 'Add Lots' : 'Unavailable until broker verification'}
          </Button>
          <Button variant="unstyled"
            type="button"
            onClick={() => setExitOptionsOpen((current) => !current)}
            disabled={Boolean(positionOperation)}
          >
            {positionOperation ? 'Exiting…' : 'Exit / Reduce'}
          </Button>
          <Button variant="unstyled" type="button" onClick={openExitLevelEditor} disabled={Boolean(positionOperation)}>Edit Current SL/TP</Button>
        </div>
          </>
        )}

        <AnimatePresence initial={false}>
          {exitOptionsOpen ? (
            <motion.div
              className="trade-edit-panel exit-lot-options"
              aria-label="Exit quantity options"
              initial={{ height: 0, opacity: 0, y: -4 }}
              animate={{ height: 'auto', opacity: 1, y: 0 }}
              exit={{ height: 0, opacity: 0, y: -4 }}
              transition={reduceMotion ? { duration: 0 } : { duration: 0.2, ease: softEase }}
            >
              {exitLotOptions(displayLots).map((option) => (
                <Button variant="unstyled"
                  key={option.label}
                  type="button"
                  className={option.lots === null ? 'is-danger' : undefined}
                  disabled={
                    Boolean(positionOperation)
                    || (option.lots !== null && !sizingOperationsAvailable)
                  }
                  title={
                    option.lots !== null && !sizingOperationsAvailable
                      ? 'Unavailable until broker verification'
                      : undefined
                  }
                  onClick={() => void runPositionOperation(
                    option.lots === null ? 'exit' : 'partial',
                    option.lots ?? undefined,
                  )}
                >
                  {option.label}
                </Button>
              ))}
            </motion.div>
          ) : null}
        </AnimatePresence>

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
                <span>Additional lots</span>
                <Input variant="unstyled"
                  type="number"
                  min="1"
                  step="1"
                  value={draftLots}
                  disabled={savingQuantity}
                  onChange={(event) => setDraftLots(event.target.value)}
                  aria-label="Additional lot count"
                />
              </label>
              <label>
                <span>Protection after fill</span>
                <NativeSelect variant="unstyled"
                  value={protectionMode}
                  disabled={savingQuantity}
                  onChange={(event) => setProtectionMode(event.target.value as typeof protectionMode)}
                  aria-label="Protection after adding lots"
                >
                  <option value="KEEP_EXISTING">Keep existing trigger prices</option>
                  <option value="RECALCULATE_FROM_AVERAGE">Recalculate from new average</option>
                  <option value="CUSTOM">Custom SL/TP</option>
                </NativeSelect>
              </label>
              {protectionMode === 'CUSTOM' ? (
                <>
                  <label>
                    <span>Custom SL</span>
                    <Input variant="unstyled"
                      type="number"
                      min="0.05"
                      step="0.05"
                      value={customStopLoss}
                      disabled={savingQuantity}
                      onChange={(event) => setCustomStopLoss(event.target.value)}
                      aria-label="Custom stop loss after adding lots"
                    />
                  </label>
                  <label>
                    <span>Custom TP</span>
                    <Input variant="unstyled"
                      type="number"
                      min="0.05"
                      step="0.05"
                      value={customTarget}
                      disabled={savingQuantity}
                      onChange={(event) => setCustomTarget(event.target.value)}
                      aria-label="Custom target after adding lots"
                    />
                  </label>
                </>
              ) : null}
              <Button variant="unstyled" type="button" onClick={() => void saveQuantity()} disabled={savingQuantity}>
                {savingQuantity ? <MotionSpinner><Loader2 size={13} /></MotionSpinner> : <Check size={13} />}
              </Button>
              <Button variant="unstyled" type="button" onClick={() => setEditingQuantity(false)} disabled={savingQuantity} aria-label="Cancel qty edit">
                <X size={13} />
              </Button>
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
                <span>Unit</span>
                <NativeSelect variant="unstyled"
                  value={exitLevelUnit}
                  disabled={savingExitLevels}
                  onChange={(event) => setExitLevelUnit(event.target.value as typeof exitLevelUnit)}
                  aria-label="SL/TP unit"
                >
                  <option value="PERCENTAGE">Percentage</option>
                  <option value="POINTS">Points</option>
                  <option value="ABSOLUTE_TRIGGER">Absolute Trigger</option>
                </NativeSelect>
              </label>
              <label>
                <span>SL value</span>
                <Input variant="unstyled"
                  type="number"
                  min="0.05"
                  step="0.05"
                  value={draftStopLoss}
                  disabled={savingExitLevels}
                  onChange={(event) => setDraftStopLoss(event.target.value)}
                  aria-label="Stop loss value"
                />
              </label>
              <label>
                <span>TP value</span>
                <Input variant="unstyled"
                  type="number"
                  min="0.05"
                  step="0.05"
                  value={draftTarget}
                  disabled={savingExitLevels}
                  onChange={(event) => setDraftTarget(event.target.value)}
                  aria-label="Target value"
                />
              </label>
              <Button variant="unstyled" type="button" onClick={() => void saveExitLevels()} disabled={savingExitLevels}>
                {savingExitLevels ? <MotionSpinner><Loader2 size={13} /></MotionSpinner> : <Check size={13} />}
              </Button>
              <Button variant="unstyled" type="button" onClick={() => setEditingExitLevels(false)} disabled={savingExitLevels} aria-label="Cancel SL/TP edit">
                <X size={13} />
              </Button>
            </motion.div>
          ) : null}
        </AnimatePresence>
        {exitLevelStatus ? <p className="trade-edit-status">{exitLevelStatus}</p> : null}

        {!compact && srReady ? (
          <div className={clsx('sr-suggestion-panel', srAccepted && 'sr-suggestion-armed')}>
            <div>
              <span>Suggested S/R SL/TP</span>
              <strong>{formatCurrency(srStop ?? 0, { decimals: 2 })} / {formatCurrency(srTarget ?? 0, { decimals: 2 })}</strong>
            </div>
            {srAccepted ? (
              <span className="sr-suggestion-status">Armed</span>
            ) : (
              <Button variant="unstyled" type="button" onClick={onApplySrSuggestion} disabled={!onApplySrSuggestion}>
                <Target size={14} />
                Use Suggested SL/TP
              </Button>
            )}
          </div>
        ) : null}

        <div className="trade-actions">
          {trade.mode === 'paper' ? (
            <Button variant="unstyled" type="button" onClick={() => setDetailsOpen((current) => !current)}>
              View paper order
              <motion.span animate={{ rotate: detailsOpen ? 180 : 0 }} transition={reduceMotion ? { duration: 0 } : { duration: 0.18, ease: softEase }}>
                <ChevronDown size={14} />
              </motion.span>
            </Button>
          ) : (
            <>
              <a href={trade.verifyUrl ?? 'https://web.dhan.co/'} target="_blank" rel="noreferrer">
                Verify on Dhan
                <ExternalLink size={13} />
              </a>
              <Button variant="unstyled" type="button" onClick={() => setDetailsOpen((current) => !current)}>
                Details
                <motion.span animate={{ rotate: detailsOpen ? 180 : 0 }} transition={reduceMotion ? { duration: 0 } : { duration: 0.18, ease: softEase }}>
                  <ChevronDown size={14} />
                </motion.span>
              </Button>
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
