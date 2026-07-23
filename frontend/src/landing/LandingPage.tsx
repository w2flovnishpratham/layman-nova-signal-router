import { useEffect, useRef, useState } from 'react'
import gsap from 'gsap'
import laptopMockup from '../assets/landing/laptop-mockup.png'
import nobgFooter from '../assets/landing/nobgfooter.png'
import portfolioCard from '../assets/landing/portfolio-card.png'
import riskLimitCard from '../assets/landing/risk-limit-card.jpg'
import automatedRiskEngine from '../assets/landing/hover/Automated_Risk_Engine.png'
import analyticsTimelines from '../assets/landing/hover/Interactive_Analytics_Timelines_Interactive_PnL_heatmaps.png'
import lowLatencyExecution from '../assets/landing/hover/Low-Latency_Execution.png'
import webhookSignalIngestion from '../assets/landing/hover/Webhook_Signal_Ingestion.png'
import awsLogo from '../assets/landing/logos/aws.png'
import dhanLogo from '../assets/landing/logos/dhan.png'
import hostingerLogo from '../assets/landing/logos/hostinger.png'
import nodeLogo from '../assets/landing/logos/node.png'
import nseLogo from '../assets/landing/logos/nse.png'
import pythonLogo from '../assets/landing/logos/python.png'
import reactLogo from '../assets/landing/logos/react.png'
import typescriptLogo from '../assets/landing/logos/typescript.png'
import { CinematicHero } from './components/CinematicHero'
import { Marquee } from './components/Marquee'
import { PlanetaryEcosystem } from './PlanetaryEcosystem'
import './landing.css'

const landingLogos = [
  { name: 'Dhan Broker', src: dhanLogo },
  { name: 'NSE India', src: nseLogo },
  { name: 'AWS Cloud', src: awsLogo },
  { name: 'Hostinger', src: hostingerLogo },
  { name: 'Node.js', src: nodeLogo },
  { name: 'Python', src: pythonLogo },
  { name: 'React', src: reactLogo },
  { name: 'TypeScript', src: typescriptLogo },
]

const LANDING_PRODUCT_VIDEO_URL = '/media/NOVA_Signal_Route_product_animation.mp4'

const SERVICES_DATA = [
  {
    num: '01',
    title: 'Webhook Signal Ingestion',
    desc: 'Instant TradingView webhook payload parsing, checksum validation, and sub-millisecond order signal transformation.',
    image: webhookSignalIngestion,
  },
  {
    num: '02',
    title: 'Automated Risk Engine',
    desc: 'Real-time daily drawdown protection, dynamic trailing stops, automated position limits, and instant kill switches.',
    image: automatedRiskEngine,
  },
  {
    num: '03',
    title: 'Low-Latency Execution',
    desc: 'Sub-50ms execution roundtrips via direct Dhan API gateways with order queuing, slippage protection, and automated failover.',
    image: lowLatencyExecution,
  },
  {
    num: '04',
    title: 'Interactive Analytics Timelines',
    desc: 'Interactive PnL heatmaps, live execution timelines, latency tracking, and full trade replay dashboards.',
    image: analyticsTimelines,
  },
]

