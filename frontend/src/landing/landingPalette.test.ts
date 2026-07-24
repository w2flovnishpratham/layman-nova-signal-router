import { describe, expect, it } from 'vitest'
import landingCss from './landing.css?raw'

// Globbed, not listed by hand: an earlier version of this test named two files
// explicitly and missed src/landing/components/, where lime survived the
// conversion until a browser smoke test caught it.
const modules = import.meta.glob('./**/*.tsx', { query: '?raw', import: 'default', eager: true })

// The landing page is styled with scoped Tailwind arbitrary values rather than
// CSS variables, so the palette is asserted against the source itself.
const sources = Object.entries(modules)
  .filter(([name]) => !name.endsWith('.test.tsx'))
  .map(([name, text]) => ({ name, text: text as string }))

const combined = sources.map((s) => s.text).join('\n')

const ELECTRIC_BLUE = '#2F6BED'
const PURPLE = '#9B5CFF'
// Both notations: the hex pass alone missed rgba(192,245,61,...) twice.
const LIME_HEX = /#(c0f53d|80af1b|7fa52b|d4ff32|a6e025|b5f230|8cc217|d4ff54)/i
const LIME_RGB = /rgba?\(\s*(192\s*,\s*245\s*,\s*61|177\s*,\s*228\s*,\s*57|160\s*,\s*230\s*,\s*40|101\s*,\s*205\s*,\s*135)/i

describe('landing palette', () => {
  it('covers every landing source file', () => {
    expect(sources.length).toBeGreaterThanOrEqual(4)
    expect(sources.map((s) => s.name).join(' ')).toContain('components/')
  })

  it('no longer uses lime in hex form', () => {
    for (const { name, text } of sources) {
      expect(LIME_HEX.test(text), `${name} still contains a lime hex`).toBe(false)
    }
  })

  it('no longer uses lime in rgba form', () => {
    // Tailwind arbitrary values carry colours as rgba() too, and those are what
    // the gradients used; a hex-only check passed while lime was still rendering.
    for (const { name, text } of sources) {
      expect(LIME_RGB.test(text), `${name} still contains a lime rgba()`).toBe(false)
    }
  })

  it('uses electric blue as the primary accent', () => {
    expect(combined).toContain(ELECTRIC_BLUE)
    // It replaced the accent wholesale, not just in one spot.
    expect(combined.split(ELECTRIC_BLUE).length - 1).toBeGreaterThan(20)
  })

  it('reserves purple for the editorial NOVA voice', () => {
    expect(combined).toContain(PURPLE)
    // Every purple use is an italic emphasis, never a button or a border.
    for (const match of combined.matchAll(/class(?:Name)?="([^"]*#9B5CFF[^"]*)"/g)) {
      expect(match[1]).toMatch(/italic/)
    }
  })

  it('never recolours Buy or success semantics', () => {
    // Green is reserved for Buy/success in the app; the landing page makes no
    // such claim, so it must not paint a market-success signal blue either.
    expect(/text-\[#2F6BED\][^"]*"\s*>\s*(BUY|Buy)\b/.test(combined)).toBe(false)
  })

  it('keeps the landing styles scoped rather than importing app CSS', () => {
    for (const line of landingCss.split('\n')) {
      if (line.trim().endsWith('{') && !line.includes('@') && !line.includes('%')) {
        expect(line).toMatch(/\.nova-landing|@layer|from|to/)
      }
    }
    expect(landingCss).not.toContain('index.css')
    expect(landingCss).not.toContain('.nova-app')
  })

  it('keeps the CTA that enters the authenticated app', () => {
    // The CTA is a callback the router turns into /app navigation, not a raw href.
    expect(combined).toContain('onEnterApp')
    expect(combined).toContain('Launch Trading Platform')
  })
})
