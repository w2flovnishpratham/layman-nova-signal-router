import { Activity } from 'lucide-react'
import { motion } from 'framer-motion'
import { softEase, useAppReducedMotion } from './MotionPrimitives'
import type { ActiveTrade, EngineMode, SideFilter } from '../types'

interface Props {
  paused: boolean
  activeTrade: ActiveTrade | null
  side: SideFilter
  engineMode: EngineMode | null
}

/** Compact router-status banner (the old full-height waiting panel now lives
 * above the NIFTY chart as a single line). */
export function EngineListening({ paused, activeTrade, side, engineMode }: Props) {
  const status = listeningStatus(paused, activeTrade, side)
  const reduceMotion = useAppReducedMotion()

  return (
    <motion.div
      className={`router-status-banner ${paused ? 'paused' : ''}`}
      initial={reduceMotion ? { opacity: 1 } : { opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduceMotion ? { duration: 0 } : { duration: 0.2, ease: softEase }}
      role="status"
    >
      <span className="router-status-pulse" aria-hidden="true">
        <Activity size={14} />
      </span>
      <strong>Router: {status.title}</strong>
      <span className="router-status-detail">- {status.detail}</span>
      <span className="router-status-mode">
        {engineMode === 'paper' ? '- Paper simulator listening' : '- Live router listening'}
      </span>
    </motion.div>
  )
}

function listeningStatus(paused: boolean, activeTrade: ActiveTrade | null, side: SideFilter): { title: string; detail: string } {
  if (paused) {
    return {
      title: 'Entry requests blocked',
      detail: activeTrade
        ? `Exits remain enabled for active ${activeTrade.optType}`
        : 'ENTRY webhooks rejected until entries are allowed',
    }
  }

  if (activeTrade?.optType === 'CE') {
    return {
      title: 'Waiting for sell / flip signal',
      detail: side === 'CE' ? 'SELL closes the active CE' : 'SELL closes CE and routes PE',
    }
  }

  if (activeTrade?.optType === 'PE') {
    return {
      title: 'Waiting for buy / flip signal',
      detail: side === 'PE' ? 'BUY closes the active PE' : 'BUY closes PE and routes CE',
    }
  }

  if (side === 'CE') {
    return { title: 'Waiting for buy signal', detail: 'Next BUY routes a CE entry' }
  }

  if (side === 'PE') {
    return { title: 'Waiting for sell signal', detail: 'Next SELL routes a PE entry' }
  }

  return { title: 'Waiting for TradingView signal', detail: 'BUY routes CE / SELL routes PE' }
}
