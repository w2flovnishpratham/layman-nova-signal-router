import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { C2AdminPanel } from './C2AdminPanel'

const api = vi.hoisted(() => ({
  config: vi.fn(), detail: vi.fn(), installations: vi.fn(), users: vi.fn(),
  compileSuccess: vi.fn(), compileFailure: vi.fn(), download: vi.fn(), create: vi.fn(),
  generate: vi.fn(), rotate: vi.fn(), revoke: vi.fn(), suspend: vi.fn(),
}))

vi.mock('../api', () => ({
  getC2Config: api.config,
  getAdminC2Conversion: api.detail,
  listAdminC2Installations: api.installations,
  listAdminUsers: api.users,
  recordC2CompileSuccess: api.compileSuccess,
  recordC2CompileFailure: api.compileFailure,
  downloadC2ApprovedPine: api.download,
  createC2Installation: api.create,
  generateAdminC2Credential: api.generate,
  rotateAdminC2Credential: api.rotate,
  revokeAdminC2Credential: api.revoke,
  suspendAdminC2Installation: api.suspend,
}))

const conversion = {
  id: 'conv-1', strategy_id: 'strategy-1', strategy_name: 'Legend MACD',
  input_version_id: 'input-1', candidate_version_id: 'candidate-1',
  source_sha256: 'a'.repeat(64), candidate_sha256: 'b'.repeat(64),
  strategy_layer_sha256: 'c'.repeat(64), submitted_at: '2026-07-19T10:00:00Z',
  analysis_status: 'ANALYZED', conversion_status: 'APPROVED_FOR_TRADINGVIEW_COMPILE',
  provider: 'anthropic_claude', model: 'test', provider_mode: 'MANUAL_ADMIN_COPY_PASTE',
  validation_status: 'PASSED', review_status: 'APPROVED_FOR_TRADINGVIEW_COMPILE',
  safe_error_code: null, analysis: {
    analyzer_version: 'v1', registry_version: 'v1', registry_sha256: 'd'.repeat(64),
    source_sha256: 'a'.repeat(64), matched_capabilities: [], unsupported_capabilities: [],
    warnings: [], blockers: [], admin_review_points: [], effective_capability_level: 'L0',
    confidence: 'HIGH',
  }, provenance: {}, validation: null, conversion_summary: null, warnings: [],
  unsupported_features: [], action_mapping: {}, approval_integrity: true,
}

const candidate = {
  conversion_id: 'conv-1', strategy_name: 'Legend MACD', candidate_version_id: 'candidate-1',
  version: '2', pine: '//@version=6\nindicator("Legend")\n', source_sha256: 'a'.repeat(64),
  strategy_layer_sha256: 'c'.repeat(64), candidate_sha256: 'b'.repeat(64),
  prompt_version: 'v3.1', prompt_sha256: 'd'.repeat(64),
  transport_version: 'pine_transport_v2', transport_sha256: 'e'.repeat(64),
}

const compile = {
  id: 'compile-1', conversion_id: 'conv-1', candidate_version_id: 'candidate-1',
  result: 'SUCCESS' as const, source_sha256: 'a'.repeat(64),
  strategy_layer_sha256: 'c'.repeat(64), candidate_sha256: 'b'.repeat(64),
  prompt_version: 'v3.1', prompt_sha256: 'd'.repeat(64),
  transport_version: 'pine_transport_v2', transport_sha256: 'e'.repeat(64),
  compiler_error_summary: null, setup_notes: null, compiled_at: '2026-07-19T10:00:00Z',
}

const installation = {
  id: 'install-1', owner_user_id: 'owner-1', conversion_id: 'conv-1',
  compile_evidence_id: 'compile-1', strategy_id: 'strategy-1', strategy_name: 'Legend MACD',
  strategy_version_id: 'candidate-1', strategy_version: '2', candidate_sha256: 'b'.repeat(64),
  source_sha256: 'a'.repeat(64), mode: 'SELF' as const, status: 'AWAITING_HOLD',
  strategy_instance_id: 'instance-1', instance_label: 'Legend Paper', instance_status: 'ready',
  execution_mode: 'signal_only' as const, credential_status: 'NOT_GENERATED' as const,
  credential: null, hold_status: 'AWAITING_HOLD' as const, hold_verified_at: null,
  paper_eligible: false, paper_eligible_at: null, live_eligible: false as const,
  gates: {}, blocking_reasons: ['Credential not generated'], suspended_at: null,
  created_at: '2026-07-19T10:00:00Z', updated_at: '2026-07-19T10:00:00Z',
}

