import { motion, useReducedMotion } from 'framer-motion'
import type { ReactNode } from 'react'

export const softEase = [0.16, 1, 0.3, 1] as const

export function useAppReducedMotion(): boolean {
  const prefersReducedMotion = useReducedMotion()
  const appMotion = typeof document === 'undefined' ? undefined : document.documentElement.dataset.motion
  return appMotion === 'reduced' || (appMotion === 'system' && Boolean(prefersReducedMotion))
}

export function MotionSpinner({ children, className = '' }: { children: ReactNode; className?: string }) {
  const reduceMotion = useAppReducedMotion()
  return (
    <motion.span
      className={`motion-icon ${className}`}
      animate={reduceMotion ? { rotate: 0 } : { rotate: 360 }}
      transition={reduceMotion ? { duration: 0 } : { duration: 0.85, ease: 'linear', repeat: Infinity }}
    >
      {children}
    </motion.span>
  )
}

export function MotionPulseText({ children, className = '' }: { children: ReactNode; className?: string }) {
  const reduceMotion = useAppReducedMotion()
  return (
    <motion.span
      className={className}
      animate={reduceMotion ? { opacity: 1 } : { opacity: [0.48, 1, 0.48] }}
      transition={reduceMotion ? { duration: 0 } : { duration: 1.25, ease: 'easeInOut', repeat: Infinity }}
    >
      {children}
    </motion.span>
  )
}

export function MotionPing({ className = '' }: { className?: string }) {
  const reduceMotion = useAppReducedMotion()
  return (
    <span className={`motion-ping ${className}`} aria-hidden="true">
      <motion.span
        className="motion-ping-ring"
        animate={reduceMotion ? { scale: 1, opacity: 0 } : { scale: [1, 2.1], opacity: [0.32, 0] }}
        transition={reduceMotion ? { duration: 0 } : { duration: 1.55, ease: 'easeOut', repeat: Infinity }}
      />
      <span className="motion-ping-dot" />
    </span>
  )
}

export function MotionProgressFill({ durationSeconds, tone = 'danger' }: { durationSeconds: number; tone?: 'danger' | 'live' }) {
  const reduceMotion = useAppReducedMotion()
  return (
    <motion.span
      className={`motion-progress-fill tone-${tone}`}
      initial={{ scaleX: 0 }}
      animate={{ scaleX: 1 }}
      transition={reduceMotion ? { duration: 0 } : { duration: durationSeconds, ease: 'linear' }}
      aria-hidden="true"
    />
  )
}
