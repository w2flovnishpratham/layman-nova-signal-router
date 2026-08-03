import { PlanetaryEcosystem } from './PlanetaryEcosystem'
import './landing.css'

export function LandingPage({ onEnterApp }: { onEnterApp?: () => void }) {
  return (
    <div className="nova-landing w-full min-h-screen bg-[#05070C] text-[#F5F8FF] overflow-hidden relative flex items-center justify-center">
      <PlanetaryEcosystem onEnterApp={onEnterApp} />
    </div>
  )
}
