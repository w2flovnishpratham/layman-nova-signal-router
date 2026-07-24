"use client";

import React, { useEffect, useRef } from "react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import mobileMockup from "../../assets/landing/mobile-mockup.png";
import portfolioCard from "../../assets/landing/portfolio-card.png";
import riskLimitCard from "../../assets/landing/risk-limit-card.jpg";
import { cn } from "../utils";

if (typeof window !== "undefined") {
    gsap.registerPlugin(ScrollTrigger);
}

const INJECTED_STYLES = `
  .gsap-reveal { visibility: hidden; }

  /* Environment Overlays */
  .film-grain {
      position: absolute; inset: 0; width: 100%; height: 100%;
      pointer-events: none; z-index: 50; opacity: 0.05; mix-blend-mode: overlay;
      background: url('data:image/svg+xml;utf8,<svg viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg"><filter id="noiseFilter"><feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves="3" stitchTiles="stitch"/></filter><rect width="100%" height="100%" filter="url(%23noiseFilter)"/></svg>');
  }

  .bg-grid-theme {
      background-size: 60px 60px;
      background-image:
          linear-gradient(to right, color-mix(in srgb, #2F6BED 8%, transparent) 1px, transparent 1px),
          linear-gradient(to bottom, color-mix(in srgb, #2F6BED 8%, transparent) 1px, transparent 1px);
      mask-image: radial-gradient(ellipse at center, black 0%, transparent 70%);
      -webkit-mask-image: radial-gradient(ellipse at center, black 0%, transparent 70%);
  }

  /* -------------------------------------------------------------------
     PHYSICAL SKEUOMORPHIC MATERIALS (NOVA Deep Olive & Lime Theme)
  ---------------------------------------------------------------------- */

  /* OUTSIDE THE CARD: Theme-aware text (Ivory & Lime Glow) */
  .text-3d-matte {
      color: #F5F8FF;
      text-shadow:
          0 10px 30px rgba(47,107,237, 0.18),
          0 2px 4px rgba(0, 0, 0, 0.6);
  }

  .text-silver-matte {
      background: linear-gradient(180deg, #F5F8FF 0%, #2F6BED 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      transform: translateZ(0); /* Hardware acceleration to prevent WebKit clipping bug */
      filter:
          drop-shadow(0px 10px 20px rgba(47,107,237,0.25))
          drop-shadow(0px 2px 4px rgba(0,0,0,0.5));
  }

  /* INSIDE THE CARD: Metallic Lime / Ivory for the dark olive background */
  .text-card-silver-matte {
      background: linear-gradient(180deg, #F5F8FF 0%, #2F6BED 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      transform: translateZ(0);
      filter:
          drop-shadow(0px 12px 24px rgba(0,0,0,0.9))
          drop-shadow(0px 4px 8px rgba(0,0,0,0.7));
  }

  /* Deep Physical Card with Dynamic Mouse Lighting */
  .premium-depth-card {
      background: linear-gradient(145deg, #101828 0%, #05070C 100%);
      box-shadow:
          0 40px 100px -20px rgba(0, 0, 0, 0.95),
          0 20px 40px -20px rgba(0, 0, 0, 0.85),
          inset 0 1px 2px rgba(47,107,237, 0.25),
          inset 0 -2px 4px rgba(0, 0, 0, 0.9);
      border: 1px solid rgba(47,107,237, 0.2);
      position: relative;
  }

  .card-sheen {
      position: absolute; inset: 0; border-radius: inherit; pointer-events: none; z-index: 50;
      background: radial-gradient(800px circle at var(--mouse-x, 50%) var(--mouse-y, 50%), rgba(47,107,237,0.12) 0%, transparent 40%);
      mix-blend-mode: screen; transition: opacity 0.3s ease;
  }

  /* Physical Tactile Buttons matched to Lime Accent */
  .btn-modern-light {
      transition: all 0.4s cubic-bezier(0.25, 1, 0.5, 1);
      background: linear-gradient(180deg, #2F6BED 0%, #2456C0 100%);
      color: #05070C;
      box-shadow: 0 0 0 1px rgba(47,107,237,0.4), 0 4px 20px rgba(47,107,237,0.3), 0 12px 28px -4px rgba(0,0,0,0.7), inset 0 1px 1px rgba(255,255,255,0.7), inset 0 -3px 6px rgba(0,0,0,0.15);
  }
  .btn-modern-light:hover {
      transform: translateY(-3px);
      background: linear-gradient(180deg, #5C93FF 0%, #3F78F0 100%);
      box-shadow: 0 0 0 1px rgba(47,107,237,0.6), 0 8px 30px rgba(47,107,237,0.45), 0 20px 36px -6px rgba(0,0,0,0.8), inset 0 1px 1px rgba(255,255,255,0.8), inset 0 -3px 6px rgba(0,0,0,0.15);
  }
  .btn-modern-light:active {
      transform: translateY(1px);
      background: linear-gradient(180deg, #2456C0 0%, #1F4CAB 100%);
      box-shadow: 0 0 0 1px rgba(47,107,237,0.3), 0 2px 4px rgba(0,0,0,0.4), inset 0 3px 6px rgba(0,0,0,0.2), inset 0 0 0 1px rgba(0,0,0,0.05);
  }
`;

