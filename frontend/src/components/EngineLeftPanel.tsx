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
    <aside className="engine-left lg:col-span-4 order-1 lg:order-3 lg:h-full lg:overflow-y-auto pr-1" aria-label="Market and manual order">
      <MarketCard snapshot={marketSnapshot} />
      <ManualOrderPanel engineMode={engineMode} activeTrade={activeTrade} />
    </aside>
  )
}
