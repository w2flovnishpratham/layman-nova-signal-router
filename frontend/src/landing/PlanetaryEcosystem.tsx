/* eslint-disable @typescript-eslint/no-explicit-any -- preserved landing source:
   gsap timeline handles and DOM element casts in this orbital animation are typed
   as any; precise typing of the imported animation code is out of scope. */
import { useEffect, useRef, useState } from 'react'
import gsap from 'gsap'

interface ModuleItem {
  id: string
  label: string
  subDesc: string
  fullDesc: string
}

const MODULES: ModuleItem[] = [
  {
    id: 'Signals',
    label: 'Signals',
    subDesc: 'Receives strategy and webhook events',
    fullDesc: 'TradingView webhook integration and sub-millisecond signal parsing.',
  },
  {
    id: 'Risk',
    label: 'Risk Engine',
    subDesc: 'Applies position and exposure controls',
    fullDesc: 'Real-time position sizing, auto stop-loss/take-profit, and daily drawdown control.',
  },
  {
    id: 'Execution',
    label: 'Execution Router',
    subDesc: 'Routes validated orders to the correct path',
    fullDesc: 'Ultra-low latency Dhan API routing with slippage protection and order queuing.',
  },
  {
    id: 'Analytics',
    label: 'Analytics',
    subDesc: 'Tracks performance, latency and outcomes',
    fullDesc: 'Interactive analytics timelines, execution heatmaps, and live performance metrics.',
  },
  {
    id: 'Paper',
    label: 'Paper Trading',
    subDesc: 'Tests strategies without broker execution',
    fullDesc: 'Sandbox simulation with real-time mark-to-market and zero capital risk.',
  },
  {
    id: 'Live',
    label: 'Live Engine',
    subDesc: 'Executes approved signals through the broker',
    fullDesc: 'Secure live trading execution with instant kill switch and automated failovers.',
  },
]

