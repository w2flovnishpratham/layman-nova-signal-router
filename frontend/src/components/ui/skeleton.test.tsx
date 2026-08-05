import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Skeleton, SkeletonText } from './skeleton'

describe('Skeleton', () => {
  it('is hidden from assistive tech so placeholders are not announced as content', () => {
    // Pages label their own loading region with role="status" + aria-label;
    // the individual bars inside it must not add noise on top of that.
    const { container } = render(<Skeleton className="h-4 w-24" />)
    const skeleton = container.querySelector('[data-slot="skeleton"]')
    expect(skeleton).toHaveAttribute('aria-hidden', 'true')
  })

  it('carries the shared fill class rather than a per-surface colour', () => {
    // The fill has to stay visible on the page background, inside cards and
    // inside nested panels; a flat bg-* token can only be right against one of
    // those, so every placeholder goes through .nova-skeleton.
    const { container } = render(<Skeleton />)
    expect(container.querySelector('[data-slot="skeleton"]')).toHaveClass('nova-skeleton')
  })

  it('keeps caller sizing, so a placeholder matches the content it stands in for', () => {
    const { container } = render(<Skeleton className="h-6 w-32" />)
    const skeleton = container.querySelector('[data-slot="skeleton"]')
    expect(skeleton).toHaveClass('h-6', 'w-32')
  })

  it('renders one bar per line and shortens the last, the way text wraps', () => {
    const { container } = render(<SkeletonText lines={3} />)
    const bars = container.querySelectorAll('[data-slot="skeleton"]')
    expect(bars).toHaveLength(3)
    expect(bars[bars.length - 1]).toHaveClass('w-3/5')
  })
})
