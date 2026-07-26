import { PlanetaryEcosystem } from './PlanetaryEcosystem'
import './landing.css'

/** Stripped-down main page: just the orbital system animation, centered.
    Clicking the core (or any orbit module) hands off to onEnterApp, which the
    router points at "/app/trading" — App.tsx shows the login screen first for
    an unauthenticated visitor and lands on trading right after. */
export function MinimalHome({ onEnterApp }: { onEnterApp?: () => void }) {
  return (
    <div className="nova-landing w-full min-h-screen bg-[#05070C] text-[#F5F8FF] flex flex-col items-center justify-center px-6 py-16">
      <span className="font-extrabold text-xl tracking-tighter text-white uppercase font-montreal flex items-center gap-2 mb-12">
        <span className="w-3 h-3 rounded-full bg-[#2F6BED] shadow-[0_0_12px_#2F6BED]" />
        NOVA SIGNAL ROUTE
      </span>
      <PlanetaryEcosystem onEnterApp={onEnterApp} />
    </div>
  )
}