export function PlanetaryEcosystem({ onEnterApp }: { onEnterApp?: () => void }) {
  const [, setHoveredIndex] = useState<number | null>(null)

  const systemRef = useRef<HTMLDivElement>(null)
  const orbitRingRef = useRef<HTMLDivElement>(null)
  const signalParticleOrbitRef = useRef<HTMLDivElement>(null)
  const signalParticleRef = useRef<HTMLDivElement>(null)
  const coreMagneticRef = useRef<HTMLDivElement>(null)
  const corePulseRef = useRef<HTMLDivElement>(null)
  const innerRingRef = useRef<HTMLDivElement>(null)

  const cardHoverRefs = useRef<(HTMLDivElement | null)[]>([])
  const cardContentRefs = useRef<(HTMLButtonElement | null)[]>([])

  const lastActiveIndexRef = useRef<number>(-1)

  useEffect(() => {
    const isMobile = window.innerWidth < 768
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

    if (prefersReducedMotion) {
      return
    }

    const ctx = gsap.context(() => {
      const orbitDuration = isMobile ? 32 : 26
      const particleDuration = 9

      // --------------------------------------------------
      // SINGLE MASTER ORBIT TIMELINE (SYNCHRONIZED)
      // --------------------------------------------------
      const masterTl = gsap.timeline({ repeat: -1 })

      // 1. Rotate orbit ring (+360 clockwise)
      masterTl.to(
        orbitRingRef.current,
        {
          rotation: 360,
          duration: orbitDuration,
          ease: 'none',
        },
        0
      )

      // 2. Counter-rotate each card wrapper from -baseAngle to -(baseAngle + 360)
      // This explicitly cancels both the orbit rotation AND the fixed base angle!
      const counterElements = gsap.utils.toArray<HTMLElement>('.orbit-counter-rotation')
      counterElements.forEach((el, index) => {
        const baseAngle = index * 60
        masterTl.fromTo(
          el,
          { rotation: -baseAngle },
          {
            rotation: -(baseAngle + 360),
            duration: orbitDuration,
            ease: 'none',
          },
          0
        )
      })

      // Store master timeline reference on DOM node for hover synchronization
      if (systemRef.current) {
        ; (systemRef.current as any)._masterTl = masterTl
      }

      // 3. Signal Particle rotation (clockwise +360 in 9s)
      const particleTween = gsap.to(signalParticleOrbitRef.current, {
        rotation: 360,
        duration: particleDuration,
        repeat: -1,
        ease: 'none',
      })

      if (systemRef.current) {
        ; (systemRef.current as any)._particleTween = particleTween
      }

      // 4. Inner Technical Ring counter-rotation (-360 in 40s)
      if (innerRingRef.current) {
        gsap.to(innerRingRef.current, {
          rotation: -360,
          duration: 40,
          repeat: -1,
          ease: 'none',
        })
      }

      // 5. Central Core subtle breathing pulse
      let breathingTween: gsap.core.Tween | null = null
      if (corePulseRef.current) {
        breathingTween = gsap.to(corePulseRef.current, {
          scale: 1.015,
          duration: 3.5,
          repeat: -1,
          yoyo: true,
          ease: 'sine.inOut',
        })
          ; (corePulseRef.current as any)._breathingTween = breathingTween
      }

      // Helper for shortest circular distance
      const angularDistance = (a: number, b: number) => {
        const diff = Math.abs(a - b) % 360
        return Math.min(diff, 360 - diff)
      }

      // Helper to activate card styling on particle crossing
      const triggerCardActivation = (activeIdx: number) => {
        MODULES.forEach((_, idx) => {
          const isActive = idx === activeIdx
          const cardHoverEl = cardHoverRefs.current[idx]
          const cardContentEl = cardContentRefs.current[idx]

          if (cardHoverEl) {
            gsap.to(cardHoverEl, {
              scale: isActive ? 1.08 : 1,
              z: isActive ? 20 : 0,
              opacity: isActive ? 1 : 0.75,
              duration: 0.35,
              ease: 'power2.out',
              overwrite: 'auto',
            })
          }

          if (cardContentEl) {
            if (isActive) {
              cardContentEl.style.borderColor = 'rgba(47,107,237, 0.85)'
              cardContentEl.style.boxShadow = '0 0 25px rgba(47,107,237, 0.3)'
              cardContentEl.style.backgroundColor = '#101828'
            } else {
              cardContentEl.style.borderColor = 'rgba(47,107,237, 0.2)'
              cardContentEl.style.boxShadow = 'none'
              cardContentEl.style.backgroundColor = 'rgba(5,7,12, 0.9)'
            }
          }
        })

        // Core reaction pulse when particle activates a new card
        if (corePulseRef.current) {
          gsap.fromTo(
            corePulseRef.current,
            { scale: 1 },
            {
              scale: 1.015,
              duration: 0.22,
              yoyo: true,
              repeat: 1,
              ease: 'sine.inOut',
              overwrite: 'auto',
            }
          )
        }
      }

      // 6. CONTINUOUS ANGLE CALCULATIONS VIA GSAP TICKER
      const angleTicker = () => {
        if (!orbitRingRef.current || !signalParticleOrbitRef.current) return

        const currentOrbitRot = (gsap.getProperty(orbitRingRef.current, 'rotation') as number) || 0
        const currentParticleRot = (gsap.getProperty(signalParticleOrbitRef.current, 'rotation') as number) || 0

        const particleAngle = ((currentParticleRot % 360) + 360) % 360

        let nearestIdx = 0
        let nearestDist = Infinity

        MODULES.forEach((_, idx) => {
          const baseAngle = idx * 60
          const cardAngle = (((baseAngle + currentOrbitRot) % 360) + 360) % 360
          const dist = angularDistance(particleAngle, cardAngle)

          if (dist < nearestDist) {
            nearestDist = dist
            nearestIdx = idx
          }
        })

        // Activate when particle is within threshold (approx <= 15 deg)
        if (nearestDist <= 15 && nearestIdx !== lastActiveIndexRef.current) {
          lastActiveIndexRef.current = nearestIdx
          triggerCardActivation(nearestIdx)
        }
      }

      gsap.ticker.add(angleTicker)

      // 7. MAGNETIC POINTER INTERACTION FOR CENTRAL CORE (DESKTOP)
      if (!isMobile && systemRef.current && coreMagneticRef.current) {
        const container = systemRef.current
        const maxOffset = 22 // ~20px to 24px

        const handlePointerMove = (e: PointerEvent) => {
          const bounds = container.getBoundingClientRect()
          const normalizedX = (e.clientX - bounds.left) / bounds.width - 0.5
          const normalizedY = (e.clientY - bounds.top) / bounds.height - 0.5

          if (breathingTween && breathingTween.isActive()) {
            breathingTween.pause()
          }

          gsap.to(coreMagneticRef.current, {
            x: normalizedX * maxOffset * 2,
            y: normalizedY * maxOffset * 2,
            scale: 1.04,
            duration: 0.55,
            ease: 'power3.out',
            overwrite: 'auto',
          })
        }

        const handlePointerLeave = () => {
          gsap.to(coreMagneticRef.current, {
            x: 0,
            y: 0,
            scale: 1,
            duration: 0.8,
            ease: 'elastic.out(1, 0.45)',
            overwrite: 'auto',
            onComplete: () => {
              if (breathingTween) {
                breathingTween.resume()
              }
            },
          })
        }

        container.addEventListener('pointermove', handlePointerMove)
        container.addEventListener('pointerleave', handlePointerLeave)

          ; (container as any)._cleanupPointer = () => {
            container.removeEventListener('pointermove', handlePointerMove)
            container.removeEventListener('pointerleave', handlePointerLeave)
          }
      }

      return () => {
        gsap.ticker.remove(angleTicker)
        if (systemRef.current && (systemRef.current as any)._cleanupPointer) {
          ; (systemRef.current as any)._cleanupPointer()
        }
      }
    }, systemRef)

    return () => ctx.revert()
  }, [])

  // --------------------------------------------------
  // HOVER INTERACTION FOR INDIVIDUAL CARDS (SYNCHRONIZED)
  // --------------------------------------------------
  const handleCardMouseEnter = (index: number) => {
    setHoveredIndex(index)
    if (systemRef.current) {
      const masterTl = (systemRef.current as any)._masterTl
      const particleTween = (systemRef.current as any)._particleTween

      // Slow down master timeline and particle tween together
      if (masterTl) gsap.to(masterTl, { timeScale: 0.35, duration: 0.5, overwrite: 'auto' })
      if (particleTween) gsap.to(particleTween, { timeScale: 0.35, duration: 0.5, overwrite: 'auto' })
    }
  }

  const handleCardMouseLeave = () => {
    setHoveredIndex(null)
    if (systemRef.current) {
      const masterTl = (systemRef.current as any)._masterTl
      const particleTween = (systemRef.current as any)._particleTween

      // Return both to full speed together
      if (masterTl) gsap.to(masterTl, { timeScale: 1, duration: 0.5, overwrite: 'auto' })
      if (particleTween) gsap.to(particleTween, { timeScale: 1, duration: 0.5, overwrite: 'auto' })
    }
  }

  return (
    <div
      data-component-version="planetary-review-v1"
      className="flex items-center justify-center w-full max-w-7xl mx-auto"
    >
      {/* ----------------- PLANETARY SYSTEM DIAGRAM CONTAINER ----------------- */}
      <div
        ref={systemRef}
        className="planetary-system relative w-[380px] h-[380px] sm:w-[480px] sm:h-[480px] md:w-[580px] md:h-[580px] flex items-center justify-center select-none touch-none"
        aria-label="NOVA Signal Engine Planetary Architecture"
      >
        {/* Main Circular Orbit Line (Expanded Outer Size) */}
        <div
          className="orbit-line absolute w-[300px] h-[300px] sm:w-[380px] sm:h-[380px] md:w-[460px] md:h-[460px] rounded-full border border-[#2F6BED]/20 bg-[radial-gradient(circle,rgba(47,107,237,0.06)_0%,transparent_70%)] pointer-events-none"
          aria-hidden="true"
        />

        {/* Inner Technical Ring */}
        <div
          ref={innerRingRef}
          className="inner-technical-ring absolute w-[200px] h-[200px] sm:w-[260px] sm:h-[260px] md:w-[280px] md:h-[280px] rounded-full border border-dashed border-[#2F6BED]/15 pointer-events-none opacity-40 will-change-transform"
          aria-hidden="true"
        />

        {/* Orbit Ring Container (Rotates +360 deg clockwise) */}
        <div
          ref={orbitRingRef}
          className="orbit-ring absolute inset-0 w-full z-10 h-full flex items-center justify-center will-change-transform"
          style={{ transformStyle: 'preserve-3d' }}
        >
          {MODULES.map((module, index) => {
            const angleDeg = index * 60

            return (
              <div
                key={module.id}
                className="orbit-position absolute w-full h-full flex items-center justify-center pointer-events-none"
                style={{
                  transform: `rotate(${angleDeg}deg)`,
                }}
              >
                {/* Outer radius offset container (Expanded Radius Offset) */}
                <div className="translate-y-[-150px] sm:translate-y-[-190px] md:translate-y-[-230px]">
                  {/* Counter-rotation wrapper (-360 deg) to keep text upright & horizontal */}
                  <div className="orbit-counter-rotation will-change-transform">
                    {/* Orbit Card Hover & Active state wrapper */}
                    <div
                      ref={(el) => {
                        cardHoverRefs.current[index] = el
                      }}
                      className="orbit-card-hover pointer-events-auto will-change-transform"
                    >
                      <button
                        ref={(el) => {
                          cardContentRefs.current[index] = el
                        }}
                        type="button"
                        onMouseEnter={() => handleCardMouseEnter(index)}
                        onMouseLeave={handleCardMouseLeave}
                        onFocus={() => handleCardMouseEnter(index)}
                        onBlur={handleCardMouseLeave}
                        tabIndex={0}
                        className="orbit-card-content px-4 py-2 sm:px-4.5 sm:py-2.5 rounded-2xl text-center bg-[#05070C]/90 border border-[#2F6BED]/20 text-[#F5F8FF] hover:border-[#2F6BED] hover:text-[#2F6BED] cursor-pointer transition-colors duration-200 backdrop-blur-md flex flex-col items-center justify-center focus:outline-none focus:ring-2 focus:ring-[#2F6BED]"
                      >
                        <span className="text-xs sm:text-sm font-medium tracking-tight block text-white">
                          {module.label}
                        </span>
                        <small className="text-[10px] text-white/60 font-normal leading-tight mt-0.5 max-w-[130px] sm:max-w-[150px] block">
                          {module.subDesc}
                        </small>
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )
          })}
        </div>

        {/* Moving Signal Particle Orbit (Rotates 360 in 9s) */}
        <div
          ref={signalParticleOrbitRef}
          className="signal-particle-orbit absolute inset-0 w-full h-full flex items-center justify-center pointer-events-none will-change-transform "
        >
          <div className="translate-y-[-150px] sm:translate-y-[-190px] md:translate-y-[-230px]">
            <div
              ref={signalParticleRef}
              className="signal-particle w-2.5 h-2.5 rounded-full bg-[#2F6BED] shadow-[0_0_12px_#2F6BED,0_0_24px_#2F6BED]"
              aria-hidden="true"
            />
          </div>
        </div>

        {/* Central CORE System (Outer Magnetic Wrapper + Inner Pulse Wrapper) */}
        <div
          ref={coreMagneticRef}
          className="core-magnetic-wrapper relative z-30 pointer-events-auto will-change-transform"
        >
          <div ref={corePulseRef} className="core-pulse-wrapper relative will-change-transform">
            {/* Attention ring: continuously expands and fades to invite the click */}
            <span
              className="absolute inset-0 rounded-full bg-[#2F6BED]/25 animate-ping pointer-events-none"
              aria-hidden="true"
            />
            <button
              type="button"
              onClick={onEnterApp}
              className="nova-core nova-core-bounce relative !rounded-full !border !border-[#2F6BED]/60 hover:!border-[#2F6BED] w-36 h-36 sm:w-44 sm:h-44 !bg-[#06070B] transition-colors duration-300 flex flex-col items-center justify-center text-center p-4 shadow-[0_0_30px_rgba(47,107,237,0.35),inset_0_0_20px_rgba(47,107,237,0.08)] cursor-pointer focus:outline-none focus:ring-2 focus:ring-[#2F6BED]"
            >
              <span className="text-[10px] uppercase font-medium text-white/50 tracking-wider">
                Click to begin
              </span>
              <strong className="text-sm sm:text-base font-semibold text-[#2F6BED] mt-1 leading-tight">
                Try NOVA Yourself
              </strong>
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
