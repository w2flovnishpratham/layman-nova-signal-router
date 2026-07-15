import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StrategyInstance, WebhookExecution } from '../api'
import { PersonalStrategiesPage } from './PersonalStrategiesPage'

const api = vi.hoisted(() => ({
  activate: vi.fn(), create: vi.fn(), generate: vi.fn(), get: vi.fn(), history: vi.fn(), list: vi.fn(),
  pause: vi.fn(), resume: vi.fn(), revoke: vi.fn(), rotate: vi.fn(), stop: vi.fn(), test: vi.fn(), lots: vi.fn(),
  pineList: vi.fn(), pineGet: vi.fn(), pineCreate: vi.fn(), pineVersion: vi.fn(), pineValidate: vi.fn(),
  pineSubmit: vi.fn(), pineSource: vi.fn(), pineLink: vi.fn(), reviewList: vi.fn(), reviewGet: vi.fn(), reviewDecide: vi.fn(),
}))

vi.mock('../api', () => ({
  activateStrategyInstance: api.activate,
  createStrategyInstance: api.create,
  generateInstanceWebhookCredential: api.generate,
  getStrategyInstance: api.get,
  listInstanceWebhookExecutions: api.history,
  listStrategyInstances: api.list,
  pauseStrategyInstance: api.pause,
  resumeStrategyInstance: api.resume,
  revokeInstanceWebhookCredential: api.revoke,
  rotateInstanceWebhookCredential: api.rotate,
  stopStrategyInstance: api.stop,
  testInstanceWebhookConnection: api.test,
  updateStrategyInstanceLots: api.lots,
  listPineStrategies: api.pineList,
  getPineStrategy: api.pineGet,
  createPineStrategy: api.pineCreate,
  createPineVersion: api.pineVersion,
  validatePineVersion: api.pineValidate,
  submitPineVersion: api.pineSubmit,
  getPineSource: api.pineSource,
  linkPineVersion: api.pineLink,
  listPineReviews: api.reviewList,
  getPineReview: api.reviewGet,
  decidePineReview: api.reviewDecide,
  createTradingViewSetup: vi.fn(),
  getTradingViewSetup: vi.fn(),
  listManagedTradingViewSetups: vi.fn().mockResolvedValue([]),
  recordManagedTradingViewInstallation: vi.fn(),
}))

const TOKEN = 'nwk_SENTINEL_PHASE3B_BROWSER_CREDENTIAL_123456'

function instance(overrides: Partial<StrategyInstance> = {}): StrategyInstance {
  return {
    id: 'instance-a', strategy_id: 'strategy-a', strategy_version_id: 'version-a',
    strategy_code: 'supertrend', strategy_display_name: 'Supertrend', source_journey: 'PERSONAL_TRADINGVIEW',
    label: 'My private strategy', status: 'ready', status_reason: null, execution_mode: 'paper_live_data',
    current_lots: 2, created_at: '2026-07-14T10:00:00Z', updated_at: '2026-07-14T10:00:00Z',
    archived_at: null, webhook_credential: null, lot_size: 65, estimated_quantity: 130,
    last_signal_time: null, last_execution_status: null,
    readiness: { paper_mode: true, valid_lots: true, active_credential: false, connection_tested: false, can_activate: false },
    ...overrides,
  }
}

const hold: WebhookExecution = {
  signal_id: 'connection-test-safe', strategy_instance_id: 'instance-a', action: 'HOLD',
  signal_time: '2026-07-14T10:00:00Z', received_at: '2026-07-14T10:00:01Z', status: 'completed',
  reason: 'HOLD', execution_mode: null, job_status: null, job_created_at: null, job_completed_at: null, result: null,
}

