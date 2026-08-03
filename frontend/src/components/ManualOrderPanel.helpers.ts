import type { ManualOrderResponse, OrderQuote } from '../api'

export interface SideContract {
  securityId: string
  tradingSymbol: string | null
  strike: number
  expiry: string
}

export function hasOpenPositionProof(response: ManualOrderResponse): boolean {
  const position = response.position
  return Boolean(
    position?.has_open_position
    && position.entry_order_id
    && Number(position.entry_price) > 0
    && Number(position.qty) > 0
    && (position.security_id || position.trading_symbol)
  )
}

export function getContractForSide(quote: OrderQuote | null, side: 'CE' | 'PE'): SideContract | null {
  if (!quote) return null
  const option = quote.atm?.options?.[side]
  const securityId = (quote.side === side ? quote.securityId : null) ?? option?.securityId ?? null
  const tradingSymbol = (quote.side === side ? quote.tradingSymbol : null) ?? option?.tradingSymbol ?? null
  const strike = option?.strike ?? quote.atm?.atmStrike ?? null
  const expiry = option?.expiry ?? null
  if (!securityId || strike === null || strike === undefined || !expiry) return null
  return { securityId, tradingSymbol: tradingSymbol ?? null, strike, expiry }
}
