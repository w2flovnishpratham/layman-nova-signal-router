import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { C2MyStrategies } from './C2MyStrategies'

const api = vi.hoisted(() => ({
  list: vi.fn(), get: vi.fn(), generate: vi.fn(), rotate: vi.fn(), revoke: vi.fn(),
}))

vi.mock('../api', () => ({
  listMyC2Installations: api.list,
  getMyC2Installation: api.get,
  generateSelfC2Credential: api.generate,
  rotateSelfC2Credential: api.rotate,
  revokeSelfC2Credential: api.revoke,
}))

const setupPackage = {
  strategy_name: 'Legend MACD', instance_label: 'Legend Paper', mode: 'SELF' as const,
  approved_pine: '//@version=6\nindicator("Legend")\n', candidate_sha256: 'b'.repeat(64),
  webhook_url: '/api/webhooks/private',
  alert_message: '{"credential":"{{ONE_TIME_CREDENTIAL}}","action":"HOLD"}',
  credential_display: 'placeholder' as const,
  expected_hold_behavior: 'A valid HOLD creates no job, order, or position.',
  instructions: 'Install this exact candidate manually.',
}

const installation = {
  id: 'install-1', owner_user_id: 'owner-1', conversion_id: 'conv-1',
  compile_evidence_id: 'compile-1', strategy_id: 'strategy-1', strategy_name: 'Legend MACD',
  strategy_version_id: 'version-1', strategy_version: '2', candidate_sha256: 'b'.repeat(64),
  source_sha256: 'a'.repeat(64), mode: 'SELF' as const, status: 'AWAITING_HOLD',
  strategy_instance_id: 'instance-1', instance_label: 'Legend Paper', instance_status: 'ready',
  execution_mode: 'signal_only' as const, credential_status: 'NOT_GENERATED' as const,
  credential: null, hold_status: 'AWAITING_HOLD' as const, hold_verified_at: null,
  paper_eligible: false, paper_eligible_at: null, live_eligible: false as const,
  gates: {}, blocking_reasons: ['Credential not generated', 'Awaiting HOLD'], suspended_at: null,
  created_at: '2026-07-19T10:00:00Z', updated_at: '2026-07-19T10:00:00Z',
  setup_package: setupPackage,
}

const issued = {
  id: 'credential-1', strategy_instance_id: 'instance-1', token_prefix: 'nwk_once',
  token: 'nwk_C2_OWNER_ONE_TIME_SECRET', created_at: '2026-07-19T10:00:00Z',
  setup_package: {
    ...setupPackage,
    alert_message: '{"credential":"nwk_C2_OWNER_ONE_TIME_SECRET","action":"HOLD"}',
    credential_display: 'one_time' as const,
  },
}

beforeEach(() => {
  vi.clearAllMocks()
  api.list.mockResolvedValue([installation])
  api.get.mockResolvedValue(installation)
  api.generate.mockResolvedValue(issued)
  api.rotate.mockResolvedValue({ ...issued, id: 'credential-2', token: 'nwk_ROTATED' })
  api.revoke.mockResolvedValue({ ...installation, credential_status: 'REVOKED' })
})

afterEach(() => cleanup())

describe('C2MyStrategies', () => {
  it('rehydrates owner-only installation state and keeps unverified strategy not ready', async () => {
    render(<C2MyStrategies />)
    expect(await screen.findByText('Legend MACD')).toBeInTheDocument()
    expect(screen.getAllByText('Credential required')).toHaveLength(2)
    expect(screen.getByText('NOT READY')).toBeInTheDocument()
    expect(screen.getByText('UNAVAILABLE')).toBeInTheDocument()
    expect(api.list).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledWith('install-1')
  })

  it('displays a self credential once, never persists it, and cannot reopen it', async () => {
    const user = userEvent.setup()
    render(<C2MyStrategies />)
    await user.click(await screen.findByRole('button', { name: /Generate one-time credential/i }))
    expect(await screen.findByRole('dialog', { name: 'One-time self credential' })).toBeInTheDocument()
    expect(screen.queryByText(issued.token)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Reveal' }))
    expect(screen.getByText(issued.token)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Dismiss permanently/i }))
    expect(screen.queryByRole('dialog', { name: 'One-time self credential' })).not.toBeInTheDocument()
    expect(screen.queryByText(issued.token)).not.toBeInTheDocument()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('shows managed status without source or credential controls', async () => {
    const managed = {
      ...installation, id: 'managed-1', mode: 'MANAGED' as const,
      setup_package: undefined, credential_status: 'ACTIVE' as const,
      blocking_reasons: ['Awaiting HOLD'],
    }
    api.list.mockResolvedValue([managed])
    api.get.mockResolvedValue(managed)
    render(<C2MyStrategies />)
    expect(await screen.findByText(/Managed setup is handled by NOVA administrators/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Generate one-time credential/i })).not.toBeInTheDocument()
    expect(screen.queryByText(setupPackage.approved_pine)).not.toBeInTheDocument()
  })

  it('renders HOLD verified and Paper ready while stating the engine remains stopped', async () => {
    const ready = {
      ...installation, status: 'PAPER_ELIGIBLE', credential_status: 'ACTIVE' as const,
      credential: { id: 'credential-1', token_prefix: 'nwk_once', created_at: 'now', last_verified_at: 'now' },
      hold_status: 'VERIFIED' as const, hold_verified_at: '2026-07-19T10:10:00Z',
      paper_eligible: true, paper_eligible_at: '2026-07-19T10:10:00Z', blocking_reasons: [],
    }
    api.list.mockResolvedValue([ready])
    api.get.mockResolvedValue(ready)
    render(<C2MyStrategies />)
    await waitFor(() => expect(screen.getAllByText('Paper ready').length).toBeGreaterThan(0))
    expect(screen.getByText(/engine remains stopped/i)).toBeInTheDocument()
    expect(screen.getByText('VERIFIED')).toBeInTheDocument()
    expect(screen.getByText('UNAVAILABLE')).toBeInTheDocument()
  })

  it('refreshes from server state after a genuine HOLD', async () => {
    const ready = {
      ...installation, status: 'PAPER_ELIGIBLE', credential_status: 'ACTIVE' as const,
      credential: { id: 'credential-1', token_prefix: 'nwk_once', created_at: 'now', last_verified_at: 'now' },
      hold_status: 'VERIFIED' as const, hold_verified_at: '2026-07-19T10:10:00Z',
      paper_eligible: true, paper_eligible_at: '2026-07-19T10:10:00Z', blocking_reasons: [],
    }
    api.list.mockResolvedValueOnce([installation]).mockResolvedValue([ready])
    api.get.mockResolvedValueOnce(installation).mockResolvedValue(ready)
    const user = userEvent.setup()
    render(<C2MyStrategies />)
    expect(await screen.findByText('NOT READY')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Refresh/i }))
    await waitFor(() => expect(screen.getByText('READY')).toBeInTheDocument())
    expect(screen.getByText('VERIFIED')).toBeInTheDocument()
    expect(screen.getByText('UNAVAILABLE')).toBeInTheDocument()
  })
})
