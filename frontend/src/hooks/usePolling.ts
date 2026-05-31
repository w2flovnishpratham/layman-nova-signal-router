/**
 * FE-C3 — Visibility-aware polling hook.
 *
 * `setInterval` keeps firing on backgrounded tabs, which:
 *   - drains mobile battery,
 *   - hammers your backend (and Dhan rate limits in REAL mode),
 *   - delivers stale data the user can't see anyway.
 *
 * This hook wraps the common pattern of "call this function on mount, then
 * every N ms" with two improvements:
 *   1. Pauses while the tab/window is hidden (`document.hidden === true`).
 *   2. Fires once immediately on remount or when the tab becomes visible
 *      again, so the user sees fresh data the moment they look at it.
 *
 * Usage:
 *   usePolling(loadData, 5000)
 *
 * The callback is captured by ref so it always points at the latest closure
 * without re-creating the interval on every render.
 */
import { useEffect, useRef } from 'react'

export function usePolling(callback: () => void | Promise<unknown>, intervalMs: number) {
  const savedCallback = useRef(callback)

  // Keep the ref pointed at the latest callback without restarting the loop.
  useEffect(() => {
    savedCallback.current = callback
  }, [callback])

  useEffect(() => {
    let timerId: number | null = null

    const tick = () => {
      try {
        const result = savedCallback.current()
        // If the callback returns a promise, let it run; rejection is the
        // caller's responsibility (we don't swallow it silently here).
        if (result && typeof (result as Promise<unknown>).then === 'function') {
          ;(result as Promise<unknown>).catch(() => undefined)
        }
      } catch {
        // Synchronous throws shouldn't kill the interval.
      }
    }

    const start = () => {
      if (timerId !== null) return
      tick() // fire immediately on (re)start so the page isn't stale
      timerId = window.setInterval(tick, intervalMs)
    }

    const stop = () => {
      if (timerId !== null) {
        window.clearInterval(timerId)
        timerId = null
      }
    }

    const onVisibilityChange = () => {
      if (document.hidden) {
        stop()
      } else {
        start()
      }
    }

    // Initial state: start only if visible.
    if (!document.hidden) start()

    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange)
      stop()
    }
  }, [intervalMs])
}
