import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { PineCodeEditor } from './PineCodeEditor'

function EditorHarness() {
  const [value, setValue] = useState('if close > 10\n  strategy.entry("Long", strategy.long)')
  return <PineCodeEditor ariaLabel="Pine source" filename="sample.pine" value={value} onChange={setValue} />
}

describe('PineCodeEditor', () => {
  it('colors Pine tokens and inserts spaces when Tab is pressed', () => {
    const { container } = render(<EditorHarness />)
    const editor = screen.getByLabelText('Pine source') as HTMLTextAreaElement

    expect(container.querySelector('.pine-token-keyword')).toHaveTextContent('if')
    expect(container.querySelector('.pine-token-builtin')).toHaveTextContent('strategy.entry')

    editor.setSelectionRange(0, 0)
    fireEvent.keyDown(editor, { key: 'Tab' })
    expect(editor.value).toMatch(/^  if close/)
  })
})