export function LandingPage({ onEnterApp }: { onEnterApp?: () => void }) {
  const [emailSub, setEmailSub] = useState('')
  const [emailDone, setEmailDone] = useState(false)

  const [hoveredService, setHoveredService] = useState<number | null>(null)
  const servicesContainerRef = useRef<HTMLDivElement>(null)
  const targetMousePos = useRef<{ x: number; y: number }>({ x: 0, y: 0 })
  const currentMousePos = useRef<{ x: number; y: number }>({ x: 0, y: 0 })
  const [previewPos, setPreviewPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 })

  const laptopRef = useRef<HTMLDivElement>(null)
  const heroTextRef = useRef<HTMLHeadingElement>(null)
  const statementRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let animId: number
    const updateLerp = () => {
      if (hoveredService !== null) {
        // Smooth lerp follow factor 0.12 for laggy, damped floating motion
        currentMousePos.current.x += (targetMousePos.current.x - currentMousePos.current.x) * 0.12
        currentMousePos.current.y += (targetMousePos.current.y - currentMousePos.current.y) * 0.12
        setPreviewPos({ x: currentMousePos.current.x, y: currentMousePos.current.y })
        animId = requestAnimationFrame(updateLerp)
      }
    }

    if (hoveredService !== null) {
      animId = requestAnimationFrame(updateLerp)
    }

    return () => {
      if (animId) cancelAnimationFrame(animId)
    }
  }, [hoveredService])

  const handleServicesMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!servicesContainerRef.current) return
    const rect = servicesContainerRef.current.getBoundingClientRect()
    targetMousePos.current = {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
    }
  }

  const handleServiceMouseEnter = (index: number, e: React.MouseEvent<HTMLDivElement>) => {
    if (servicesContainerRef.current) {
      const rect = servicesContainerRef.current.getBoundingClientRect()
      const x = e.clientX - rect.left
      const y = e.clientY - rect.top
      if (hoveredService === null) {
        currentMousePos.current = { x, y }
        setPreviewPos({ x, y })
      }
      targetMousePos.current = { x, y }
    }
    setHoveredService(index)
  }

  const handleServiceMouseLeave = () => {
    setHoveredService(null)
  }

  useEffect(() => {
    // GSAP smooth float & subtle tilt animation + staggered text reveals
    const ctx = gsap.context(() => {
      if (laptopRef.current) {
        gsap.to(laptopRef.current, {
          y: -18,
          rotation: 1.2,
          duration: 3.5,
          repeat: -1,
          yoyo: true,
          ease: 'power1.inOut',
        })
      }

      if (heroTextRef.current) {
        const headlineItems = heroTextRef.current.querySelectorAll('.reveal-text')
        gsap.fromTo(
          headlineItems,
          { y: '115%', opacity: 0 },
          {
            y: '0%',
            opacity: 1,
            duration: 1.1,
            stagger: 0.16,
            ease: 'power3.out',
            delay: 0.1,
          }
        )
      }

      if (statementRef.current) {
        const statementWords = statementRef.current.querySelectorAll('.reveal-word')
        gsap.fromTo(
          statementWords,
          { y: '115%', opacity: 0 },
          {
            y: '0%',
            opacity: 1,
            duration: 0.9,
            stagger: 0.045,
            ease: 'power3.out',
            delay: 0.3,
          }
        )
      }
    })

    return () => ctx.revert()
  }, [])

  function handleSubscribe(e: React.FormEvent) {
    e.preventDefault()
    if (!emailSub) return
    setEmailDone(true)
    setTimeout(() => {
      setEmailDone(false)
      setEmailSub('')
    }, 4000)
  }

  return (
    <div className="nova-landing w-full min-h-screen bg-[#0A0D04] text-[#FAFFF3] overflow-x-hidden relative">
      {/* ----------------- Top Navigation Header ----------------- */}
      <header className="sticky top-0 z-50 w-full bg-[#0A0D04]/90 backdrop-blur-xl border-b border-[#C0F53D]/15 py-4 px-6 md:px-12 flex items-center justify-between">
        <div className="flex items-center gap-12">
          <span className="font-extrabold text-xl tracking-tighter text-white uppercase font-montreal flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-[#C0F53D] shadow-[0_0_12px_#C0F53D]" />
            NOVA SIGNAL ROUTE
          </span>
          <nav className="hidden md:flex items-center gap-8" aria-label="Landing Navigation">
            <a href="#services" className="text-[11px] font-medium tracking-[0.12em] uppercase text-[#FAFFF3]/60 hover:text-[#C0F53D] transition-colors">SERVICES</a>
            <a href="#work" className="text-[11px] font-medium tracking-[0.12em] uppercase text-[#FAFFF3]/60 hover:text-[#C0F53D] transition-colors">WORK</a>
            <a href="#about" className="text-[11px] font-medium tracking-[0.12em] uppercase text-[#FAFFF3]/60 hover:text-[#C0F53D] transition-colors">ABOUT</a>
            <a href="#case-studies" className="text-[11px] font-medium tracking-[0.12em] uppercase text-[#FAFFF3]/60 hover:text-[#C0F53D] transition-colors">CASE STUDIES</a>
            <a href="#contact" className="text-[11px] font-medium tracking-[0.12em] uppercase text-[#FAFFF3]/60 hover:text-[#C0F53D] transition-colors">CONTACT</a>
          </nav>
        </div>
        <div className="flex items-center gap-3">

          <a
            href="#discovery"
            className="hidden sm:inline-flex px-5 py-2 rounded-full border border-white/20 text-xs font-medium text-white hover:border-[#C0F53D] hover:text-[#C0F53D] transition-colors"
          >
            LAUNCH TRADING APP
          </a>
        </div>
      </header>

      {/* ----------------- Hero Section ----------------- */}
      <section className="relative pt-12 pb-20 px-6 md:px-12 max-w-7xl mx-auto" id="discovery">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 items-center">
          {/* Main Hero Headline Left */}
          <div className="lg:col-span-5 flex flex-col items-start gap-8">
            <h1 ref={heroTextRef} className="text-[clamp(42px,8vw,105px)] leading-[0.95] tracking-[-0.03em] text-white font-medium">
              <span className="block overflow-hidden py-1">
                <span className="block reveal-text will-change-transform">Master</span>
              </span>
              <span className="block overflow-hidden py-1">
                <span className="block reveal-text will-change-transform">
                  <em className="font-serif italic font-normal text-[#C0F53D] tracking-normal pr-2">The</em>
                  Signals
                </span>
              </span>
            </h1>
          </div>

          {/* Hero Laptop Showcase with Greenish Blur Glow & GSAP Floating Animation */}
          <div className="lg:col-span-7 flex items-center justify-center lg:justify-end relative py-2">
            <div className="relative flex items-center justify-center w-full translate-x-6 md:translate-x-16 lg:translate-x-28 translate-y-2 md:translate-y-4 lg:translate-y-8">
              {/* Greenish glowing circle with blur radius behind laptop */}
              <div className="absolute w-180 bg-[radial-gradient(circle,rgba(192,245,61,0.48)_0%,rgba(160,230,40,0.28)_45%,rgba(192,245,61,0)_75%)] blur-[95px] pointer-events-none top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 animate-pulse" aria-hidden="true" />

              {/* Floating Laptop Component animated with GSAP */}
              <div ref={laptopRef} className="relative z-10 w-full max-w-[960px] scale-105 lg:scale-125 origin-center will-change-transform">
                <img
                  src={laptopMockup}
                  alt="NOVA Signal Route Laptop Showcase"
                  className="w-full h-auto object-contain drop-shadow-2xl hover:drop-shadow-[0_25px_50px_rgba(0,0,0,0.95)] transition-all duration-300"
                />
              </div>
            </div>
          </div>
        </div>

        {/* Bottom Hero Statement Text (Staggered reveal animation from overflow hidden) */}
        <div ref={statementRef} className="mt-4 py-10 flex flex-col items-center justify-center text-center">
          <div className="max-w-5xl px-4">
            <p className="text-xl md:text-2xl lg:text-[35px] leading-relaxed md:leading-[1.38] tracking-tight font-extralight text-[#FAFFF3]">
              <span className="inline-block overflow-hidden align-top py-0.5">
                <span className="inline-block reveal-word uppercase tracking-widest text-white font-extralight will-change-transform">NOVA SIGNAL ROUTE</span>
              </span>{' '}
              <span className="inline-block overflow-hidden align-top py-0.5">
                <em className="inline-block reveal-word font-serif italic font-extralight text-[#C0F53D] pr-1.5 will-change-transform">empowers</em>
              </span>{' '}
              <span className="inline-block overflow-hidden align-top py-0.5">
                <span className="inline-block reveal-word uppercase tracking-wider text-white font-extralight will-change-transform">ALGORITHMIC TRADERS</span>
              </span>{' '}
              <span className="inline-block overflow-hidden align-top py-0.5">
                <em className="inline-block reveal-word font-serif italic font-extralight text-[#FAFFF3]/90 px-1.5 will-change-transform">and</em>
              </span>{' '}
              <span className="inline-block overflow-hidden align-top py-0.5">
                <span className="inline-block reveal-word uppercase tracking-wider text-white font-extralight will-change-transform">INSTITUTIONAL INVESTORS</span>
              </span>{' '}
              <span className="inline-block overflow-hidden align-top py-0.5">
                <em className="inline-block reveal-word font-serif italic font-extralight text-[#FAFFF3]/90 px-1.5 will-change-transform">with</em>
              </span>{' '}
              <span className="inline-block overflow-hidden align-top py-0.5">
                <em className="inline-block reveal-word font-serif italic font-extralight text-[#C0F53D] pr-1.5 will-change-transform">real-time risk management,</em>
              </span>{' '}
              <span className="inline-block overflow-hidden align-top py-0.5">
                <span className="inline-block reveal-word uppercase tracking-wider text-white font-extralight will-change-transform">LOW-LATENCY EXECUTION,</span>
              </span>{' '}
              <span className="inline-block overflow-hidden align-top py-0.5">
                <em className="inline-block reveal-word font-serif italic font-extralight text-[#FAFFF3]/90 px-1.5 will-change-transform">and</em>
              </span>{' '}
              <span className="inline-block overflow-hidden align-top py-0.5">
                <span className="inline-block reveal-word font-serif italic font-extralight text-[#C0F53D] uppercase tracking-wider will-change-transform">INTERACTIVE ANALYTICS TIMELINES.</span>
              </span>
            </p>
          </div>
        </div>
      </section>

      {/* Cinematic Landing Hero Component (Full width layer to prevent ScrollTrigger pinning layout shifts) */}
      <div className="w-full relative border-y border-[#C0F53D]/10 bg-[#0A0D04]">
        <CinematicHero />
      </div>

      {/* ----------------- Ecosystem Diagram Section ----------------- */}
      <section className="py-24 px-6 md:px-12 max-w-7xl mx-auto " id="about">
        <div className="text-center max-w-3xl mx-auto mb-16">
          <h2 className="text-3xl md:text-5xl font-medium tracking-tight text-white leading-tight">
            Institutional risk management & ultra low-latency <br />
            <em className="font-serif italic text-[#C0F53D]">signal execution</em> architecture.
          </h2>
        </div>

        <PlanetaryEcosystem />
      </section>

      {/* ----------------- Portfolio Showcase Section ----------------- */}
      <section className="py-24 px-6 md:px-12 max-w-7xl mx-auto border-t border-white/10" id="work">
        <div className="flex flex-col md:flex-row md:items-end justify-between mb-16 gap-6">
          <div>
            <span className="text-xs font-bold uppercase tracking-widest text-[#C0F53D] block mb-2">
              02 / SELECTED WORK
            </span>
            <h2 className="text-4xl md:text-6xl font-medium tracking-tight text-white">
              Next Level <em className="font-serif italic text-[#C0F53D]">Execution</em>
            </h2>
          </div>
          <a href="#case-studies" className="inline-flex items-center gap-2 px-5 py-2 rounded-full border border-[#C0F53D]/25 bg-[#1A2209]/50 text-xs font-semibold text-[#FAFFF3] hover:border-[#C0F53D] hover:text-[#C0F53D] transition-all self-start md:self-auto">
            Portfolio
          </a>
        </div>

        {/* Portfolio Cards Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          <div className="bg-[#1A2209]/70 border border-[#C0F53D]/12 rounded-2xl overflow-hidden hover:-translate-y-1 hover:border-[#C0F53D]/40 transition-all duration-300 group cursor-pointer">
            <div className="h-96 w-full overflow-hidden relative">
              <img
                src={portfolioCard}
                alt="High-Frequency Signal Router"
                className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
              />
              <div className="absolute inset-0 bg-linear-to-t from-black/90 via-black/20 to-transparent" />
              <div className="absolute bottom-6 left-6 right-6">
                <span className="text-xs text-[#C0F53D] uppercase font-bold block mb-1">
                  SIGNAL ROUTING
                </span>
                <h3 className="text-2xl font-bold text-white">TradingView to Dhan Gateway</h3>
                <p className="text-xs text-white/70 mt-1">Sub-50ms Order Execution & Payload Validation</p>
              </div>
            </div>
          </div>

          <div className="bg-[#1A2209]/70 border border-[#C0F53D]/12 rounded-2xl overflow-hidden hover:-translate-y-1 hover:border-[#C0F53D]/40 transition-all duration-300 group cursor-pointer">
            <div className="h-96 w-full overflow-hidden relative">
              <img
                src={riskLimitCard}
                alt="Institutional Risk Dashboard"
                className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
              />
              <div className="absolute inset-0 bg-linear-to-t from-black/90 via-black/20 to-transparent" />
              <div className="absolute bottom-6 left-6 right-6">
                <span className="text-xs text-[#C0F53D] uppercase font-bold block mb-1">
                  RISK MANAGEMENT
                </span>
                <h3 className="text-2xl font-bold text-white">Institutional Risk Control</h3>
                <p className="text-xs text-white/70 mt-1">Interactive Timelines & Drawdown Protection</p>
              </div>
            </div>
          </div>
        </div>

        {/* Infinite Brand & Technology Logo Marquee - Compact Sleek Strip */}

      </section>


      {/* ----------------- Case Studies Grid Section ----------------- */}
      <section className="py-24 px-6 md:px-12 max-w-7xl mx-auto" id="case-studies">
        <div className="text-center max-w-3xl mx-auto mb-16">
          <h2 className="text-4xl md:text-6xl font-medium tracking-tight text-white mb-4">
            Empowering Traders to <em className="font-serif italic text-[#C0F53D]">Execute Faster</em>
          </h2>
          <p className="text-sm text-white/60">
            Proven results delivering institutional execution & low-latency performance.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          <div className="bg-white/3 border border-white/10 rounded-2xl p-8 flex flex-col justify-between hover:border-[#C0F53D]/50 transition-colors">
            <div>
              <span className="text-xs text-[#C0F53D] font-bold block mb-2">CASE 01</span>
              <h3 className="text-2xl font-bold text-white mb-2">Apex Alpha Fund</h3>
              <p className="text-xs text-white/60 leading-relaxed mb-6">
                Automated Nifty index options routing with sub-50ms webhook ingestion.
              </p>
            </div>
            <div className="pt-6 border-t border-white/10 flex justify-between items-center">
              <div>
                <span className="text-2xl font-extrabold text-[#C0F53D] block">&lt;35ms</span>
                <span className="text-[10px] text-white/50 uppercase">Order Latency</span>
              </div>
              <div className="text-right">
                <span className="text-2xl font-extrabold text-white block">+184%</span>
                <span className="text-[10px] text-white/50 uppercase">Fill Efficiency</span>
              </div>
            </div>
          </div>

          <div className="bg-white/3 border border-white/10 rounded-2xl p-8 flex flex-col justify-between hover:border-[#C0F53D]/50 transition-colors">
            <div>
              <span className="text-xs text-[#C0F53D] font-bold block mb-2">CASE 02</span>
              <h3 className="text-2xl font-bold text-white mb-2">QuantPulse Capital</h3>
              <p className="text-xs text-white/60 leading-relaxed mb-6">
                Real-time risk management and institutional position sizing automation.
              </p>
            </div>
            <div className="pt-6 border-t border-white/10 flex justify-between items-center">
              <div>
                <span className="text-2xl font-extrabold text-[#C0F53D] block">$12.4M</span>
                <span className="text-[10px] text-white/50 uppercase">Volume Routed</span>
              </div>
              <div className="text-right">
                <span className="text-2xl font-extrabold text-white block">0.01%</span>
                <span className="text-[10px] text-white/50 uppercase">Max Slippage</span>
              </div>
            </div>
          </div>

          <div className="bg-white/3 border border-white/10 rounded-2xl p-8 flex flex-col justify-between hover:border-[#C0F53D]/50 transition-colors">
            <div>
              <span className="text-xs text-[#C0F53D] font-bold block mb-2">CASE 03</span>
              <h3 className="text-2xl font-bold text-white mb-2">Veloce Trading</h3>
              <p className="text-xs text-white/60 leading-relaxed mb-6">
                Seamless paper-to-live strategy transition with zero code modifications.
              </p>
            </div>
            <div className="pt-6 border-t border-white/10 flex justify-between items-center">
              <div>
                <span className="text-2xl font-extrabold text-[#d4ff32] block">99.99%</span>
                <span className="text-[10px] text-white/50 uppercase">Signal Uptime</span>
              </div>
              <div className="text-right">
                <span className="text-2xl font-extrabold text-white block">4.8x</span>
                <span className="text-[10px] text-white/50 uppercase">Sharpe Ratio</span>
              </div>
            </div>
          </div>
        </div>
      </section>
       <div className="my-12 py-2.5 w-full overflow-hidden marquee-mask relative bg-[#080808] hover:bg-[#000000] transition-colors duration-500 group/marquee">
        <Marquee pauseOnHover repeat={4} className="[--duration:25s] [--gap:8rem] items-center p-0">
          {landingLogos.map((logo, index) => (
            <div
              key={`${logo.name}-${index}`}
              className="flex items-center shrink-0 justify-between opacity-40 hover:opacity-100 transition-opacity duration-300 cursor-pointer px-2"
              title={logo.name}
            >
              <img
                src={logo.src}
                alt={logo.name}
                className="h-20 md:h-14 w-auto max-w-45  object-contain brightness-0 invert opacity-40 hover:opacity-100 transition-all duration-300 drop-shadow-[0_0_8px_rgba(255,255,255,0.12)]"
              />
            </div>
          ))}
        </Marquee>
      </div>
      {/* ----------------- Light Services Showcase Section ----------------- */}
      <section className="" id="services">
        {/* Ambient Lime Outer Gradient Frame */}
        <div className="relative p-3 sm:p-5 md:p-7 bg-linear-to-b from-[#ccf661] via-[#d2f47c] to-[#d7f695] shadow-[0_0_60px_rgba(192,245,61,0.25)]">
          {/* Inner Floating Ivory Card */}
          <div
            ref={servicesContainerRef}
            onMouseMove={handleServicesMouseMove}
            onMouseLeave={handleServiceMouseLeave}
            className="relative bg-[#FAFFF3] text-[#0A0D04] p-8 my-10 sm:p-12 md:p-16 shadow-2xl overflow-hidden"
          >
            <div className="text-center max-w-2xl mx-auto mb-14">
              <h2 className="text-4xl sm:text-5xl md:text-6xl font-medium tracking-tight text-black">
                What we do <em className="font-serif italic font-normal">best.</em>
              </h2>
            </div>

            {/* Floating Cursor-Follow Image Preview Card (Desktop) */}
            {(() => {
              const cardWidth = 400
              const cardHeight = 250
              let clampedX = previewPos.x
              let clampedY = previewPos.y

              if (servicesContainerRef.current) {
                const rect = servicesContainerRef.current.getBoundingClientRect()
                clampedX = Math.max(cardWidth / 2 + 16, Math.min(rect.width - cardWidth / 2 - 16, previewPos.x))
                clampedY = Math.max(cardHeight / 2 + 16, Math.min(rect.height - cardHeight / 2 - 16, previewPos.y))
              }

              return (
                <div
                  className="pointer-events-none absolute z-40 transition-opacity duration-300 ease-out will-change-transform hidden md:block"
                  style={{
                    left: `${clampedX}px`,
                    top: `${clampedY}px`,
                    transform: `translate(-80%, -50%) scale(${hoveredService !== null ? 1 : 0.75})`,
                    opacity: hoveredService !== null ? 1 : 0,
                  }}
                >
                  <div className="w-[360px] lg:w-[420px]">
                    {SERVICES_DATA.map((service, idx) => (
                      <img
                        key={service.num}
                        src={service.image}
                        alt={service.title}
                        className={`w-full h-auto object-cover transition-all duration-300 ${hoveredService === idx ? 'block opacity-75 scale-85' : 'hidden opacity-0 scale-65'
                          }`}
                      />
                    ))}
                  </div>
                </div>
              )
            })()}

            <div className="divide-y divide-black/15 font-medium relative z-10">
              {SERVICES_DATA.map((service, index) => {
                const isActive = hoveredService === index
                return (
                  <div
                    key={service.num}
                    onMouseEnter={(e) => handleServiceMouseEnter(index, e)}
                    className={`py-8 flex flex-col md:flex-row md:items-center justify-between gap-6 cursor-pointer transition-all duration-300 ${isActive ? 'bg-black/[0.025] -mx-4 px-4 rounded-xl' : ''
                      }`}
                  >
                    <div className="flex items-center gap-6 md:w-1/2">
                      <span
                        className={`text-sm font-montreal transition-colors duration-300 ${isActive ? 'text-[#80AF1B] font-bold' : 'text-black/50'
                          }`}
                      >
                        {service.num}
                      </span>
                      <h3
                        className={`text-xl sm:text-2xl md:text-3xl font-montreal font-medium transition-colors duration-300 ${isActive ? 'text-[#80AF1B]' : 'text-black'
                          }`}
                      >
                        {service.title}
                      </h3>
                    </div>
                    <p
                      className={`text-xs sm:text-sm max-w-md leading-relaxed transition-colors duration-300 md:w-1/2 ${isActive ? 'text-black/90 font-medium' : 'text-black/75'
                        }`}
                    >
                      {service.desc}
                    </p>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </section>

      {/* ----------------- Product Animation Video Section ----------------- */}
      <section className="py-24 px-6 md:px-12 max-w-7xl mx-auto border-t border-white/10">
        <div className="text-center max-w-3xl mx-auto mb-12">
          <span className="text-xs font-medium uppercase tracking-widest text-[#C0F53D] block mb-4">
            PRODUCT ANIMATION
          </span>
          <h2 className="text-3xl md:text-5xl font-medium tracking-tight text-white mb-4">
            Experience real-time execution & <br className="hidden md:block" />
            <em className="font-serif italic text-[#C0F53D]">low-latency risk routing</em> in action.
          </h2>
          <p className="text-xs text-white/60 font-normal">
            Seamless TradingView webhook ingestion, Dhan API execution, and live position safeguards.
          </p>
        </div>

        <div className="relative">
          <div className="relative overflow-hidden aspect-video flex items-center justify-center">
            <video
              src={LANDING_PRODUCT_VIDEO_URL}
              autoPlay
              loop
              muted
              playsInline
              preload="metadata"
              className="w-full h-full object-cover rounded-2xl"
            >
              Your browser does not support embedded MP4 video.
            </video>
          </div>
        </div>
      </section>

      {/* ----------------- Full Electric Lime CTA Section ----------------- */}
      <section className="py-16" id="contact">
        <div className="bg-[#C0F53D] text-[#0A0D04]  py-16 px-9 text-center">
          <span className="text-xs font-extrabold uppercase tracking-widest text-black/70 block mb-3">
            GET STARTED
          </span>
          <h2 className="text-4xl md:text-7xl font-medium tracking-tight text-black max-w-4xl mx-auto mb-8 leading-tight">
            Take command of your algorithmic execution with <em className="font-serif italic">NOVA Signal Route.</em>
          </h2>
          {onEnterApp ? (
            <button
              type="button"
              onClick={onEnterApp}
              className="bg-[#0A0D04]  text-[#C0F53D] border-2 border-[#0A0D04] font-bold text-xs px-6 py-3 rounded-full hover:bg-[#1A2209] hover:-translate-y-0.5 transition-all cursor-pointer "
            >
              Launch Trading Platform
            </button>
          ) : (
            <a href="#discovery" className="bg-[#0A0D04]  text-[#C0F53D] font-bold text-xs px-6 py-3 rounded-full hover:bg-[#1A2209] hover:-translate-y-0.5 transition-all cursor-pointer inline-block">
              Get Started
            </a>
          )}
        </div>
      </section>

      {/* ----------------- Unified Matte-Black to Olive-Lime Footer Card ----------------- */}
      <footer className=''>
        <div className="relative flex min-h-[500px] flex-col justify-between overflow-hidden bg-[#070807] p-8 shadow-[0_0_60px_rgba(0,0,0,0.8)] sm:p-12 md:p-16">
          {/* Large unrestricted bottom blur — no inner overflow clipping */}
          <div
            className="
        pointer-events-none absolute
        -bottom-[190px] left-1/2
        h-[430px] w-[92%]
        -translate-x-1/2
        rounded-[50%]
        bg-[linear-gradient(90deg,rgba(192,245,61,0.88)_0%,rgba(177,228,57,0.74)_48%,rgba(101,205,135,0.50)_100%)]
        opacity-85
        blur-[95px]
      "
          />

          {/* Secondary softer atmosphere */}
          <div
            className="
        pointer-events-none absolute
        -bottom-[210px] left-1/2
        h-[460px] w-full
        -translate-x-1/2
        rounded-[50%]
        bg-[#7FA52B]/20
        blur-[135px]
      "
          />

          {/* Soft black fade so the glow blends naturally upward */}
          <div
            className="
        pointer-events-none absolute inset-0
        bg-[linear-gradient(180deg,rgba(7,8,7,1)_0%,rgba(7,8,7,1)_48%,rgba(7,8,7,0.96)_62%,rgba(7,8,7,0.55)_78%,rgba(7,8,7,0)_100%)]
      "
          />

          {/* Main Footer Content Grid */}
          <div className="relative z-10 mb-16 grid grid-cols-1 items-start gap-12 lg:grid-cols-12 ">
            {/* Left: Brand Pill + Heading + Email Form */}
            <div className="flex flex-col gap-6 lg:col-span-6">
              <div className="self-start rounded-[1000px] border border-white/20 bg-white/5 px-5 py-2 text-[11px] font-medium uppercase tracking-widest text-white">
                NOVA ROUTE
              </div>

              <h3 className="max-w-md text-2xl font-normal leading-snug tracking-tight text-white sm:text-3xl md:text-4xl">
                <em className="font-editorial italic text-white">
                  Sign up to harness the
                </em>{" "}
                <span className="font-montreal">power of NOVA.</span>
              </h3>

              {emailDone ? (
                <div className="max-w-sm rounded-xl border border-[#C0F53D]/30 bg-[#C0F53D]/10 px-4 py-3 text-xs font-medium text-[#C0F53D]">
                  ✓ Thank you! You&apos;re subscribed to NOVA updates.
                </div>
              ) : (
                <form
                  onSubmit={handleSubscribe}
                  className="mt-2 flex w-full max-w-md items-center gap-3"
                >
                  <div className="relative flex-1">
                    <input
                      type="email"
                      required
                      placeholder="Email"
                      value={emailSub}
                      onChange={(e) => setEmailSub(e.target.value)}
                      className="w-full rounded-xl border border-white/20 bg-black/40 px-4 py-3 text-xs text-white placeholder:text-white/40 transition-colors focus:border-[#C0F53D] focus:outline-none"
                    />
                  </div>

                  <button
                    type="submit"
                    aria-label="Subscribe"
                    className="flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-xl border border-[#C0F53D]/30 bg-[#1A2209] text-sm font-bold text-white transition-all hover:border-[#C0F53D] hover:text-[#C0F53D]"
                  >
                    →
                  </button>
                </form>
              )}
            </div>

            {/* Right: Footer Feature Illustration */}
            <div className="flex items-center justify-center lg:justify-end lg:col-span-6 overflow-visible">
              <img
              src={nobgFooter}
                alt="NOVA Signal Route Footer Feature"
                className="w-full max-w-2xl opacity-50  lg:max-w-3xl scale-120 lg:scale-[1.6] origin-center lg:origin-right h-auto object-contain  pointer-events-none select-none transition-transform duration-500 hover:scale-[1.85]"
              />
            </div>
          </div>

          {/* Bottom Copyright */}
          <div className="relative z-10 flex flex-col items-center justify-center gap-2.5 pt-6 text-center">
            <span className="font-montreal text-[15px] tracking-wider text-white/40 sm:text-[16px]">
              ©2026 Nova Signal Route
            </span>
          </div>
        </div>
      </footer>
    </div>
  )
}
