import { motion, useReducedMotion } from 'framer-motion'

export function TypingDots() {
  const prefersReducedMotion = useReducedMotion()
  const reduceMotion = appReducedMotion(prefersReducedMotion)

  return (
    <div className="typing-row" aria-label="Nova is typing">
      <div className="bot-avatar">N</div>
      <div className="typing-bubble">
        {[0, 1, 2].map((index) => (
          <motion.span
            key={index}
            animate={reduceMotion ? { opacity: 0.7 } : { y: [0, -3, 0], opacity: [0.4, 1, 0.4] }}
            transition={
              reduceMotion
                ? { duration: 0 }
                : {
                    duration: 0.9,
                    repeat: Infinity,
                    ease: 'easeInOut',
                    delay: index * 0.12,
                  }
            }
          />
        ))}
      </div>
    </div>
  )
}

function appReducedMotion(prefersReducedMotion: boolean | null): boolean {
  const appMotion = document.documentElement.dataset.motion
  return appMotion === 'reduced' || (appMotion === 'system' && Boolean(prefersReducedMotion))
}
