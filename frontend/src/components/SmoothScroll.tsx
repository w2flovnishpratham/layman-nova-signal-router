import Lenis from 'lenis'
import 'lenis/dist/lenis.css'
import { useEffect, type ReactNode } from 'react'

export function SmoothScroll({ children }: { children: ReactNode }) {
  useEffect(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
    let lenis: Lenis | null = null

    const sync = () => {
      const reduced = reducedMotion.matches || document.documentElement.dataset.motion === 'reduced'
      if (reduced) {
        lenis?.destroy()
        lenis = null
        return
      }
      lenis ??= new Lenis({
        anchors: true,
        autoRaf: true,
        smoothWheel: true,
        stopInertiaOnNavigate: true,
        prevent: (node) => Boolean(node.closest('[data-lenis-prevent], [role="dialog"], .panel-scroll, .terminal-table-wrap')),
      })
    }

    const observer = new MutationObserver(sync)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-motion'] })
    reducedMotion.addEventListener('change', sync)
    sync()

    return () => {
      reducedMotion.removeEventListener('change', sync)
      observer.disconnect()
      lenis?.destroy()
    }
  }, [])

  return children
}
