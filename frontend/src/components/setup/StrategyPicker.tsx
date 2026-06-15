import type { ClientCommand } from '../../types'

export function StrategyPicker({ onSend }: { onSend: (command: ClientCommand) => void }) {
  return (
    <article className="setup-card">
      <div>
        <span className="eyebrow">Strategy</span>
        <h2>Supertrend ATM Reversal</h2>
      </div>
      <button type="button" onClick={() => onSend({ type: 'setup.select_strategy', data: { strategy: 'supertrend' } })}>
        Select strategy
      </button>
      <button type="button" className="disabled-option" disabled>ORB Breakout <span>Coming soon</span></button>
      <button type="button" className="disabled-option" disabled>VWAP Reclaim <span>Coming soon</span></button>
    </article>
  )
}
