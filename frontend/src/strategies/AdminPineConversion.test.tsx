import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AdminPineConversionWorkspace } from './AdminPineConversion'

const api = vi.hoisted(() => ({
  list: vi.fn(), get: vi.fn(), submit: vi.fn(), convert: vi.fn(), manualPackage: vi.fn(),
  manualResponse: vi.fn(), approve: vi.fn(), reject: vi.fn(), requestChanges: vi.fn(),
}))
const toastApi = vi.hoisted(() => ({
  add: vi.fn(),
  promise: vi.fn(async (request: Promise<unknown>) => request),
}))
vi.mock('@/components/ui/toast', () => ({ toast: toastApi }))

vi.mock('../api', () => ({
  listAdminPineConversions: api.list,
  getAdminPineConversion: api.get,
  submitAdminPineConversion: api.submit,
  runAdminPineConversion: api.convert,
  getAdminPineManualPackage: api.manualPackage,
  submitAdminPineManualResponse: api.manualResponse,
  approveAdminPineConversion: api.approve,
  rejectAdminPineConversion: api.reject,
  requestChangesAdminPineConversion: api.requestChanges,
  getC2Config: vi.fn().mockResolvedValue({ enabled: false }),
  getAdminC2Conversion: vi.fn(),
  listAdminC2Installations: vi.fn().mockResolvedValue({ installations: [] }),
  listAdminUsers: vi.fn().mockResolvedValue([]),
  recordC2CompileSuccess: vi.fn(),
  recordC2CompileFailure: vi.fn(),
  downloadC2ApprovedPine: vi.fn(),
  createC2Installation: vi.fn(),
  generateAdminC2Credential: vi.fn(),
  rotateAdminC2Credential: vi.fn(),
  revokeAdminC2Credential: vi.fn(),
  suspendAdminC2Installation: vi.fn(),
}))