export interface CinematicHeroProps extends React.HTMLAttributes<HTMLDivElement> {
    brandName?: string;
    tagline1?: string;
    tagline2?: string;
    cardHeading?: string;
    cardDescription?: React.ReactNode;
    metricValue?: number;
    metricLabel?: string;
    ctaHeading?: string;
    ctaDescription?: string;
}

export function CinematicHero({
    brandName = "NOVA",
    tagline1 = "Track the market,",
    tagline2 = "master the signals.",
    cardHeading = "Signal Route, Redefined.",
    cardDescription = <><span className="text-[#F5F8FF] font-semibold">NOVA Signal Route</span> empowers algorithmic traders and institutional investors with real-time risk management, low-latency execution, and interactive analytics timelines.</>,
    metricValue = 365,
    ctaHeading = "Start trading now.",
    ctaDescription = "Join high-performance algorithmic traders and take control of your execution route today.",
    className,
    ...props
}: CinematicHeroProps) {

    const containerRef = useRef<HTMLDivElement>(null);
    const mainCardRef = useRef<HTMLDivElement>(null);
    const mockupRef = useRef<HTMLDivElement>(null);
    const requestRef = useRef<number>(0);

    // 1. High-Performance Mouse Interaction Logic (Using requestAnimationFrame)
    useEffect(() => {
        const handleMouseMove = (e: MouseEvent) => {
            if (window.scrollY > window.innerHeight * 2) return;

            cancelAnimationFrame(requestRef.current);

            requestRef.current = requestAnimationFrame(() => {
                if (mainCardRef.current && mockupRef.current) {
                    const rect = mainCardRef.current.getBoundingClientRect();
                    const mouseX = e.clientX - rect.left;
                    const mouseY = e.clientY - rect.top;

                    mainCardRef.current.style.setProperty("--mouse-x", `${mouseX}px`);
                    mainCardRef.current.style.setProperty("--mouse-y", `${mouseY}px`);

                    const xVal = (e.clientX / window.innerWidth - 0.5) * 2;
                    const yVal = (e.clientY / window.innerHeight - 0.5) * 2;

                    gsap.to(mockupRef.current, {
                        rotationY: xVal * 12,
                        rotationX: -yVal * 12,
                        ease: "power3.out",
                        duration: 1.2,
                    });
                }
            });
        };

        window.addEventListener("mousemove", handleMouseMove);
        return () => {
            window.removeEventListener("mousemove", handleMouseMove);
            cancelAnimationFrame(requestRef.current);
        };
    }, []);

    // 2. Complex Cinematic Scroll Timeline
    useEffect(() => {
        const isMobile = window.innerWidth < 768;

        const ctx = gsap.context(() => {
            gsap.set(".text-track", { autoAlpha: 0, y: 60, scale: 0.85, filter: "blur(20px)", rotationX: -20 });
            gsap.set(".text-days", { autoAlpha: 1, clipPath: "inset(0 100% 0 0)" });
            gsap.set(".main-card", { y: window.innerHeight + 200, autoAlpha: 1 });
            gsap.set([".card-left-text", ".card-right-text", ".mockup-scroll-wrapper", ".floating-badge", ".phone-widget"], { autoAlpha: 0 });
            gsap.set(".cta-wrapper", { autoAlpha: 0, scale: 0.8, filter: "blur(30px)" });

            const introTl = gsap.timeline({ delay: 0.3 });
            introTl
                .to(".text-track", { duration: 1.8, autoAlpha: 1, y: 0, scale: 1, filter: "blur(0px)", rotationX: 0, ease: "expo.out" })
                .to(".text-days", { duration: 1.4, clipPath: "inset(0 0% 0 0)", ease: "power4.inOut" }, "-=1.0");

            const scrollTl = gsap.timeline({
                scrollTrigger: {
                    trigger: containerRef.current,
                    start: "top top",
                    end: "+=7000",
                    pin: true,
                    scrub: 1,
                    anticipatePin: 1,
                },
            });

            scrollTl
                .to([".hero-text-wrapper", ".bg-grid-theme"], { scale: 1.15, filter: "blur(20px)", opacity: 0.2, ease: "power2.inOut", duration: 2 }, 0)
                .to(".main-card", { y: 0, ease: "power3.inOut", duration: 2 }, 0)
                .to(".main-card", { width: "100%", height: "100%", borderRadius: "0px", ease: "power3.inOut", duration: 1.5 })
                .fromTo(".mockup-scroll-wrapper",
                    { y: 300, z: -500, rotationX: 50, rotationY: -30, autoAlpha: 0, scale: 0.6 },
                    { y: 0, z: 0, rotationX: 0, rotationY: 0, autoAlpha: 1, scale: 1, ease: "expo.out", duration: 2.5 }, "-=0.8"
                )
                .fromTo(".phone-widget", { y: 40, autoAlpha: 0, scale: 0.95 }, { y: 0, autoAlpha: 1, scale: 1, stagger: 0.15, ease: "back.out(1.2)", duration: 1.5 }, "-=1.5")
                .fromTo(".floating-badge", { y: 100, autoAlpha: 0, scale: 0.7, rotationZ: -10 }, { y: 0, autoAlpha: 1, scale: 1, rotationZ: 0, ease: "back.out(1.5)", duration: 1.5, stagger: 0.2 }, "-=2.0")
                .fromTo(".card-left-text", { x: -50, autoAlpha: 0 }, { x: 0, autoAlpha: 1, ease: "power4.out", duration: 1.5 }, "-=1.5")
                .fromTo(".card-right-text", { x: 50, autoAlpha: 0, scale: 0.8 }, { x: 0, autoAlpha: 1, scale: 1, ease: "expo.out", duration: 1.5 }, "<")
                .to({}, { duration: 2.5 })
                .set(".hero-text-wrapper", { autoAlpha: 0 })
                .set(".cta-wrapper", { autoAlpha: 1 })
                .to({}, { duration: 1.5 })
                .to([".mockup-scroll-wrapper", ".floating-badge", ".card-left-text", ".card-right-text"], {
                    scale: 0.9, y: -40, z: -200, autoAlpha: 0, ease: "power3.in", duration: 1.2, stagger: 0.05,
                })
                // Responsive card pullback sizing
                .to(".main-card", {
                    width: isMobile ? "92vw" : "85vw",
                    height: isMobile ? "92vh" : "85vh",
                    borderRadius: isMobile ? "32px" : "40px",
                    ease: "expo.inOut",
                    duration: 1.8
                }, "pullback")
                .to(".cta-wrapper", { scale: 1, filter: "blur(0px)", ease: "expo.inOut", duration: 1.8 }, "pullback")
                .to(".main-card", { y: -window.innerHeight - 300, ease: "power3.in", duration: 1.5 });

        }, containerRef);

        return () => ctx.revert();
    }, [metricValue]);

    return (
        <div
            ref={containerRef}
            className={cn("relative w-full min-h-screen overflow-hidden flex items-center justify-center bg-[#05070C] text-[#F5F8FF] font-montreal antialiased", className)}
            style={{ perspective: "1500px" }}
            {...props}
        >
            <style dangerouslySetInnerHTML={{ __html: INJECTED_STYLES }} />
            <div className="film-grain" aria-hidden="true" />
            <div className="bg-grid-theme absolute inset-0 z-0 pointer-events-none opacity-50" aria-hidden="true" />

            {/* BACKGROUND LAYER: Hero Texts (Centering fix) */}
            <div className="hero-text-wrapper absolute inset-0 z-10 flex flex-col items-center justify-center text-center w-full px-4 will-change-transform transform-style-3d">
                <h1 className="text-track gsap-reveal text-3d-matte text-5xl md:text-7xl lg:text-[6rem] font-bold tracking-tight mb-2">
                    {tagline1}
                </h1>
                <h1 className="text-days gsap-reveal text-silver-matte text-5xl md:text-7xl lg:text-[6rem] font-extrabold tracking-tighter">
                    {tagline2}
                </h1>
            </div>

            {/* BACKGROUND LAYER 2: Tactile CTA Buttons (Centering fix) */}
            <div className="cta-wrapper absolute inset-0 z-10 flex flex-col items-center justify-center text-center w-full px-4 gsap-reveal pointer-events-auto will-change-transform">
                <h2 className="text-4xl md:text-6xl lg:text-7xl font-bold mb-6 tracking-tight text-silver-matte">
                    {ctaHeading}
                </h2>
                <p className="text-[#F5F8FF]/70 text-lg md:text-xl mb-12 max-w-xl mx-auto font-light leading-relaxed">
                    {ctaDescription}
                </p>
                <div className="flex flex-col sm:flex-row gap-6">
                    <a href="#discovery" aria-label="Launch Trading Platform" className="btn-modern-light flex items-center justify-center gap-3 px-8 py-4 rounded-[1.25rem] group focus:outline-none focus:ring-2 focus:ring-[#2F6BED] focus:ring-offset-2">
                        <div className="text-center">
                            <div className="text-xs font-extrabold tracking-wider text-[#05070C] uppercase">LAUNCH TRADING PLATFORM</div>
                        </div>
                    </a>
                </div>
            </div>

            {/* FOREGROUND LAYER: The Physical Deep Olive/Sage Card (Centering fix) */}
            <div className="absolute inset-0 z-20 flex items-center justify-center pointer-events-none" style={{ perspective: "1500px" }}>
                <div
                    ref={mainCardRef}
                    className="main-card premium-depth-card relative overflow-hidden gsap-reveal flex items-center justify-center pointer-events-auto w-[92vw] md:w-[85vw] h-[92vh] md:h-[85vh] rounded-[32px] md:rounded-[40px]"
                >
                    <div className="card-sheen" aria-hidden="true" />

                    {/* DYNAMIC RESPONSIVE GRID: Flex-col on mobile to force order, Grid on desktop */}
                    <div className="relative w-full h-full max-w-7xl mx-auto px-4 lg:px-12 flex flex-col justify-evenly lg:grid lg:grid-cols-3 items-center lg:gap-8 z-10 py-6 lg:py-0">

                        {/* 1. TOP (Mobile) / RIGHT (Desktop): BRAND NAME */}
                        <div className="card-right-text gsap-reveal order-1 lg:order-3 flex justify-center lg:justify-end relative lg:-left-[20px] xl:-left-[40px] z-20 w-full pointer-events-none">
                            <h2 className="text-4xl md:text-[6rem] lg:text-[8rem] xl:text-[10rem] font-black uppercase tracking-tighter text-card-silver-matte lg:mt-0">
                                {brandName}
                            </h2>
                        </div>

                        {/* 2. MIDDLE (Mobile) / CENTER (Desktop): IPHONE MOCKUP IMAGE */}
                        <div className="mockup-scroll-wrapper order-2 lg:order-2 relative w-full h-[380px] lg:h-[600px] flex items-center justify-center z-10" style={{ perspective: "1000px" }}>

                            {/* Inner wrapper for safe CSS scaling that doesn't conflict with GSAP */}
                            <div className="relative w-full h-full flex items-center justify-center transform scale-[0.78] md:scale-[0.95] lg:scale-[1.18] z-10">

                                {/* Mobile image uses the same animated wrappers and therefore follows the same scroll motion. */}
                                <div
                                    ref={mockupRef}
                                    className="relative w-[300px] md:w-[330px] h-full max-h-[380px] lg:max-h-[580px] flex items-center justify-center will-change-transform transform-style-3d z-10"
                                >
                                    <div className="phone-widget relative w-full h-full flex items-center justify-center">
                                        <img
                                            src={mobileMockup}
                                            alt="Mobile Trading App Mockup"
                                            className="block w-full h-full object-contain object-center drop-shadow-[0_40px_80px_rgba(0,0,0,0.9)] select-none"
                                            draggable={false}
                                            onLoad={() => ScrollTrigger.refresh()}
                                        />
                                    </div>
                                </div>

                                {/* Floating Widget Card 1 (Top Left): Portfolio Live Performance Chart */}
                                <div className="floating-badge absolute top-2 lg:top-4 left-[-15px] md:left-[-40px] lg:left-[-70px] z-30 w-24 md:w-36 lg:w-40 rounded-xl border border-[#2F6BED]/25 shadow-[0_15px_35px_rgba(0,0,0,0.85)] hover:scale-[1.05] transition-all duration-300 pointer-events-auto overflow-hidden bg-[#080B14]/90 backdrop-blur-xl group">
                                    <div className="absolute inset-0 bg-gradient-to-tr from-[#2F6BED]/10 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" />
                                    <img
                                        src={portfolioCard}
                                        alt="Portfolio Live Performance Chart"
                                        className="w-full h-auto object-contain rounded-xl filter brightness-105"
                                    />
                                </div>

                                {/* Floating Widget Card 2 (Bottom Right): Real-Time Risk Limit Gauge */}
                                <div className="floating-badge absolute bottom-6 lg:bottom-10 right-[-15px] md:right-[-40px] lg:right-[-70px] z-30 w-24 md:w-36 lg:w-40 rounded-xl border border-[#2F6BED]/25 shadow-[0_15px_35px_rgba(0,0,0,0.85)] hover:scale-[1.05] transition-all duration-300 pointer-events-auto overflow-hidden bg-[#080B14]/90 backdrop-blur-xl group">
                                    <div className="absolute inset-0 bg-gradient-to-bl from-[#2F6BED]/10 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" />
                                    <img
                                        src={riskLimitCard}
                                        alt="Real-Time Risk Limit Gauge"
                                        className="w-full h-auto object-contain rounded-xl filter brightness-105"
                                    />
                                </div>

                            </div>
                        </div>

                        {/* 3. BOTTOM (Mobile) / LEFT (Desktop): ACCOUNTABILITY TEXT */}
                        <div className="card-left-text gsap-reveal order-3 lg:order-1 flex flex-col justify-center text-center lg:text-left z-20 w-full lg:max-w-none px-4 lg:px-0">
                            <h3 className="text-[#F5F8FF] text-2xl md:text-3xl lg:text-4xl font-medium mb-0 lg:mb-5 tracking-tight">
                                {cardHeading}
                            </h3>
                            {/* HIDDEN ON MOBILE (added hidden md:block) */}
                            <p className="hidden md:block text-[#F5F8FF]/80 text-sm md:text-base lg:text-lg font-normal leading-relaxed mx-auto lg:mx-0 max-w-sm lg:max-w-none">
                                {cardDescription}
                            </p>
                        </div>

                    </div>
                </div>
            </div>
        </div>
    );
}
