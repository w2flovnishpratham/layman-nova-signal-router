import { ManualOrderPanel } from './ManualOrderPanel'
import { MarketCard } from './MarketCard'
import type { ActiveTrade, EngineMode, MarketSnapshot } from '../types'

interface Props {
  marketSnapshot: MarketSnapshot | null
  engineMode: EngineMode | null
  activeTrade: ActiveTrade | null
}

export function EngineLeftPanel({ marketSnapshot, engineMode, activeTrade }: Props) {
  return (
    <aside className="engine-left" aria-label="Market and manual order">
      <MarketCard snapshot={marketSnapshot} />
      <ManualOrderPanel engineMode={engineMode} activeTrade={activeTrade} />
    </aside>
  )
}
