export function exitLotOptions(lots: number): Array<{ lots: number | null; label: string }> {
  const wholeLots = Math.max(Math.floor(lots), 1)
  return [
    ...Array.from({ length: wholeLots - 1 }, (_, index) => {
      const exitLots = index + 1
      return { lots: exitLots, label: `Exit ${exitLots} ${exitLots === 1 ? 'lot' : 'lots'}` }
    }),
    { lots: null, label: 'Exit All' },
  ]
}