function arrange(detail = instance(), executions: WebhookExecution[] = []) {
  let current = detail
  api.list.mockImplementation(() => Promise.resolve([current]))
  api.get.mockImplementation(() => Promise.resolve(current))
  api.history.mockResolvedValue({ executions, limit: 10, offset: 0 })
  api.generate.mockImplementation(() => {
    current = instance({
      ...current,
      webhook_credential: { id: 'credential-a', strategy_instance_id: current.id, token_prefix: TOKEN.slice(0, 10), created_at: null, last_used_at: null, revoked_at: null },
      readiness: { ...current.readiness!, active_credential: true },
    })
    return Promise.resolve({ ...current.webhook_credential!, token: TOKEN })
  })
  api.rotate.mockResolvedValue({ id: 'credential-b', strategy_instance_id: current.id, token_prefix: 'nwk_rotate', created_at: null, last_used_at: null, revoked_at: null, token: TOKEN })
  api.revoke.mockResolvedValue({ id: 'credential-a', strategy_instance_id: current.id, token_prefix: 'nwk_', created_at: null, last_used_at: null, revoked_at: '2026-07-14' })
  api.test.mockResolvedValue({ status: 'NO_OP', signal_id: hold.signal_id })
  api.lots.mockResolvedValue(current)
  api.activate.mockResolvedValue(current)
  api.pause.mockResolvedValue(current)
  api.resume.mockResolvedValue(current)
  api.stop.mockResolvedValue(current)
  return () => current
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } })
})

afterEach(() => cleanup())

