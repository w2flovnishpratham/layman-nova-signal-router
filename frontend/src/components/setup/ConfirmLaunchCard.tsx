import { formatCurrency, exitModeLabel, sideLabel } from '../../lib/format'
import { useState } from 'react'
import { ConfirmationDialog } from '../ConfirmationDialog'
import type { ClientCommand, SafetyStatus, TradeConfig } from '../../types'
import { contractsForLots, DEFAULT_NIFTY_LOT_SIZE } from '../../lib/trading'

interface Props {
  config: TradeConfig
  safetyStatus: SafetyStatus | null
  pending: boolean
  error?: string
  onSend: (command: ClientCommand) => boolean
}

export function ConfirmLaunchCard({ config, safetyStatus, pending, error, onSend }: Props) {
  const [open, setOpen] = useState(false)
  const lots = config.risk?.lots ?? 1
  const quantity = contractsForLots(lots, DEFAULT_NIFTY_LOT_SIZE)
  const sampleStrike = 'NIFTY 23500 CE / weekly 4 Jun'
  const estimatedMargin = 24000 * lots
  const estimatedCharges = 92 * lots

  return (
    <article className="setup-card launch-card">
      <span className="eyebrow">Confirm</span>
      <h2>Review live-money launch</h2>
      <dl>
        <div><dt>Strategy</dt><dd>Supertrend ATM Reversal</dd></div>
        <div><dt>Client</dt><dd>{config.broker?.clientId ?? 'pending'}</dd></div>
        <div><dt>Lots</dt><dd>{lots} lot / {quantity} qty</dd></div>
        <div><dt>Side</dt><dd>{sideLabel(config.risk?.side)}</dd></div>
        <div><dt>Exit</dt><dd>{exitModeLabel(config.exits?.mode)}</dd></div>
      </dl>
      <div className="trust-grid">
        <div><span>Sample strike</span><strong>{sampleStrike}</strong></div>
        <div><span>Est. margin</span><strong>{formatCurrency(estimatedMargin)}</strong></div>
        <div><span>Round-trip charges</span><strong>{formatCurrency(estimatedCharges)}</strong></div>
      </div>
      <button className="live-confirm" type="button" disabled={!safetyStatus?.single_operator_live_allowed || pending} onClick={() => setOpen(true)}>
        {pending ? 'Starting...' : 'Review real-money launch'}
      </button>
      <ConfirmationDialog
        open={open}
        title="Start Live trading with real money?"
        consequence="Real Dhan orders may be placed after backend risk checks."
        confirmLabel="Start Live Trading"
        confirmPhrase="START LIVE WITH REAL MONEY"
        mode="live"
        affectsRealOrders
        pending={pending}
        error={error}
        onClose={() => setOpen(false)}
        onConfirm={() => { onSend({ type: 'setup.confirm_live', data: {} }) }}
      />
    </article>
  )
}
