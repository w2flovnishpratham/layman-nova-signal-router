import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PersonalStrategiesPage } from './PersonalStrategiesPage'

const api = vi.hoisted(() => ({ config: vi.fn() }))
vi.mock('../api', () => ({ getC2Config: api.config }))
vi.mock('./EngineStrategyPicker', () => ({ EngineStrategyPicker: () => <div>Published strategy picker</div> }))
vi.mock('./C2MyStrategies', () => ({ C2MyStrategies: () => <div>My strategy installations</div> }))
vi.mock('./AdminPineConversion', () => ({ AdminPineConversionWorkspace: () => <div>Admin strategy workspace</div> }))

describe('PersonalStrategiesPage', () => {
  beforeEach(() => api.config.mockResolvedValue({ enabled: false }))

  it('shows users only the admin-published strategy picker', async () => {
    render(<PersonalStrategiesPage />)
    expect(screen.getByText('Published strategy picker')).toBeInTheDocument()
    expect(screen.queryByText(/Pine scripts/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/TradingView webhooks/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /admin strategies/i })).not.toBeInTheDocument()
  })

  it('gives admins access to add, edit, publish, and delete strategies', async () => {
    render(<PersonalStrategiesPage user={{ is_admin: true } as never} />)
    await userEvent.click(screen.getByRole('tab', { name: /admin strategies/i }))
    expect(screen.getByText('Admin strategy workspace')).toBeInTheDocument()
  })
})
