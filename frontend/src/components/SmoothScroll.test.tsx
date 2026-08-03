import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SmoothScroll } from './SmoothScroll'

const lenis = vi.hoisted(() => ({ create: vi.fn(), destroy: vi.fn() }))
vi.mock('lenis', () => ({ default: function MockLenis() { lenis.create(); return { destroy: lenis.destroy } } }))

beforeEach(() => {
  lenis.create.mockClear()
  lenis.destroy.mockClear()
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
  })
})

afterEach(() => cleanup())

describe('SmoothScroll', () => {
  it('starts Lenis and destroys it with the app shell', () => {
    const view = render(<SmoothScroll><main>Content</main></SmoothScroll>)
    expect(lenis.create).toHaveBeenCalledOnce()
    view.unmount()
    expect(lenis.destroy).toHaveBeenCalledOnce()
  })
})