describe('PersonalStrategiesPage', () => {
  it('creates only a personal paper strategy after acknowledgement', async () => {
    api.list.mockResolvedValue([])
    api.create.mockResolvedValue(instance())
    api.get.mockResolvedValue(instance())
    api.history.mockResolvedValue({ executions: [], limit: 10, offset: 0 })
    const user = userEvent.setup()
    render(<PersonalStrategiesPage />)
    await screen.findByText('No personal strategies')
    await user.click(screen.getByRole('button', { name: /new strategy/i }))
    const create = screen.getByRole('button', { name: /^create strategy$/i })
    expect(create).toBeDisabled()
    await user.type(screen.getByLabelText('Strategy name'), 'Opening range route')
    await user.click(screen.getByLabelText(/paper-only/i))
    await user.click(create)
    await waitFor(() => expect(api.create).toHaveBeenCalledWith(expect.objectContaining({
      source_journey: 'PERSONAL_TRADINGVIEW', execution_mode: 'paper_live_data', label: 'Opening range route',
    })))
    expect(JSON.stringify(api.create.mock.calls)).not.toContain('real_orders')
  })

  it('masks the one-time credential, copies body JSON explicitly, and never uses browser storage or URL state', async () => {
    arrange()
    const originalUrl = window.location.href
    const user = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, 'writeText')
    render(<PersonalStrategiesPage />)
    await screen.findByRole('heading', { name: 'My private strategy' })
    await user.click(screen.getByRole('button', { name: /generate credential/i }))
    await screen.findByText('Shown only now')
    expect(screen.queryByText(TOKEN)).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain(TOKEN)
    await user.click(screen.getByRole('button', { name: /reveal webhook credential/i }))
    expect(screen.getByText(TOKEN)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /copy BUY_CE JSON/i }))
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining(`"credential": "${TOKEN}"`))
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    expect(window.location.href).toBe(originalUrl)
    expect(window.location.search).not.toContain(TOKEN)
  })

  it('edits lots, sends the durable HOLD test, and displays paginated history', async () => {
    arrange(instance({
      webhook_credential: { id: 'credential-a', strategy_instance_id: 'instance-a', token_prefix: 'nwk_safe', created_at: null, last_used_at: null, revoked_at: null },
      readiness: { paper_mode: true, valid_lots: true, active_credential: true, connection_tested: true, can_activate: true },
    }), [hold])
    const user = userEvent.setup()
    render(<PersonalStrategiesPage />)
    expect((await screen.findAllByText('No action needed')).length).toBeGreaterThan(1)
    const lots = screen.getByLabelText('Lots', { selector: '#personal-lots' })
    fireEvent.change(lots, { target: { value: '3' } })
    await user.click(screen.getByRole('button', { name: /save lots/i }))
    await waitFor(() => expect(api.lots).toHaveBeenCalledWith('instance-a', 3))
    await user.click(screen.getByRole('button', { name: /send paper HOLD test/i }))
    await waitFor(() => expect(api.test).toHaveBeenCalledWith('instance-a'))
    expect(screen.getByText('Page 1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /newer/i })).toBeDisabled()
  })

  it('activates only a ready strategy and exposes pause, stop, rotate, and revoke controls', async () => {
    const credential = { id: 'credential-a', strategy_instance_id: 'instance-a', token_prefix: 'nwk_safe', created_at: null, last_used_at: null, revoked_at: null }
    arrange(instance({
      webhook_credential: credential,
      readiness: { paper_mode: true, valid_lots: true, active_credential: true, connection_tested: true, can_activate: true },
    }))
    let user = userEvent.setup()
    const first = render(<PersonalStrategiesPage />)
    await screen.findByRole('heading', { name: 'My private strategy' })
    await user.click(screen.getByRole('button', { name: /^activate$/i }))
    await waitFor(() => expect(api.activate).toHaveBeenCalledWith('instance-a'))
    first.unmount()

    vi.clearAllMocks()
    arrange(instance({
      status: 'active', webhook_credential: credential,
      readiness: { paper_mode: true, valid_lots: true, active_credential: true, connection_tested: true, can_activate: true },
    }))
    user = userEvent.setup()
    render(<PersonalStrategiesPage />)
    await screen.findByRole('heading', { name: 'My private strategy' })
    await user.click(screen.getByRole('button', { name: /^pause$/i }))
    await waitFor(() => expect(api.pause).toHaveBeenCalled())
    await user.click(screen.getByRole('button', { name: /^stop$/i }))
    await user.click(screen.getByRole('button', { name: /^rotate$/i }))
    await user.click(screen.getByRole('button', { name: /^revoke$/i }))
    await waitFor(() => {
      expect(api.stop).toHaveBeenCalled()
      expect(api.rotate).toHaveBeenCalled()
      expect(api.revoke).toHaveBeenCalled()
    })
  })

  it('shows safe API errors without leaking a sentinel credential', async () => {
    arrange()
    api.generate.mockRejectedValue(new Error('Private webhook execution feature disabled'))
    const user = userEvent.setup()
    render(<PersonalStrategiesPage />)
    await screen.findByRole('heading', { name: 'My private strategy' })
    await user.click(screen.getByRole('button', { name: /generate credential/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('feature disabled')
    expect(document.body.textContent).not.toContain(TOKEN)
  })

  it('keeps an open-position strategy paused when stop returns a conflict', async () => {
    const credential = { id: 'credential-a', strategy_instance_id: 'instance-a', token_prefix: 'nwk_safe', created_at: null, last_used_at: null, revoked_at: null }
    arrange(instance({
      status: 'paused', has_open_position: true, webhook_credential: credential,
      readiness: { paper_mode: true, valid_lots: true, active_credential: true, connection_tested: true, can_activate: true },
    }))
    api.stop.mockRejectedValue(new Error('Close the open position before stopping this strategy. You may pause it now to block new entries while keeping exits available.'))
    const user = userEvent.setup()
    render(<PersonalStrategiesPage />)
    expect(await screen.findByText('New entries are blocked.')).toBeInTheDocument()
    expect(screen.getByText(/Exit signals and NOVA protective exits remain active/)).toBeInTheDocument()
    expect(screen.getByText(/Revoking this credential disables TradingView exit signals/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^stop$/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Close the open position')
    expect(screen.getAllByText('Paused')).toHaveLength(2)
    expect(screen.getByRole('button', { name: /^stop$/i })).toBeInTheDocument()
    expect(document.body.textContent).not.toContain(TOKEN)
  })

  it('hides credential provisioning and the private HOLD test from a NOVA-managed user, and shows the exact blocker', async () => {
    arrange(instance({
      requires_managed_setup: true, setup_type: 'NOVA_MANAGED_TRADINGVIEW', credential_status: 'active',
      webhook_credential: { id: 'c', strategy_instance_id: 'instance-a', token_prefix: 'nwk_x', created_at: null, last_used_at: null, revoked_at: null },
      readiness: { paper_mode: true, valid_lots: true, active_credential: true, approved_version: true, installation_confirmed: true, hold_verified: true, paper_entry_verified: true, paper_exit_verified: false, can_activate: false },
      blocking_code: 'PAPER_EXIT_NOT_VERIFIED',
    }))
    render(<PersonalStrategiesPage />)
    await screen.findByRole('heading', { name: 'My private strategy' })
    expect(screen.queryByRole('button', { name: /generate credential/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /send paper HOLD test/i })).not.toBeInTheDocument()
    expect(screen.getByText(/Credential configured by NOVA/i)).toBeInTheDocument()
    expect(screen.getByText(/Not ready — Waiting for a confirmed paper exit/i)).toBeInTheDocument()
  })
})
