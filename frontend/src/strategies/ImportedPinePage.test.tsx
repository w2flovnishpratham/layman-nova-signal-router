import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ImportedPinePage, claudeCompletionError } from './ImportedPinePage'

const api = vi.hoisted(() => ({
  list: vi.fn(), get: vi.fn(), create: vi.fn(), version: vi.fn(), validate: vi.fn(), submit: vi.fn(),
  source: vi.fn(), link: vi.fn(), instances: vi.fn(), reviews: vi.fn(), review: vi.fn(), decide: vi.fn(),
  conversionConfig: vi.fn(), conversionPackage: vi.fn(), convert: vi.fn(), conversion: vi.fn(), accept: vi.fn(), reject: vi.fn(), retry: vi.fn(),
  createSetup: vi.fn(), getSetup: vi.fn(),
  managedSetups: vi.fn(), recordInstallation: vi.fn(), managedCredential: vi.fn(),
  adminConversions: vi.fn(), adminConversion: vi.fn(),
  deleteStrategy: vi.fn(), conversionHistory: vi.fn(),
  ownerClaudeConfig: vi.fn(), ownerClaudeCreate: vi.fn(),
  ownerClaudeList: vi.fn(), ownerClaudeGet: vi.fn(),
}))
const toastApi = vi.hoisted(() => ({
  add: vi.fn(),
  promise: vi.fn(async (request: Promise<unknown>) => request),
}))
vi.mock('@/components/ui/toast', () => ({ toast: toastApi }))
vi.mock('../api', () => ({
  listPineStrategies: api.list, getPineStrategy: api.get, createPineStrategy: api.create,
  createPineVersion: api.version, validatePineVersion: api.validate, submitPineVersion: api.submit,
  getPineSource: api.source, linkPineVersion: api.link, listStrategyInstances: api.instances,
  listPineReviews: api.reviews, getPineReview: api.review, decidePineReview: api.decide,
  getPineConversionConfig: api.conversionConfig, generatePineConversionPackage: api.conversionPackage,
  createPineConversion: api.convert, getPineConversion: api.conversion, acceptPineConversion: api.accept,
  rejectPineConversion: api.reject, retryPineConversion: api.retry,
  createTradingViewSetup: api.createSetup, getTradingViewSetup: api.getSetup,
  listManagedTradingViewSetups: api.managedSetups, recordManagedTradingViewInstallation: api.recordInstallation,
  generateManagedTradingViewCredential: api.managedCredential,
  listAdminPineConversions: api.adminConversions, getAdminPineConversion: api.adminConversion,
  submitAdminPineConversion: vi.fn(), runAdminPineConversion: vi.fn(),
  getAdminPineManualPackage: vi.fn(), submitAdminPineManualResponse: vi.fn(),
  approveAdminPineConversion: vi.fn(), rejectAdminPineConversion: vi.fn(),
  deletePineStrategy: api.deleteStrategy, listPineConversions: api.conversionHistory,
  getOwnerClaudeConversionConfig: api.ownerClaudeConfig,
  createOwnerClaudeConversion: api.ownerClaudeCreate,
  listOwnerClaudeConversions: api.ownerClaudeList,
  getOwnerClaudeConversion: api.ownerClaudeGet,
}))

const MANAGED_TOKEN = 'nwk_MANAGED_SENTINEL_CREDENTIAL_0987654321'

const SOURCE = '//@version=6\nindicator("<script>alert(1)</script> NIFTY", overlay=true)\nalert("BUY_CE")\nalert("EXIT")\n'
const finding = { code: 'BAR_CONFIRMATION_MISSING', severity: 'WARNING', title: 'Confirm bars', explanation: 'May repeat.', remediation: 'Use confirmed bars.', blocks_review: false, line: 3, column: 1, excerpt: 'alert("BUY_CE")' }
const validation = { id: 'r1', status: 'PASSED_WITH_WARNINGS', validator_version: '1.0.0', contract_version: 1, source_sha256: 'abc', error_count: 0, warning_count: 1, info_count: 0, eligible_for_review: true, findings: [finding] }
const strategy = { id: 's1', name: 'Private script', description: null, status: 'active', version_count: 1, latest_version: null }
const version = { id: 'v1', strategy_id: 's1', version: '1.0.0', status: 'ready_for_review', source_sha256: 'abc', pine_contract_version: 1, changelog: null, created_at: null, approved_at: null, validation }

beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear(); sessionStorage.clear()
  api.list.mockResolvedValue([strategy]); api.get.mockResolvedValue({ strategy, versions: [version] })
  api.source.mockResolvedValue({ source: SOURCE, filename: 'safe.pine', source_sha256: 'abc', approved: false })
  api.instances.mockResolvedValue([]); api.reviews.mockResolvedValue([])
  api.conversionConfig.mockResolvedValue({ manual_package_enabled: true, ai_enabled: false, provider: null, model: null, prompt_version: 'v1', prompt_status: 'DEPLOYED', transport_version: null, contract_version: 1, daily_limit: 10 })
  api.conversionPackage.mockResolvedValue({ package: `PRIVATE WARNING\n${SOURCE}`, filename: 'conversion.txt', package_sha256: 'pkg' })
  api.getSetup.mockRejectedValue(new Error('not configured'))
  api.managedSetups.mockResolvedValue([])
  api.adminConversions.mockResolvedValue([])
  api.conversionHistory.mockResolvedValue([])
  api.ownerClaudeConfig.mockResolvedValue({
    enabled: false, provider: 'anthropic_claude', model: null,
    prompt_version: 'v4.0', transport_version: 'pine_transport_v2',
    admin_review_required: true, paper_verification_required: true,
    live_eligible: false,
  })
  api.ownerClaudeList.mockResolvedValue([])
  api.deleteStrategy.mockResolvedValue({ deleted: true, strategy_id: 's1' })
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } })
})
afterEach(() => cleanup())

/** OwnerWorkspace now opens browse-only by default (read-only list + code
 * viewer, matching the Strategies Redesign mockup) -- the editor only
 * mounts after clicking Edit (existing script) or New (blank script). */
async function openEditor(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /^edit$/i }))
}

