import { forwardRef, useMemo, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { Braces, FileCode2 } from 'lucide-react'
import { Textarea } from '@/components/ui/textarea'

type PineCodeEditorProps = {
  value: string
  onChange: (value: string) => void
  filename: string
  ariaLabel: string
  minHeight?: number
}

const TOKEN_PATTERN = /(\/\/[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b(?:ta|math|str|array|matrix|strategy|request|input)\.[A-Za-z_]\w*|\b(?:strategy|indicator|library|if|else|for|while|switch|var|const|true|false|na|and|or|not)\b|\b\d+(?:\.\d+)?\b)/g
const TOKEN_CHECK_PATTERN = /^(?:\/\/[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:ta|math|str|array|matrix|strategy|request|input)\.[A-Za-z_]\w*|(?:strategy|indicator|library|if|else|for|while|switch|var|const|true|false|na|and|or|not)|\d+(?:\.\d+)?)$/

function tokenClass(token: string) {
  if (token.startsWith('//')) return 'comment'
  if (token.startsWith('"') || token.startsWith("'")) return 'string'
  if (/^\d/.test(token)) return 'number'
  if (token.includes('.')) return 'builtin'
  return 'keyword'
}

function highlightedSource(source: string) {
  return source.split(TOKEN_PATTERN).map((token, index) => {
    if (!token) return null
    return TOKEN_CHECK_PATTERN.test(token)
      ? <span className={`pine-token-${tokenClass(token)}`} key={index}>{token}</span>
      : token
  })
}

export const PineCodeEditor = forwardRef<HTMLTextAreaElement, PineCodeEditorProps>(function PineCodeEditor({
  value,
  onChange,
  filename,
  ariaLabel,
  minHeight = 430,
}, ref) {
  const [scroll, setScroll] = useState({ top: 0, left: 0 })
  const lines = useMemo(() => value.split('\n'), [value])
  const highlighted = useMemo(() => highlightedSource(value), [value])

  function indent(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Tab') return
    event.preventDefault()
    const input = event.currentTarget
    const next = `${value.slice(0, input.selectionStart)}  ${value.slice(input.selectionEnd)}`
    const cursor = input.selectionStart + 2
    onChange(next)
    requestAnimationFrame(() => input.setSelectionRange(cursor, cursor))
  }

  return (
    <div className="pine-ide" style={{ minHeight }}>
      <div className="pine-ide-head">
        <div className="pine-window-dots" aria-hidden="true"><i /><i /><i /></div>
        <span className="pine-ide-file"><FileCode2 size={13} /> {filename || 'strategy.pine'}</span>
        <span className="pine-ide-language"><Braces size={12} /> Pine Script</span>
      </div>
      <div className="pine-ide-body">
        <div className="pine-line-numbers" aria-hidden="true" style={{ transform: `translateY(${-scroll.top}px)` }}>
          {lines.map((_, index) => <span key={index}>{index + 1}</span>)}
        </div>
        <pre className="pine-highlight" aria-hidden="true" style={{ transform: `translate(${-scroll.left}px, ${-scroll.top}px)` }}>
          <code>{highlighted}{'\n'}</code>
        </pre>
        <Textarea
          variant="unstyled"
          ref={ref}
          className="pine-source"
          aria-label={ariaLabel}
          value={value}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          onKeyDown={indent}
          onScroll={(event) => setScroll({ top: event.currentTarget.scrollTop, left: event.currentTarget.scrollLeft })}
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
      <div className="pine-ide-status">
        <span>{lines.length} lines</span>
        <span>{value.length.toLocaleString()} characters</span>
        <span>UTF-8</span>
      </div>
    </div>
  )
})
