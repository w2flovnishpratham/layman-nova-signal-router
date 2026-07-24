import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  createPineStrategy: vi.fn(),
  validatePineVersion: vi.fn(),
  submitForReview: vi.fn(),
}))
vi.mock('./pineApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./pineApi')>(),
  ...apiMocks,
}))

import { AddStrategyPage } from './AddStrategyPage'

const created = { strategy: { id: 's-1', name: 'Mine' }, version: { id: 'v-1', status: 'draft' } }

const validation = (over: Record<string, unknown> = {}) => ({
  validation: {
    status: 'PASSED',
    validation_engine: 'nova-pine-static',
    validator_version: '1.0.0',
    contract_version: 'v3.1',
    error_count: 0,
    warning_count: 0,
    info_count: 0,
    eligible_for_review: true,
    findings: [],
    ...over,
  },
})

async function pasteAndValidate() {
  fireEvent.change(screen.getByLabelText('Strategy name'), { target: { value: 'Mine' } })
  fireEvent.change(screen.getByLabelText('Paste Pine code'), { target: { value: '//@version=5' } })
  fireEvent.click(screen.getByRole('button', { name: 'Validate' }))
}

afterEach(() => {
  cleanup()
  apiMocks.createPineStrategy.mockReset()
  apiMocks.validatePineVersion.mockReset()
  apiMocks.submitForReview.mockReset()
})

describe('AddStrategyPage', () => {
  it('never claims TradingView compiled the script', async () => {
    apiMocks.createPineStrategy.mockResolvedValue(created)
    apiMocks.validatePineVersion.mockResolvedValue(validation())
    render(<AddStrategyPage />)
    await pasteAndValidate()

    expect(await screen.findByText('Static validation passed')).toBeInTheDocument()
    expect(screen.getByText(/TradingView compilation pending/i)).toBeInTheDocument()
    expect(screen.queryByText(/compiled (successfully|on TradingView)/i)).toBeNull()
  })

  it('validates pasted source through the existing workflow', async () => {
    apiMocks.createPineStrategy.mockResolvedValue(created)
    apiMocks.validatePineVersion.mockResolvedValue(validation())
    render(<AddStrategyPage />)
    await pasteAndValidate()
    await waitFor(() => expect(apiMocks.validatePineVersion).toHaveBeenCalledWith('s-1', 'v-1'))
  })

  it('rejects an unsupported file extension', async () => {
    render(<AddStrategyPage />)
    const input = screen.getByLabelText(/upload a \.pine or \.txt file/i)
    const file = new File(['x'], 'strategy.js', { type: 'text/javascript' })
    fireEvent.change(input, { target: { files: [file] } })
    expect(await screen.findByRole('alert')).toHaveTextContent(/only \.pine and \.txt/i)
  })

  it('blocks submission when static validation failed', async () => {
    apiMocks.createPineStrategy.mockResolvedValue(created)
    apiMocks.validatePineVersion.mockResolvedValue(validation({
      status: 'FAILED',
      error_count: 1,
      eligible_for_review: false,
      findings: [{ code: 'LOOKAHEAD', severity: 'ERROR', title: 'lookahead_on is not allowed', explanation: 'Repainting.' }],
    }))
    render(<AddStrategyPage />)
    await pasteAndValidate()

    expect(await screen.findByText('Static validation failed')).toBeInTheDocument()
    expect(screen.getByText('lookahead_on is not allowed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /submit for admin review/i })).toBeNull()
  })

  it('submits for admin review and never starts an engine', async () => {
    apiMocks.createPineStrategy.mockResolvedValue(created)
    apiMocks.validatePineVersion.mockResolvedValue(validation())
    apiMocks.submitForReview.mockResolvedValue({ review: { id: 'r-1', status: 'PENDING' } })
    render(<AddStrategyPage />)
    await pasteAndValidate()

    fireEvent.click(await screen.findByRole('button', { name: /submit for admin review/i }))
    expect(await screen.findByText('Submitted for admin review')).toBeInTheDocument()
    expect(screen.getByText(/you still choose the strategy, complete setup/i)).toBeInTheDocument()
    // No activation affordance exists anywhere on this page.
    expect(screen.queryByRole('button', { name: /start engine|activate/i })).toBeNull()
  })
})
