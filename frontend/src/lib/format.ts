export function formatCurrency(value: number, options: { decimals?: number } = {}): string {
  const decimals = options.decimals ?? (Number.isInteger(value) ? 0 : 2)
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value)
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat('en-IN').format(value)
}

export function formatPercent(value: number): string {
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}%`
}

export function formatTime(value: string): string {
  return new Intl.DateTimeFormat('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

export function exitModeLabel(mode: string | undefined): string {
  if (mode === 'flip_tp') return 'Flip + target'
  if (mode === 'custom') return 'Custom'
  return 'Reversal flip'
}

export function sideLabel(side: string | undefined): string {
  if (side === 'CE') return 'CE only'
  if (side === 'PE') return 'PE only'
  return 'Both'
}
