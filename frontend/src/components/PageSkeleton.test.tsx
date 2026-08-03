import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PageSkeleton } from './PageSkeleton'

describe('PageSkeleton', () => {
  it('keeps loading pages structurally filled and accessible', () => {
    const { container } = render(<PageSkeleton label="Loading signals" variant="table" />)
    expect(screen.getByRole('status', { name: 'Loading signals' })).toBeInTheDocument()
    // 3 summary cards + 6 rows — table pages go straight from summary to rows,
    // with no primary/side block since none of them render one.
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(9)
  })
})
