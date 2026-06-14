import type { ClientCommand } from '../../types'

export function StrategyPicker({ pending, onSend }: { pending: boolean; onSend: (command: ClientCommand) => boolean }) {
  return (
    <article className="setup-card">
      <div>
        <span className="eyebrow">Strategy</span>
        <h2>Supertrend ATM Reversal</h2>
      </div>
      <button type="button" disabled={pending} onClick={() => onSend({ type: 'setup.select_strategy', data: { strategy: 'supertrend' } })}>
        {pending ? 'Selecting...' : 'Select strategy'}
      </button>
      <button type="button" className="disabled-option" disabled>ORB Breakout <span>Coming soon</span></button>
      <button type="button" className="disabled-option" disabled>VWAP Reclaim <span>Coming soon</span></button>
    </article>
  )
}
