import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import type { RuntimeStatus } from '../api'
import { EngineConfigCard } from './EngineConfigCard'

afterEach(cleanup)

it('starts collapsed, hiding the configuration fields until expanded', async () => {
  const user = userEvent.setup()
  const runtime = {
    engine: { state: 'STOPPED', mode: 'paper' },
    config: { active: { configured_lots: 1, option_sl_percent: 10, option_tp_percent: 20, max_trades_per_day: 6 } },
    selected_strategy: { display_name: 'Supertrend (NIFTY)' },
  } as unknown as RuntimeStatus

  render(<EngineConfigCard runtime={runtime} onStop={vi.fn()} onSaveConfig={vi.fn()} side="BOTH" onSideChange={vi.fn()} />)
  expect(screen.queryByRole('group', { name: 'Lots' })).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: 'Expand engine configuration' }))
  expect(screen.getByRole('group', { name: 'Lots' })).toBeInTheDocument()
})

it('edits and saves the stopped engine configuration', async () => {
  const user = userEvent.setup()
  const onSaveConfig = vi.fn().mockResolvedValue(undefined)
  const runtime = {
    engine: { state: 'STOPPED', mode: 'paper' },
    config: { active: { configured_lots: 1, option_sl_percent: 10, option_tp_percent: 20, max_trades_per_day: 6 } },
    selected_strategy: { display_name: 'Supertrend (NIFTY)' },
  } as unknown as RuntimeStatus

  render(<EngineConfigCard runtime={runtime} onStop={vi.fn()} onSaveConfig={onSaveConfig} side="BOTH" onSideChange={vi.fn()} />)
  // Collapsed by default -- expand before interacting with its fields.
  await user.click(screen.getByRole('button', { name: 'Expand engine configuration' }))
  expect(screen.getByRole('button', { name: 'Decrease lots' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: 'Increase lots' }))
  expect(screen.getByRole('group', { name: 'Lots' })).toHaveTextContent('2')
  await user.click(screen.getByRole('button', { name: 'Save configuration' }))

  expect(onSaveConfig).toHaveBeenCalledWith({ lots: 2, stopLossPercent: 10, takeProfitPercent: 20, maxTradesPerDay: 6 })
  expect(screen.queryByText(/all orders simulated/i)).not.toBeInTheDocument()
})