describe('ImportedPinePage', () => {
  it('opens the import editor from an empty Pine library', async () => {
    api.list.mockResolvedValue([])
    const user = userEvent.setup()
    render(<ImportedPinePage />)

    await user.click(await screen.findByRole('button', { name: /import pine script/i }))
    expect(await screen.findByLabelText('Pine source')).toBeInTheDocument()
  })

  it('routes the empty setup state back to the Pine library', async () => {
    api.list.mockResolvedValue([])
    const user = userEvent.setup()
    render(<ImportedPinePage />)

    await user.click(await screen.findByRole('button', { name: /setup & verify/i }))
    expect(await screen.findByText('Choose a Pine version to prepare')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /open pine library/i }))

    expect(await screen.findByRole('button', { name: /import pine script/i })).toBeInTheDocument()
  })

  it('runs validation from the Static findings empty state', async () => {
    const user = userEvent.setup()
    const draft = { ...version, status: 'draft', validation: null }
    const passed = { ...version, validation: { ...validation, status: 'PASSED', warning_count: 0, findings: [] } }
    api.get.mockResolvedValueOnce({ strategy, versions: [draft] }).mockResolvedValue({ strategy, versions: [passed] })
    api.validate.mockResolvedValue({ version: passed, report: passed.validation, reused: false })
    render(<ImportedPinePage />)

    await openEditor(user)
    expect(await screen.findByText('Validate this exact version')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /run validation/i }))

    await waitFor(() => expect(api.validate).toHaveBeenCalledWith('s1', 'v1'))
    expect(await screen.findByText('No static findings')).toBeInTheDocument()
  })

  it('renders untrusted source as text, findings navigate, and no browser persistence or URL state is used', async () => {
    const user = userEvent.setup()
    const originalUrl = window.location.href
    render(<ImportedPinePage />)
    await openEditor(user)
    const editor = await screen.findByLabelText('Pine source') as HTMLTextAreaElement
    await waitFor(() => expect(editor.value).toContain('<script>alert(1)</script>'))
    expect(document.querySelector('.pine-editor-card script')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /confirm bars/i }))
    expect(editor.selectionStart).toBeGreaterThan(0)
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0); expect(window.location.href).toBe(originalUrl)
  })

  it('pastes a corrected version, validates it, submits it, and warns before unload', async () => {
    const user = userEvent.setup()
    const draft = { ...version, id: 'v2', status: 'draft', validation: null }
    const ready = { ...version, id: 'v2', status: 'ready_for_review' }
    api.get.mockResolvedValueOnce({ strategy, versions: [version] })
      .mockResolvedValueOnce({ strategy, versions: [draft] })
      .mockResolvedValue({ strategy, versions: [ready] })
    api.version.mockResolvedValue({ version: draft, reused: false })
    api.validate.mockResolvedValue({ version, report: validation, reused: false })
    api.submit.mockResolvedValue({ version: { ...version, status: 'submitted' } })
    render(<ImportedPinePage />)
    await openEditor(user)
    const editor = await screen.findByLabelText('Pine source')
    await waitFor(() => expect((editor as HTMLTextAreaElement).value).toBe(SOURCE))
    await user.type(editor, '\n// corrected')
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event); expect(event.defaultPrevented).toBe(true)
    await user.click(screen.getByRole('button', { name: /save as new version/i }))
    await waitFor(() => expect(api.version).toHaveBeenCalledWith('s1', expect.objectContaining({ source: expect.stringContaining('corrected') })))
    await user.click(screen.getByRole('button', { name: /^validate$/i }))
    await waitFor(() => expect(api.validate).toHaveBeenCalled())
    for (const label of [
      /I reviewed the converted strategy/i,
      /static validation does not guarantee/i,
      /backtests do not guarantee/i,
      /initially run in paper mode/i,
    ]) await user.click(screen.getByRole('checkbox', { name: label }))
    await user.click(screen.getByRole('button', { name: /accept and submit for review/i }))
    await waitFor(() => expect(api.submit).toHaveBeenCalledWith('s1', 'v2', expect.objectContaining({ prompt_version_id: 'v1', accepts_paper_only: true })))
  })

  it('accepts a UTF-8 .pine upload and rejects binary decoding', async () => {
    const user = userEvent.setup()
    render(<ImportedPinePage />)
    await openEditor(user)
    const editor = await screen.findByLabelText('Pine source')
    await waitFor(() => expect((editor as HTMLTextAreaElement).value).toBe(SOURCE))
    const input = document.querySelector('input[type=file]') as HTMLInputElement
    const good = new File([SOURCE], 'upload.pine', { type: 'text/plain' })
    fireEvent.change(input, { target: { files: [good] } })
    await waitFor(() => expect((screen.getByLabelText('Pine source') as HTMLTextAreaElement).value).toBe(SOURCE))
    const bad = new File([new Uint8Array([0xff, 0xfe])], 'bad.pine', { type: 'text/plain' })
    fireEvent.change(input, { target: { files: [bad] } })
    expect(await screen.findByRole('alert')).toHaveTextContent('valid UTF-8')
  })

  it('generates the manual package without starting AI conversion', async () => {
    const user = userEvent.setup(); render(<ImportedPinePage />)
    await openEditor(user)
    await waitFor(() => expect((screen.getByLabelText('Pine source') as HTMLTextAreaElement).value).toBe(SOURCE))
    await user.click(screen.getByRole('button', { name: /copy conversion package/i }))
    await waitFor(() => expect(api.conversionPackage).toHaveBeenCalledWith('s1', 'v1'))
    expect(api.convert).not.toHaveBeenCalled()
    expect(toastApi.add).toHaveBeenCalledWith(expect.objectContaining({ title: 'Package copy completed.', type: 'success' }))
  })

  it('shows only the safe package-assembly error', async () => {
    const user = userEvent.setup()
    api.conversionPackage.mockRejectedValueOnce(new Error('The NOVA conversion package could not be generated safely. Please retry or contact NOVA support.'))
    render(<ImportedPinePage />)
    await openEditor(user)
    await user.click(await screen.findByRole('button', { name: /copy conversion package/i }))
    await waitFor(() => expect(toastApi.add).toHaveBeenCalledWith(expect.objectContaining({
      title: expect.stringMatching(/could not be generated safely/i),
      type: 'error',
    })))
    expect(JSON.stringify(toastApi.add.mock.calls)).not.toContain('{{TRANSPORT}}')
    expect(JSON.stringify(toastApi.add.mock.calls)).not.toContain('{{OPTIONS}}')
    expect(JSON.stringify(toastApi.add.mock.calls)).not.toContain('{{SOURCE}}')
  })

  it('shows layman V3 package guidance and keeps the admin manifest separate', async () => {
    const user = userEvent.setup()
    api.conversionConfig.mockResolvedValue({ manual_package_enabled: true, ai_enabled: false, provider: null, model: null, prompt_version: 'v3.1', prompt_status: 'QUALIFICATION', transport_version: 'pine_transport_v2', contract_version: 1, daily_limit: 10 })
    render(<ImportedPinePage />)
    await openEditor(user)
    expect(await screen.findByText(/Prompt v3.1 · QUALIFICATION/i)).toBeInTheDocument()
    expect(screen.getByText(/Copy this package into ChatGPT or Claude/i)).toBeInTheDocument()
    expect(screen.getByText(/Copy only Artifact 1 back into NOVA/i)).toBeInTheDocument()
    expect(screen.getByText(/Artifact 2 is a simple status/i)).toBeInTheDocument()
    expect(screen.getByText(/Artifact 3 is for NOVA review/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/Conversion assumptions/i)).not.toBeInTheDocument()
  })

  it('requires unselected consent before sending the exact owner-bound version to Claude', async () => {
    const user = userEvent.setup()
    api.ownerClaudeConfig.mockResolvedValue({
      enabled: true, provider: 'anthropic_claude', model: 'claude-test',
      prompt_version: 'v4.0', transport_version: 'pine_transport_v2',
      admin_review_required: true, paper_verification_required: true,
      live_eligible: false,
    })
    api.ownerClaudeCreate.mockResolvedValue({ conversion: {
      id: 'c1', owner_user_id: 'u1', strategy_id: 's1', strategy_name: 'Private script',
      input_version_id: 'v1', candidate_version_id: 'v2', source_sha256: 'abc',
      candidate_sha256: 'def', strategy_layer_sha256: 'ghi', submitted_at: 'now',
      analysis_status: 'ANALYZED', conversion_status: 'READY_FOR_ADMIN_REVIEW',
      provider: 'anthropic_claude', model: 'claude-test', provider_mode: 'CLAUDE_API',
      validation_status: 'PASSED', review_status: 'PENDING', safe_error_code: null,
      analysis: { analyzer_version: '1', registry_version: '1', registry_sha256: 'a', source_sha256: 'abc', matched_capabilities: [], unsupported_capabilities: [], warnings: [], blockers: [], admin_review_points: [], effective_capability_level: 'L1', confidence: 'HIGH' },
      provenance: {}, validation, conversion_summary: 'Converted', warnings: [],
      unsupported_features: [], action_mapping: {},
    }, reused: false })
    render(<ImportedPinePage />)
    await openEditor(user)
    await waitFor(() => expect((screen.getByLabelText('Pine source') as HTMLTextAreaElement).value).toBe(SOURCE))
    const send = screen.getByRole('button', { name: /convert and send for admin review/i })
    expect(send).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: /private pine version to claude/i })); await user.click(send)
    await waitFor(() => expect(api.ownerClaudeCreate).toHaveBeenCalledWith('s1', 'v1', {
      requested_setup_type: 'USER_MANAGED_TRADINGVIEW',
      intended_symbol: 'NIFTY',
      intended_timeframe: '5',
    }))
    expect(toastApi.promise).toHaveBeenCalledWith(
      expect.any(Promise),
      expect.objectContaining({
        loading: expect.objectContaining({ title: 'Converting with Claude… this may take several minutes.' }),
        success: expect.objectContaining({ title: 'Conversion finished and was sent for admin review.' }),
      }),
    )
    expect(await screen.findByText(/Claude conversion for Private script/i)).toBeInTheDocument()
    expect(screen.getByText(/admin must now review/i)).toBeInTheDocument()
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0)
  })

  it('reports a resolved safe Claude failure as an error instead of success', () => {
    const conversion: Parameters<typeof claudeCompletionError>[0] = {
      conversion_status: 'AI_FAILED_RETRYABLE',
      safe_error_code: 'PROVIDER_TIMEOUT',
    }
    expect(claudeCompletionError(conversion)).toBe('Claude conversion stopped safely: provider timeout.')
  })

  it('shows distinct Premium and NOVA-managed setup paths without false readiness', async () => {
    const user = userEvent.setup()
    const approved = { ...version, status: 'approved' }
    api.get.mockResolvedValue({ strategy, versions: [approved] })
    api.source.mockResolvedValue({ source: SOURCE, filename: 'safe.pine', source_sha256: 'abc', approved: true })
    api.instances.mockResolvedValue([{ id: 'i1', label: 'Paper strategy', execution_mode: 'paper_live_data' }])
    api.link.mockResolvedValue({})
    api.createSetup.mockResolvedValue({ id: 'tv1', strategy_instance_id: 'i1', approved_version_id: 'v1', setup_type: 'NOVA_MANAGED_TRADINGVIEW', status: 'SETUP_PENDING', ready_for_paper: false, blocking_step: 'TradingView installation', who_acts_next: 'Admin', blocking_reason: null, user_reported_compiled_at: null, hold_verified_at: null, paper_entry_verified_at: null, paper_exit_verified_at: null, gates: {}, updated_at: null })
    render(<ImportedPinePage />)
    await openEditor(user)
    await user.click(await screen.findByRole('combobox', { name: 'Personal strategy instance' }))
    await user.click(screen.getByRole('option', { name: /Paper strategy/i }))
    expect(screen.getByText(/You manage this strategy in your TradingView account/i)).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: /I need NOVA-managed/i }))
    await user.click(screen.getByRole('button', { name: /save setup path/i }))
    await waitFor(() => expect(api.createSetup).toHaveBeenCalledWith('i1', 'NOVA_MANAGED_TRADINGVIEW'))
    expect(await screen.findByText(/Pending: TradingView installation/i)).toBeInTheDocument()
    expect(screen.queryByText('READY FOR PAPER USE')).not.toBeInTheDocument()
  })

  it('shows the admin rejection reason on the version the user submitted', async () => {
    const rejected = {
      ...version, status: 'rejected',
      review_history: [{ decision: 'rejected', note: 'Missing stop-loss handling.', previous_status: 'under_review', new_status: 'rejected', reviewed_at: '2026-07-27T10:00:00Z' }],
    }
    api.get.mockResolvedValue({ strategy, versions: [rejected] })
    const user = userEvent.setup()
    render(<ImportedPinePage />)
    await openEditor(user)
    expect(await screen.findByText(/Rejected by admin review/i)).toBeInTheDocument()
    expect(screen.getByText('Missing stop-loss handling.')).toBeInTheDocument()
  })

  it('withdraws a script after confirmation and refreshes the list', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.list.mockResolvedValueOnce([strategy]).mockResolvedValue([])
    render(<ImportedPinePage />)
    await openEditor(user)
    await user.click(await screen.findByRole('button', { name: /withdraw script/i }))
    await waitFor(() => expect(api.deleteStrategy).toHaveBeenCalledWith('s1'))
    expect(toastApi.add).toHaveBeenCalledWith(expect.objectContaining({ title: 'Withdraw script completed.', type: 'success' }))
  })

  it('does not withdraw when the confirmation is declined', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<ImportedPinePage />)
    await openEditor(user)
    await user.click(await screen.findByRole('button', { name: /withdraw script/i }))
    expect(api.deleteStrategy).not.toHaveBeenCalled()
  })

  it('shows conversion history filtered to the current script', async () => {
    api.conversionHistory.mockResolvedValue([
      { id: 'c1', strategy_id: 's1', input_version_id: 'v1', status: 'succeeded', provider: 'nova-ai', model: 'pine-model', prompt_version: 'v1', consent_at: 'now', candidate_version_id: 'v2', conversion_summary: null, assumptions: [], unsupported_features: [], warnings: [], action_mapping: {}, safe_error_code: null },
      { id: 'c2', strategy_id: 's-other', input_version_id: 'v9', status: 'provider_failed', provider: 'nova-ai', model: 'pine-model', prompt_version: 'v1', consent_at: 'now', candidate_version_id: null, conversion_summary: null, assumptions: [], unsupported_features: [], warnings: [], action_mapping: {}, safe_error_code: 'PROVIDER_ERROR' },
    ])
    const user = userEvent.setup()
    render(<ImportedPinePage />)
    await openEditor(user)
    expect(await screen.findByText('Conversion history')).toBeInTheDocument()
    expect(screen.getByText('nova-ai · pine-model')).toBeInTheDocument()
    expect(screen.queryByText(/provider_failed/i)).not.toBeInTheDocument()
  })

  it('lets an admin provision a one-time managed credential, masked until revealed', async () => {
    const user = userEvent.setup()
    api.managedSetups.mockResolvedValue([{
      id: 'tv1', user_id: 'user-1234-5678', status: 'ALERT_TEST_PENDING', blocking_step: 'HOLD connectivity test',
      approved_source_sha256: 'deadbeef', strategy_instance_id: 'i1', credential_status: 'missing_or_revoked',
      hold_verified_at: null, paper_entry_verified_at: null, paper_exit_verified_at: null,
      setup_type: 'NOVA_MANAGED_TRADINGVIEW', ready_for_paper: false,
    }])
    api.managedCredential.mockResolvedValue({ id: 'c1', strategy_instance_id: 'i1', token_prefix: 'nwk_mng123', created_at: null, last_used_at: null, revoked_at: null, token: MANAGED_TOKEN })
    render(<ImportedPinePage isAdmin />)
    await user.click(screen.getByRole('button', { name: /admin review queue/i }))
    await user.click(await screen.findByRole('button', { name: /managed setups/i }))
    await screen.findByText('Managed TradingView setup')
    await user.click(await screen.findByRole('button', { name: /generate managed credential/i }))
    await waitFor(() => expect(api.managedCredential).toHaveBeenCalledWith('tv1', false))
    expect(await screen.findByText('Shown only now')).toBeInTheDocument()
    expect(screen.queryByText(MANAGED_TOKEN)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^reveal$/i }))
    expect(screen.getByText(MANAGED_TOKEN)).toBeInTheDocument()
  })
})
