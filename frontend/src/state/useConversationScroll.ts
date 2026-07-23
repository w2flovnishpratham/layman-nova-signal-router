import { useCallback, useEffect, useRef, useState } from 'react'

const NEAR_BOTTOM_PX = 96

function isNearBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_PX
}

/**
 * One reusable scroll controller for the conversation canvas.
 *
 * - When the user is near the bottom, new content auto-scrolls to the latest.
 * - When the user has scrolled up, their position is preserved and "Jump to
 *   latest" is shown instead of yanking the viewport down.
 * - Reduced motion uses immediate ('auto') scrolling.
 * - A single scroll listener is attached; it's removed on unmount. No
 *   ResizeObserver / layout-measuring effect loops.
 *
 * `itemCount` (the transcript entry count) is the new-content signal; the effect
 * only reacts to it changing, and reads liveness from a ref so it never fights a
 * user who is reading older messages.
 */
export function useConversationScroll(options: { itemCount: number; reducedMotion?: boolean }) {
  const { itemCount, reducedMotion = false } = options
  const ref = useRef<HTMLDivElement | null>(null)
  const nearBottomRef = useRef(true)
  const [showJump, setShowJump] = useState(false)

  const scrollToLatest = useCallback(() => {
    const el = ref.current
    if (!el || typeof el.scrollTo !== 'function') return
    el.scrollTo({ top: el.scrollHeight, behavior: reducedMotion ? 'auto' : 'smooth' })
    nearBottomRef.current = true
    setShowJump(false)
  }, [reducedMotion])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const onScroll = () => {
      const near = isNearBottom(el)
      nearBottomRef.current = near
      setShowJump((prev) => (prev === !near ? prev : !near))
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  // React only to new content. If the user is near the bottom, follow it; if they
  // have scrolled up, leave their position and surface the jump affordance.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (nearBottomRef.current) {
      if (typeof el.scrollTo === 'function') {
        el.scrollTo({ top: el.scrollHeight, behavior: reducedMotion ? 'auto' : 'smooth' })
      }
      setShowJump(false)
    } else {
      setShowJump(true)
    }
  }, [itemCount, reducedMotion])

  return { ref, showJump, jumpToLatest: scrollToLatest }
}
