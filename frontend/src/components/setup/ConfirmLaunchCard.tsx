import { formatCurrency, exitModeLabel, sideLabel } from '../../lib/format'
import type { ClientCommand, TradeConfig } from '../../types'
import { contractsForLots, DEFAULT_NIFTY_LOT_SIZE } from '../../lib/trading'

interface Props {
  config: TradeConfig
  strategyLabel?: string
  onSend: (command: ClientCommand) => void
}

export function ConfirmLaunchCard({ config, strategyLabel = 'Selected owner strategy', onSend }: Props) {
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
        <div><dt>Strategy</dt><dd>{strategyLabel}</dd></div>
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
      <button className="live-confirm" type="button" onClick={() => onSend({ type: 'setup.confirm_live', data: {} })}>
        Confirm trade real money
      </button>
    </article>
  )
}
