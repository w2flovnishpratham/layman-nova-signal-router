import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useConversationScroll } from './useConversationScroll'

function Harness({ itemCount, reducedMotion }: { itemCount: number; reducedMotion?: boolean }) {
  const { ref, showJump, jumpToLatest } = useConversationScroll({ itemCount, reducedMotion })
  return (
    <div>
      <div ref={ref} data-testid="canvas" />
      {showJump ? <button onClick={jumpToLatest}>Jump to latest</button> : null}
    </div>
  )
}

function setMetrics(el: HTMLElement, { scrollHeight, clientHeight, scrollTop }: { scrollHeight: number; clientHeight: number; scrollTop: number }) {
  Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true })
  Object.defineProperty(el, 'clientHeight', { value: clientHeight, configurable: true })
  el.scrollTop = scrollTop
}

beforeEach(() => {
  // jsdom implements neither scrollTo nor layout metrics.
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('useConversationScroll', () => {
  it('auto-scrolls to the latest when the user is near the bottom', () => {
    const { rerender } = render(<Harness itemCount={1} />)
    const canvas = screen.getByTestId('canvas')
    setMetrics(canvas, { scrollHeight: 1000, clientHeight: 500, scrollTop: 500 }) // at bottom
    ;(Element.prototype.scrollTo as ReturnType<typeof vi.fn>).mockClear()
    rerender(<Harness itemCount={2} />)
    expect(canvas.scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: 'smooth' })
    expect(screen.queryByText('Jump to latest')).toBeNull()
  })

  it('preserves position and shows Jump to latest when the user has scrolled up', () => {
    const { rerender } = render(<Harness itemCount={1} />)
    const canvas = screen.getByTestId('canvas')
    setMetrics(canvas, { scrollHeight: 1000, clientHeight: 500, scrollTop: 0 }) // scrolled up
    fireEvent.scroll(canvas)
    ;(Element.prototype.scrollTo as ReturnType<typeof vi.fn>).mockClear()
    rerender(<Harness itemCount={2} />)
    expect(canvas.scrollTo).not.toHaveBeenCalled() // not yanked down
    expect(screen.getByText('Jump to latest')).toBeInTheDocument()
  })

  it('Jump to latest scrolls once and hides itself', () => {
    const { rerender } = render(<Harness itemCount={1} />)
    const canvas = screen.getByTestId('canvas')
    setMetrics(canvas, { scrollHeight: 1000, clientHeight: 500, scrollTop: 0 })
    fireEvent.scroll(canvas)
    rerender(<Harness itemCount={2} />)
    ;(Element.prototype.scrollTo as ReturnType<typeof vi.fn>).mockClear()
    fireEvent.click(screen.getByText('Jump to latest'))
    expect(canvas.scrollTo).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Jump to latest')).toBeNull()
  })

  it('uses immediate scrolling for reduced-motion users', () => {
    const { rerender } = render(<Harness itemCount={1} reducedMotion />)
    const canvas = screen.getByTestId('canvas')
    setMetrics(canvas, { scrollHeight: 1000, clientHeight: 500, scrollTop: 500 })
    ;(Element.prototype.scrollTo as ReturnType<typeof vi.fn>).mockClear()
    rerender(<Harness itemCount={2} reducedMotion />)
    expect(canvas.scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: 'auto' })
  })

  it('removes its scroll listener on unmount', () => {
    const { unmount } = render(<Harness itemCount={1} />)
    const canvas = screen.getByTestId('canvas')
    const removeSpy = vi.spyOn(canvas, 'removeEventListener')
    unmount()
    expect(removeSpy).toHaveBeenCalledWith('scroll', expect.any(Function))
  })
})
