import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ConversationState } from '../state/conversationMachine'
import { SetupPage } from './SetupPage'

vi.mock('./brokerApi', () => ({
  getBrokerStatus: vi.fn(async () => ({
    has_dhan_client_id: false,
    dhan_client_id_masked: null,
  })),
}))

const fields: ConversationState['fields'] = [
  {
    key: 'direction',
    type: 'choice',
    label: 'Direction',
    options: ['CE', 'PE', 'BOTH'],
    required: true,
    default: 'BOTH',
  },
  {
    key: 'lots',
    type: 'integer',
    label: 'Lots',
    minimum: 1,
    maximum: 20,
    required: true,
    default: 1,
  },
  {
    key: 'stop_loss_percent',
    type: 'decimal',
    label: 'Stop loss',
    minimum: 0,
    maximum: 100,
    required: true,
    default: 10,
  },
  {
    key: 'take_profit_percent',
    type: 'decimal',
    label: 'Take profit',
    minimum: 0,
    maximum: 1000,
    required: true,
    default: 20,
  },
  {
    key: 'max_daily_loss',
    type: 'decimal',
    label: 'Daily loss',
    minimum: 0,
    maximum: 10_000_000,
    required: true,
    default: 25_000,
  },
  {
    key: 'max_trades_per_day',
    type: 'integer',
    label: 'Trade cap',
    minimum: 0,
    maximum: 50,
    required: true,
    default: 6,
  },
  {
    key: 'entry_cutoff_ist',
    type: 'choice',
    label: 'Entry cutoff',
    options: ['14:30', '15:00', '15:15', 'No cutoff'],
    required: true,
    default: '15:15',
  },
]

describe('SetupPage authoritative saved revision hydration', () => {
  it('shows a fully saved revision as 100% before Resume or Review is clicked', () => {
    render(
      <SetupPage
        conversation={<div>Conversation</div>}
        snapshot={{
          state: {
            phase: 'SAVED_SETUP_FOUND',
            mode: 'paper',
            strategyKey: 'nova-supertrend',
            fields,
            saved: {
              direction: 'BOTH',
              lots: 1,
              stop_loss_percent: 10,
              take_profit_percent: 20,
              max_daily_loss: 25_000,
              max_trades_per_day: 6,
              entry_cutoff_ist: '15:15',
            },
            draft: {},
            activeQuestionKey: null,
            origin: null,
            generation: 2,
          },
          strategyName: 'Supertrend',
          strategyVersion: '1.0.0',
          savedComplete: true,
        }}
      />,
    )

    expect(screen.getByRole('progressbar', { name: 'Setup progress' })).toHaveAttribute(
      'aria-valuenow',
      '100',
    )
    expect(screen.getByRole('progressbar', { name: 'Compact setup progress' })).toHaveAttribute(
      'aria-valuenow',
      '100',
    )
    expect(screen.getByText('All steps answered.')).toBeInTheDocument()
    expect(screen.getByText('CE + PE · 1 lot')).toBeInTheDocument()
    expect(screen.getByText('SL 10% · TP 20%')).toBeInTheDocument()
    expect(screen.getByText('₹25,000 · max 6 trades · 15:15 IST')).toBeInTheDocument()
    expect(screen.queryByText(/Guided engine setup/i)).toBeNull()
  })

  it('hides setup progress when bootstrap data is unavailable', () => {
    const view = render(<SetupPage conversation={<div>Trading database is not configured.</div>} snapshot={null} unavailable />)

    expect(view.getByText('Trading database is not configured.')).toBeInTheDocument()
    expect(view.container.querySelector('[role="progressbar"]')).toBeNull()
    expect(view.container.querySelector('[aria-label="Setup steps"]')).toBeNull()
  })
})
