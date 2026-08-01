import { useGSAP } from '@gsap/react'
import { gsap } from 'gsap'
import { BarChart3, Sliders, Wallet } from 'lucide-react'
import { useRef } from 'react'
import { Button } from '@/components/ui/button'
import { useAppReducedMotion } from './MotionPrimitives'

gsap.registerPlugin(useGSAP)

export type TerminalMobileSection = 'market' | 'risk' | 'account'

const ITEMS = [
  { key: 'market', label: 'Market', Icon: Sliders },
  { key: 'risk', label: 'Bias / Risk', Icon: BarChart3 },
  { key: 'account', label: 'Account', Icon: Wallet },
] as const

export function TerminalMobileBar({
  active,
  onSelect,
}: {
  active: TerminalMobileSection | null
  onSelect: (section: TerminalMobileSection) => void
}) {
  const root = useRef<HTMLDivElement>(null)
  const reduceMotion = useAppReducedMotion()

  useGSAP(() => {
    const duration = reduceMotion ? 0 : 0.38
    for (const item of gsap.utils.toArray<HTMLElement>('[data-terminal-mobile-item]', root.current)) {
      const selected = item.dataset.active === 'true'
      gsap.to(item, {
        flexGrow: active ? (selected ? 1.55 : 0.725) : 1,
        duration,
        ease: 'power3.inOut',
        overwrite: 'auto',
      })
      gsap.to(item.querySelector('.terminal-mobile-pill'), {
        autoAlpha: selected ? 1 : 0,
        scaleX: selected ? 1 : 0.42,
        duration,
        ease: 'power3.inOut',
        overwrite: 'auto',
      })
      gsap.to(item.querySelector('.terminal-mobile-icon'), {
        x: selected ? -30 : 0,
        duration,
        ease: 'power3.inOut',
        overwrite: 'auto',
      })
      gsap.to(item.querySelector('.terminal-mobile-label'), {
        autoAlpha: selected ? 1 : 0,
        x: selected ? 6 : 16,
        duration: reduceMotion ? 0 : 0.26,
        ease: 'power2.out',
        overwrite: 'auto',
      })
    }
  }, { scope: root, dependencies: [active, reduceMotion], revertOnUpdate: true })

  return (
    <div className="terminal-mobile-bar" ref={root} aria-label="Terminal panels">
      {ITEMS.map(({ key, label, Icon }) => {
        const selected = active === key
        return (
          <Button
            variant="unstyled"
            type="button"
            className="terminal-mobile-action"
            data-terminal-mobile-item
            data-active={selected}
            aria-pressed={selected}
            aria-label={label}
            key={key}
            onClick={() => onSelect(key)}
          >
            <span className="terminal-mobile-pill" aria-hidden="true" />
            <span className="terminal-mobile-icon" aria-hidden="true"><Icon size={19} /></span>
            <span className="terminal-mobile-label" aria-hidden={!selected}>{label}</span>
          </Button>
        )
      })}
    </div>
  )
}
