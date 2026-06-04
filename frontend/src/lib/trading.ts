export const DEFAULT_NIFTY_LOT_SIZE = 65

export function normalizeLotSize(value: number | null | undefined): number {
  const lotSize = Number(value)
  return Number.isFinite(lotSize) && lotSize > 0 ? Math.round(lotSize) : DEFAULT_NIFTY_LOT_SIZE
}

export function contractsForLots(lots: number, lotSize: number): number {
  return Math.max(1, Math.round(lots)) * normalizeLotSize(lotSize)
}

export function lotsForQuantity(quantity: number, lotSize: number): number {
  return Math.max(1, Math.ceil(quantity / normalizeLotSize(lotSize)))
}
