import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ImportedPinePage } from './ImportedPinePage'

const api = vi.hoisted(() => ({
  list: vi.fn(), get: vi.fn(), create: vi.fn(), version: vi.fn(), validate: vi.fn(), submit: vi.fn(),
  source: vi.fn(), link: vi.fn(), instances: vi.fn(), reviews: vi.fn(), review: vi.fn(), decide: vi.fn(),
}))
vi.mock('../api', () => ({
  listPineStrategies: api.list, getPineStrategy: api.get, createPineStrategy: api.create,
  createPineVersion: api.version, validatePineVersion: api.validate, submitPineVersion: api.submit,
  getPineSource: api.source, linkPineVersion: api.link, listStrategyInstances: api.instances,
  listPineReviews: api.reviews, getPineReview: api.review, decidePineReview: api.decide,
}))

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
    await user.click(screen.getByRole('button', { name: /submit for review/i }))
    await waitFor(() => expect(api.submit).toHaveBeenCalled())
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
})