const issued = {
  id: 'cred-1', strategy_instance_id: 'instance-1', token_prefix: 'nwk_once',
  token: 'nwk_ONE_TIME_C2_SECRET',
  created_at: '2026-07-19T10:00:00Z',
  setup_package: {
    strategy_name: 'Legend MACD', instance_label: 'Legend Paper', mode: 'SELF' as const,
    approved_pine: candidate.pine, candidate_sha256: candidate.candidate_sha256,
    webhook_url: '/api/webhooks/private',
    alert_message: '{"credential":"nwk_ONE_TIME_C2_SECRET","action":"HOLD"}',
    credential_display: 'one_time' as const, expected_hold_behavior: 'No jobs.',
    instructions: 'Install manually.',
  },
}

beforeEach(() => {
  vi.clearAllMocks()
  api.config.mockResolvedValue({ enabled: true })
  api.detail.mockResolvedValue({ enabled: true, compile: null, candidate })
  api.installations.mockResolvedValue({ installations: [] })
  api.users.mockResolvedValue([{ id: 'owner-1', email: 'owner@example.com', name: 'Owner', is_admin: false }])
  api.compileSuccess.mockResolvedValue({ compile })
  api.compileFailure.mockResolvedValue({ compile: { ...compile, result: 'FAILURE', compiler_error_summary: 'syntax error' } })
  api.create.mockResolvedValue({ installation })
  api.generate.mockResolvedValue({ credential: issued })
  api.rotate.mockResolvedValue({ credential: { ...issued, id: 'cred-2', token: 'nwk_ROTATED' } })
  api.revoke.mockResolvedValue({ installation: { ...installation, credential_status: 'REVOKED' } })
  api.suspend.mockResolvedValue({ installation: { ...installation, status: 'INSTALLATION_SUSPENDED' } })
})

afterEach(() => cleanup())

describe('C2AdminPanel', () => {
  it('is hidden when the server feature flag is off', async () => {
    api.config.mockResolvedValue({ enabled: false })
    render(<C2AdminPanel conversion={conversion as never} />)
    await waitFor(() => expect(api.config).toHaveBeenCalled())
    expect(screen.queryByRole('heading', { name: /TradingView installation/i })).not.toBeInTheDocument()
  })

  it('records manual compile success and sanitized compile failure paths', async () => {
    const user = userEvent.setup()
    render(<C2AdminPanel conversion={conversion as never} />)
    await user.type(await screen.findByLabelText('TradingView compile notes'), 'Compiled manually')
    await user.click(screen.getByRole('button', { name: /Record Compile Successful/i }))
    await waitFor(() => expect(api.compileSuccess).toHaveBeenCalledWith('conv-1', 'Compiled manually'))

    api.detail.mockResolvedValue({ enabled: true, compile: null, candidate })
    await user.type(screen.getByLabelText('TradingView compiler error'), 'safe syntax summary')
    await user.click(screen.getByRole('button', { name: /Record Compile Failed/i }))
    await waitFor(() => expect(api.compileFailure).toHaveBeenCalledWith('conv-1', 'safe syntax summary'))
  })

  it('selects SELF or MANAGED, validates an owner, and creates the installation', async () => {
    const user = userEvent.setup()
    api.detail.mockResolvedValue({ enabled: true, compile, candidate })
    render(<C2AdminPanel conversion={conversion as never} />)
    await user.selectOptions(await screen.findByLabelText('C2 installation mode'), 'MANAGED')
    await user.clear(screen.getByLabelText('C2 instance label'))
    await user.type(screen.getByLabelText('C2 instance label'), 'Managed Legend')
    await user.click(screen.getByRole('button', { name: 'Create Installation' }))
    await waitFor(() => expect(api.create).toHaveBeenCalledWith({
      conversion_id: 'conv-1', owner_user_id: 'owner-1', mode: 'MANAGED', instance_label: 'Managed Legend',
    }))
  })

  it('shows credential plaintext once and cannot reopen it after dismissal', async () => {
    const user = userEvent.setup()
    api.detail.mockResolvedValue({ enabled: true, compile, candidate })
    api.installations.mockResolvedValue({ installations: [installation] })
    render(<C2AdminPanel conversion={conversion as never} />)
    await user.click(await screen.findByRole('button', { name: /Generate Credential/i }))
    const dialog = await screen.findByRole('dialog', { name: 'One-time C2 credential' })
    expect(dialog).toBeInTheDocument()
    expect(screen.queryByText(issued.token)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Reveal' }))
    expect(screen.getByText(issued.token)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Dismiss permanently/i }))
    expect(screen.queryByRole('dialog', { name: 'One-time C2 credential' })).not.toBeInTheDocument()
    expect(screen.queryByText(issued.token)).not.toBeInTheDocument()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })
})
