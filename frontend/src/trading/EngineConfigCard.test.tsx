import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import type { RuntimeStatus } from '../api'
import { EngineConfigCard } from './EngineConfigCard'

it('edits and saves the stopped engine configuration', async () => {
  const user = userEvent.setup()
  const onSaveConfig = vi.fn().mockResolvedValue(undefined)
  const runtime = {
    engine: { state: 'STOPPED', mode: 'paper' },
    config: { active: { configured_lots: 1, option_sl_percent: 10, option_tp_percent: 20, max_trades_per_day: 6 } },
    selected_strategy: { display_name: 'Supertrend (NIFTY)' },
  } as unknown as RuntimeStatus

  render(<EngineConfigCard runtime={runtime} onStop={vi.fn()} onSaveConfig={onSaveConfig} side="BOTH" onSideChange={vi.fn()} />)
  expect(screen.getByRole('button', { name: 'Decrease lots' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: 'Increase lots' }))
  expect(screen.getByRole('group', { name: 'Lots' })).toHaveTextContent('2')
  await user.click(screen.getByRole('button', { name: 'Save configuration' }))

  expect(onSaveConfig).toHaveBeenCalledWith({ lots: 2, stopLossPercent: 10, takeProfitPercent: 20, maxTradesPerDay: 6 })
  expect(screen.queryByText(/all orders simulated/i)).not.toBeInTheDocument()
})
