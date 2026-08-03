import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { TerminalMobileBar } from './TerminalMobileBar'

const gsapTo = vi.hoisted(() => vi.fn())

vi.mock('@gsap/react', async () => {
  const { useLayoutEffect } = await import('react')
  return { useGSAP: (callback: () => void) => useLayoutEffect(callback, []) }
})
vi.mock('gsap', () => ({
  gsap: {
    registerPlugin: vi.fn(),
    to: gsapTo,
    utils: { toArray: vi.fn((selector: string, root: HTMLElement) => Array.from(root.querySelectorAll(selector))) },
  },
}))

describe('TerminalMobileBar', () => {
  it('exposes only the selected panel as active and keeps every icon keyboard operable', async () => {
    const onSelect = vi.fn()
    render(<TerminalMobileBar active="risk" onSelect={onSelect} />)
    expect(screen.getByRole('button', { name: 'Bias / Risk' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Market' })).toHaveAttribute('aria-pressed', 'false')
    expect(gsapTo).toHaveBeenCalledWith(expect.any(HTMLElement), expect.objectContaining({ flexGrow: 1.55 }))
    expect(gsapTo.mock.calls.filter(([, values]) => values.flexGrow === 0.725)).toHaveLength(2)
    await userEvent.click(screen.getByRole('button', { name: 'Account' }))
    expect(onSelect).toHaveBeenCalledWith('account')
  })
})
