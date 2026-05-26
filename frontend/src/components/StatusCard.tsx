import { ReactNode } from 'react'

interface Props {
  title: string
  value: string | number | ReactNode
  subtitle?: string
  accent?: 'green' | 'red' | 'yellow' | 'blue' | 'default'
}

const accents = {
  green: 'border-[#98e94d]/40 bg-[#98e94d]/5',
  red: 'border-red-500/40 bg-red-500/5',
  yellow: 'border-yellow-500/40 bg-yellow-500/5',
  blue: 'border-[#d3cec5]/30 bg-[#d3cec5]/5',
  default: 'border-[#24231f]',
}

export default function StatusCard({ title, value, subtitle, accent = 'default' }: Props) {
  return (
    <div className={`card border ${accents[accent]}`}>
      <p className="text-xs uppercase tracking-wide mb-1 text-[#9a968f]">{title}</p>
      <div className="text-xl font-bold text-[#f4f1ea]">{value}</div>
      {subtitle && <p className="text-xs mt-1 text-[#77736c]">{subtitle}</p>}
    </div>
  )
}
