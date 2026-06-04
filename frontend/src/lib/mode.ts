import type { EngineMode } from '../types'

export const MODE_LABEL: Record<EngineMode, string> = {
  paper: 'Paper mode',
  live: 'Live mode',
}

export function modeBadgeText(mode: EngineMode | null): string {
  if (mode === 'paper') return 'PAPER MODE'
  if (mode === 'live') return 'LIVE MODE'
  return 'SELECT MODE'
}
