import { ManualOrderPanel } from './ManualOrderPanel'
import { MarketCard } from './MarketCard'
import type { ReactNode } from 'react'
import type { ActiveTrade, EngineMode, MarketSnapshot } from '../types'

interface Props {
  marketSnapshot: MarketSnapshot | null
  engineMode: EngineMode | null
  activeTrade: ActiveTrade | null
  runtimePositionOpen?: boolean
  collapseControl?: ReactNode
}

export function EngineLeftPanel({ marketSnapshot, engineMode, activeTrade, runtimePositionOpen = false, collapseControl }: Props) {
  return (
    <div className="engine-left flex flex-col gap-4" aria-label="Market and manual order">
      <MarketCard snapshot={marketSnapshot} collapseControl={collapseControl} />
      <ManualOrderPanel engineMode={engineMode} activeTrade={activeTrade} runtimePositionOpen={runtimePositionOpen} />
    </div>
  )
}
