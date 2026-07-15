import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ImportedPinePage } from './ImportedPinePage'

const api = vi.hoisted(() => ({
  list: vi.fn(), get: vi.fn(), create: vi.fn(), version: vi.fn(), validate: vi.fn(), submit: vi.fn(),
  source: vi.fn(), link: vi.fn(), instances: vi.fn(), reviews: vi.fn(), review: vi.fn(), decide: vi.fn(),
  conversionConfig: vi.fn(), conversionPackage: vi.fn(), convert: vi.fn(), conversion: vi.fn(), accept: vi.fn(), reject: vi.fn(), retry: vi.fn(),
  createSetup: vi.fn(), getSetup: vi.fn(),
  managedSetups: vi.fn(), recordInstallation: vi.fn(), managedCredential: vi.fn(),
}))
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
  api.conversionConfig.mockResolvedValue({ manual_package_enabled: true, ai_enabled: false, provider: null, model: null, prompt_version: 'v1', contract_version: 1, daily_limit: 10 })
  api.conversionPackage.mockResolvedValue({ package: `PRIVATE WARNING\n${SOURCE}`, filename: 'conversion.txt', package_sha256: 'pkg' })
  api.getSetup.mockRejectedValue(new Error('not configured'))
  api.managedSetups.mockResolvedValue([])
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } })
})
afterEach(() => cleanup())

describe('ImportedPinePage', () => {
  it('renders untrusted source as text, findings navigate, and no browser persistence or URL state is used', async () => {
    const originalUrl = window.location.href
    render(<ImportedPinePage />)
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
    render(<ImportedPinePage />); const editor = await screen.findByLabelText('Pine source')
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
    await waitFor(() => expect((screen.getByLabelText('Pine source') as HTMLTextAreaElement).value).toBe(SOURCE))
    await user.click(screen.getByRole('button', { name: /copy conversion package/i }))
    await waitFor(() => expect(api.conversionPackage).toHaveBeenCalledWith('s1', 'v1'))
    expect(api.convert).not.toHaveBeenCalled()
    expect(await screen.findByRole('status')).toHaveTextContent('Package copy completed')
  })

  it('requires unselected consent before sending the exact version', async () => {
    const user = userEvent.setup()
    api.conversionConfig.mockResolvedValue({ manual_package_enabled: true, ai_enabled: true, provider: 'configured', model: 'pine-model', prompt_version: 'v1', contract_version: 1, daily_limit: 10 })
    api.convert.mockResolvedValue({ conversion: { id: 'c1', strategy_id: 's1', input_version_id: 'v1', status: 'queued', provider: 'configured', model: 'pine-model', prompt_version: 'v1', consent_at: 'now', candidate_version_id: null, conversion_summary: null, assumptions: [], unsupported_features: [], warnings: [], action_mapping: {}, safe_error_code: null }, reused: false })
    render(<ImportedPinePage />); await waitFor(() => expect((screen.getByLabelText('Pine source') as HTMLTextAreaElement).value).toBe(SOURCE))
    const send = screen.getByRole('button', { name: /send source for conversion/i })
    expect(send).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: /i consent/i })); await user.click(send)
    await waitFor(() => expect(api.convert).toHaveBeenCalledWith('s1', 'v1'))
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0)
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
    await user.selectOptions(await screen.findByLabelText('Personal strategy instance'), 'i1')
    expect(screen.getByText(/You manage this strategy in your TradingView account/i)).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: /I need NOVA-managed/i }))
    await user.click(screen.getByRole('button', { name: /save setup path/i }))
    await waitFor(() => expect(api.createSetup).toHaveBeenCalledWith('i1', 'NOVA_MANAGED_TRADINGVIEW'))
    expect(await screen.findByText(/Pending: TradingView installation/i)).toBeInTheDocument()
    expect(screen.queryByText('READY FOR PAPER USE')).not.toBeInTheDocument()
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
    await screen.findByText('Managed TradingView setup')
    await user.click(await screen.findByRole('button', { name: /generate managed credential/i }))
    await waitFor(() => expect(api.managedCredential).toHaveBeenCalledWith('tv1', false))
    expect(await screen.findByText('Shown only now')).toBeInTheDocument()
    expect(screen.queryByText(MANAGED_TOKEN)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^reveal$/i }))
    expect(screen.getByText(MANAGED_TOKEN)).toBeInTheDocument()
  })
})
