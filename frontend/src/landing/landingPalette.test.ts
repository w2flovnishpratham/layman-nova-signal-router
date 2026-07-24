import { describe, expect, it } from 'vitest'
import landingCss from './landing.css?raw'
import landingSource from './LandingPage.tsx?raw'
import planetarySource from './PlanetaryEcosystem.tsx?raw'

// The landing page is styled with scoped Tailwind arbitrary values rather than
// CSS variables, so the palette is asserted against the source itself.
const sources = [
  { name: 'LandingPage.tsx', text: landingSource },
  { name: 'PlanetaryEcosystem.tsx', text: planetarySource },
]

const ELECTRIC_BLUE = '#2F6BED'
const PURPLE = '#9B5CFF'
const LIME = /#c0f53d|#80af1b|#7fa52b|#d4ff32/i

describe('landing palette', () => {
  it('no longer uses lime anywhere', () => {
    for (const { name, text } of sources) {
      expect(LIME.test(text), `${name} still contains a lime accent`).toBe(false)
    }
  })

  it('uses electric blue as the primary accent', () => {
    const combined = sources.map((s) => s.text).join('\n')
    expect(combined).toContain(ELECTRIC_BLUE)
    // It replaced the accent wholesale, not just in one spot.
    expect(combined.split(ELECTRIC_BLUE).length - 1).toBeGreaterThan(20)
  })

  it('reserves purple for the editorial NOVA voice', () => {
    const landing = sources[0].text
    expect(landing).toContain(PURPLE)
    // Every purple use is an italic emphasis, never a button or a border.
    for (const match of landing.matchAll(/class(?:Name)?="([^"]*#9B5CFF[^"]*)"/g)) {
      expect(match[1]).toMatch(/italic/)
    }
  })

  it('never recolours Buy or success semantics', () => {
    // Green is reserved for Buy/success in the app; the landing page makes no
    // such claim, so it must not paint a market-success signal blue either.
    const combined = sources.map((s) => s.text).join('\n')
    expect(/text-\[#2F6BED\][^"]*"\s*>\s*(BUY|Buy)\b/.test(combined)).toBe(false)
  })

  it('keeps the landing styles scoped rather than importing app CSS', () => {
    // Every rule is namespaced under .nova-landing (or a keyframes/layer block).
    for (const line of landingCss.split('\n')) {
      if (line.trim().endsWith('{') && !line.includes('@') && !line.includes('%')) {
        expect(line).toMatch(/\.nova-landing|@layer|from|to/)
      }
    }
    expect(landingCss).not.toContain('index.css')
    expect(landingCss).not.toContain('.nova-app')
  })

  it('keeps the CTA that enters the authenticated app', () => {
    const landing = sources[0].text
    // The CTA is a callback the router turns into /app navigation, not a raw href.
    expect(landing).toContain('onEnterApp')
    expect(landing).toContain('Launch Trading Platform')
  })
})
