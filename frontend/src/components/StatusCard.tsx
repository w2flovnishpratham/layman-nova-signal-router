import { ReactNode } from 'react'

interface Props {
  title: string
  value: string | number | ReactNode
  subtitle?: string
  accent?: 'green' | 'red' | 'yellow' | 'blue' | 'default'
}

const accents = {
  green: 'border-green-500/40 bg-green-500/5',
  red: 'border-red-500/40 bg-red-500/5',
  yellow: 'border-yellow-500/40 bg-yellow-500/5',
  blue: 'border-brand-500/40 bg-brand-500/5',
  default: 'border-gray-800',
}

export default function StatusCard({ title, value, subtitle, accent = 'default' }: Props) {
  return (
    <div className={`card border ${accents[accent]}`}>
      <p className="text-gray-400 text-xs uppercase tracking-wide mb-1">{title}</p>
      <div className="text-xl font-bold text-gray-100">{value}</div>
      {subtitle && <p className="text-gray-500 text-xs mt-1">{subtitle}</p>}
    </div>
  )
}
