import { animate, motion, useMotionValue, useTransform } from 'framer-motion'
import { useEffect } from 'react'
import { formatCurrency, formatNumber } from '../lib/format'

interface Props {
  value: number
  kind?: 'currency' | 'number' | 'percent'
  decimals?: number
  signed?: boolean
}

export function TickingNumber({ value, kind = 'currency', decimals, signed = false }: Props) {
  const motionValue = useMotionValue(value)
  const display = useTransform(motionValue, (latest) => formatValue(latest, kind, decimals, signed))

  useEffect(() => {
    const controls = animate(motionValue, value, {
      duration: 0.3,
      ease: [0.4, 0, 0.2, 1],
    })
    return () => controls.stop()
  }, [motionValue, value])

  return <motion.span className="nv-tabular" style={{ fontVariantNumeric: 'tabular-nums' }}>{display}</motion.span>
}

function formatValue(value: number, kind: Props['kind'], decimals: number | undefined, signed: boolean): string {
  const sign = signed && value > 0 ? '+' : ''
  if (kind === 'number') return `${sign}${formatNumber(Math.round(value))}`
  if (kind === 'percent') return `${sign}${value.toFixed(decimals ?? 1)}%`
  return `${sign}${formatCurrency(value, { decimals })}`
}