const SOURCE = '//@version=6\nindicator("NIFTY source", overlay=true)\n'
const LAYER = '//@version=6\nindicator("NIFTY converted", overlay=true)\nbool novaBuyCeSignal = true\nbool novaBuyPeSignal = false\nbool novaExitSignal = false\n'
const BACKTEST_LAYER = '//@version=6\nstrategy("NIFTY converted backtest", overlay=true, default_qty_type=strategy.fixed, default_qty_value=1)\nbool novaBuyCeSignal = true\nif novaBuyCeSignal\n    strategy.entry("CE", strategy.long)\n'
const TRANSPORT = '// === NOVA FROZEN TRANSPORT BEGIN: pine_transport_v2 ===\n// transport\n'
const validation = {
  id: 'report-1', status: 'PASSED_WITH_WARNINGS', validator_version: '1.0.0', contract_version: 1,
  source_sha256: 'b'.repeat(64), error_count: 0, warning_count: 1, info_count: 0,
  eligible_for_review: true,
  findings: [{ code: 'EOD_HANDLING_UNCONFIRMED', severity: 'WARNING', title: 'EOD review', explanation: 'Review it.', remediation: 'Compile separately.', blocks_review: false, line: null, column: null, excerpt: null }],
}
const ready = {
  id: 'c1', strategy_id: 's1', strategy_name: 'Legend MACD', input_version_id: 'v1',
  candidate_version_id: 'v2', source_sha256: 'a'.repeat(64), candidate_sha256: 'b'.repeat(64),
  strategy_layer_sha256: 'c'.repeat(64), submitted_at: '2026-07-18T10:00:00Z',
  owner_user_id: 'owner-1', analysis_status: 'ANALYZED', conversion_status: 'READY_FOR_ADMIN_REVIEW',
  provider: 'anthropic_claude', model: 'claude-test', provider_mode: 'CLAUDE_API',
  validation_status: 'PASSED', review_status: 'PENDING', safe_error_code: null,
  analysis: {
    analyzer_version: 'v1', registry_version: 'v1', registry_sha256: 'd'.repeat(64),
    source_sha256: 'a'.repeat(64), matched_capabilities: ['BASIC_INDICATOR_BOOLEAN_SIGNAL'],
    unsupported_capabilities: [], warnings: [], blockers: [], admin_review_points: ['Review crossover timing'],
    effective_capability_level: 'L0_DIRECTLY_SUPPORTED', confidence: 'HIGH_CONFIDENCE_MATCH',
  },
  conversion_guidance: null,
  provenance: { input_token_count: 120, output_token_count: 80, cache_status: 'MISS', repair_count: 0 },
  validation, conversion_summary: 'Preserved behavior.', warnings: [], unsupported_features: [],
  action_mapping: { buy_ce_source: 'cross', buy_pe_source: 'under', exit_source: 'false' },
  original_source: SOURCE, strategy_layer: LAYER, backtest_layer: BACKTEST_LAYER, final_candidate: `${LAYER}\n${TRANSPORT}`,
  transport_source: TRANSPORT,
  diff: [{ kind: 'removed', text: 'indicator("NIFTY source", overlay=true)' }, { kind: 'added', text: 'indicator("NIFTY converted", overlay=true)' }],
  approval_integrity: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  api.list.mockResolvedValue([ready])
  api.get.mockResolvedValue(ready)
  api.submit.mockResolvedValue(ready)
  api.convert.mockResolvedValue(ready)
  api.manualPackage.mockResolvedValue({ package: 'CONTROLLED PACKAGE', filename: 'manual.txt', package_sha256: 'x', source_sha256: ready.source_sha256 })
  api.manualResponse.mockResolvedValue(ready)
  api.approve.mockResolvedValue({ ...ready, conversion_status: 'APPROVED_FOR_TRADINGVIEW_COMPILE', review_status: 'APPROVED_FOR_TRADINGVIEW_COMPILE', approval_integrity: true })
  api.reject.mockResolvedValue({ ...ready, conversion_status: 'REJECTED', review_status: 'REJECTED' })
  api.requestChanges.mockResolvedValue({ ...ready, conversion_status: 'CHANGES_REQUESTED', review_status: 'CHANGES_REQUESTED' })
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('AdminPineConversionWorkspace', () => {
  it('rehydrates the conversion detail with analysis, diff, validation, transport, and provenance', async () => {
    render(<AdminPineConversionWorkspace />)
    expect((await screen.findAllByText('Legend MACD')).length).toBeGreaterThan(0)
    expect(api.get).toHaveBeenCalledWith('c1')
    expect(screen.getByText(/L0_DIRECTLY_SUPPORTED/)).toBeInTheDocument()
    expect(screen.getByText(/EOD_HANDLING_UNCONFIRMED/)).toBeInTheDocument()
    expect(screen.getByText(/120 in \/ 80 out/)).toBeInTheDocument()
    expect(screen.getAllByText(/indicator\("NIFTY converted"/).length).toBeGreaterThan(0)
    expect(screen.getByText('Server-added Transport V2')).toBeInTheDocument()
  })

  it('shows the backtest layer with a working copy button', async () => {
    const user = userEvent.setup()
    render(<AdminPineConversionWorkspace />)
    await screen.findAllByText('Legend MACD')
    expect(screen.getByText(/Backtest layer/)).toBeInTheDocument()
    expect(screen.getByText(/strategy\("NIFTY converted backtest"/)).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: /copy/i })[0])
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(BACKTEST_LAYER)
  })

  it('submits pasted or uploaded UTF-8 Pine with no options, model, or prompt controls', async () => {
    const user = userEvent.setup()
    api.list.mockResolvedValue([])
    render(<AdminPineConversionWorkspace />)
    await screen.findByText('No conversion submissions')
    await user.type(screen.getByLabelText('Conversion strategy name'), 'New strategy')
    const uploaded = new File([SOURCE], 'source.pine', { type: 'text/plain' })
    fireEvent.change(screen.getByLabelText('Upload Pine source for conversion'), { target: { files: [uploaded] } })
    await waitFor(() => expect(screen.getByLabelText('Admin Pine source')).toHaveValue(SOURCE))
    await user.click(screen.getByRole('button', { name: /submit and analyze/i }))
    await waitFor(() => expect(api.submit).toHaveBeenCalledWith({
      strategy_name: 'New strategy', source: SOURCE, original_filename: 'source.pine', internal_notes: undefined,
    }))
    expect(screen.queryByLabelText(/symbol/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/timeframe/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/setup type/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/model/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/prompt/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/api key/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/credential:/i)).not.toBeInTheDocument()
  })

  it('shows blocked and failed states without permitting AI conversion or approval', async () => {
    const blocked = {
      ...ready, candidate_version_id: null, conversion_status: 'UNSUPPORTED_STRATEGY',
      validation: null, validation_status: 'NOT_RUN', final_candidate: null, strategy_layer: null,
      analysis: { ...ready.analysis, blockers: ['BLK_FUTURE_LEAK'] },
    }
    api.list.mockResolvedValue([blocked]); api.get.mockResolvedValue(blocked)
    render(<AdminPineConversionWorkspace />)
    expect(await screen.findByText(/BLK_FUTURE_LEAK/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /run ai conversion/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /approve for tradingview compile/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /open manual fallback/i })).toBeDisabled()
  })

  it('shows advisory conversion guidance for a matched capability without ever gating conversion', async () => {
    const advisory = {
      ...ready, candidate_version_id: null, conversion_status: 'READY_FOR_CONVERSION',
      validation: null, validation_status: 'NOT_RUN', final_candidate: null, strategy_layer: null,
      analysis: {
        ...ready.analysis,
        matched_capabilities: ['OPPOSITE_DIRECTION_REVERSAL_NORMALIZATION', 'PENDING_ORDER_CANCELLATION', 'PENDING_STOP_ENTRY'],
        blockers: ['BLK_PENDING_ENGINE'], effective_capability_level: 'L3_REQUIRES_BACKEND_CAPABILITY',
        confidence: 'PARTIAL_MATCH',
      },
      conversion_guidance: {
        blockers: ['BLK_PENDING_ENGINE'],
        matched_capabilities: ['PENDING_ORDER_CANCELLATION', 'PENDING_STOP_ENTRY'],
        notes: [{
          blocker_code: 'BLK_PENDING_ENGINE', title: 'Pending order engine',
          original_semantics: ['Pending stop or limit entry orders'],
          proposed_semantics: ['An opposite signal while a position is open emits EXIT only'],
        }],
      },
    }
    api.list.mockResolvedValue([advisory]); api.get.mockResolvedValue(advisory)
    render(<AdminPineConversionWorkspace />)
    expect((await screen.findAllByText(/BLK_PENDING_ENGINE/)).length).toBeGreaterThan(0)
    expect(screen.getByText('Conversion guidance given to Claude')).toBeInTheDocument()
    expect(screen.getByText('Pending stop or limit entry orders')).toBeInTheDocument()
    expect(screen.getByText(/opposite signal while a position is open emits EXIT only/)).toBeInTheDocument()
    // Advisory only -- conversion stays reachable, nothing here requires a decision.
    expect(screen.getByRole('button', { name: /run ai conversion/i })).toBeEnabled()
    expect(screen.getByRole('button', { name: /open manual fallback/i })).toBeEnabled()
  })

  it('runs AI conversion with a visible loading boundary and safe failure state', async () => {
    const pending = { ...ready, candidate_version_id: null, conversion_status: 'READY_FOR_CONVERSION', validation: null, final_candidate: null, strategy_layer: null }
    api.list.mockResolvedValue([pending]); api.get.mockResolvedValue(pending)
    let resolve!: (value: Record<string, unknown>) => void
    api.convert.mockReturnValue(new Promise((done) => { resolve = done }))
    const user = userEvent.setup()
    render(<AdminPineConversionWorkspace />)
    const button = await screen.findByRole('button', { name: /run ai conversion/i })
    await user.click(button)
    expect(button).toBeDisabled()
    resolve({ ...ready, conversion_status: 'MANUAL_CONVERSION_REQUIRED', safe_error_code: 'PROVIDER_TIMEOUT' })
    await waitFor(() => expect(api.convert).toHaveBeenCalledWith('c1'))
  })

  it('copies the permanent manual package and submits the structured response through the same review path', async () => {
    const user = userEvent.setup()
    render(<AdminPineConversionWorkspace />)
    await user.click(await screen.findByRole('button', { name: /open manual fallback/i }))
    await waitFor(() => expect(api.manualPackage).toHaveBeenCalledWith('c1'))
    expect(toastApi.add).toHaveBeenCalledWith(expect.objectContaining({
      title: 'Manual package copy completed.',
      type: 'success',
    }))
    expect(screen.getByText('Current C1 manual package')).toBeInTheDocument()
    expect(screen.getByText('CONTROLLED PACKAGE')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Manual Claude response JSON'), { target: { value: '{"schema_version":"v1"}' } })
    await user.click(screen.getByRole('button', { name: /submit manual response/i }))
    await waitFor(() => expect(api.manualResponse).toHaveBeenCalledWith('c1', '{"schema_version":"v1"}'))
  })

  it('binds approval to explicit confirmation', async () => {
    const user = userEvent.setup()
    render(<AdminPineConversionWorkspace />)
    const approve = await screen.findByRole('button', { name: /approve for tradingview compile/i })
    expect(approve).toBeEnabled()
    await user.click(approve)
    await waitFor(() => expect(window.confirm).toHaveBeenCalled())
    expect(api.approve).toHaveBeenCalledWith('c1', '')
  })

  it('requires a reason before request-changes or reject are enabled, and records it', async () => {
    const user = userEvent.setup()
    render(<AdminPineConversionWorkspace />)
    await screen.findByRole('button', { name: /approve for tradingview compile/i })
    const requestChanges = screen.getByRole('button', { name: /request changes/i })
    const reject = screen.getByRole('button', { name: /reject candidate/i })
    expect(requestChanges).toBeDisabled()
    expect(reject).toBeDisabled()
    await user.type(screen.getByLabelText('Conversion review reason'), 'TradingView mismatch')
    expect(requestChanges).toBeEnabled()
    expect(reject).toBeEnabled()
    await user.click(requestChanges)
    await waitFor(() => expect(api.requestChanges).toHaveBeenCalledWith('c1', 'TradingView mismatch'))
  })

  it('records the rejection reason', async () => {
    const user = userEvent.setup()
    render(<AdminPineConversionWorkspace />)
    await screen.findByRole('button', { name: /approve for tradingview compile/i })
    await user.type(screen.getByLabelText('Conversion review reason'), 'TradingView mismatch')
    await user.click(screen.getByRole('button', { name: /reject candidate/i }))
    await waitFor(() => expect(api.reject).toHaveBeenCalledWith('c1', 'TradingView mismatch'))
  })
})
