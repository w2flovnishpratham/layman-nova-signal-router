import { describe, expect, it } from 'vitest'
import { hasOpenPositionProof } from './ManualOrderPanel.helpers'

describe('manual-order terminal truth', () => {
  it('rejects request acceptance without a persisted position', () => {
    expect(hasOpenPositionProof({
      ok: true,
      message: 'ORDER PLACED',
      operationState: 'PAPER_ORDER_ACCEPTED',
      position: null,
    })).toBe(false)
  })

  it('requires position id, fill price, quantity, and contract', () => {
    expect(hasOpenPositionProof({
      ok: true,
      message: 'Position opened.',
      operationState: 'POSITION_OPEN',
      position: {
        has_open_position: true,
        entry_order_id: 'PAPER-1',
        entry_price: 101.25,
        qty: 65,
        security_id: '123',
      },
    })).toBe(true)
  })
})
