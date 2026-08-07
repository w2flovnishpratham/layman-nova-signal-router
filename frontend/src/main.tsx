import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MotionConfig } from 'framer-motion'
import { QueryClientProvider } from '@tanstack/react-query'
import '@fontsource-variable/inter/index.css'
import '@fontsource-variable/jetbrains-mono/index.css'
import './index.css'
import { AppRouter } from './AppRouter.tsx'
import { Toaster } from './components/ui/toast'
import { SmoothScroll } from './components/SmoothScroll'
import { queryClient, wireQueryInvalidation } from './lib/queryClient'
import { initializePreferences, motionConfigMode } from './state/sessionStore'

// Recover from a deploy landing while the app is open. The routes are lazily
// loaded (React.lazy), so a session holding the previous index.html asks for
// content-hashed chunks that the new build has replaced. Vite fires
// vite:preloadError for that; reloading picks up the current index.html and its
// chunk names. Without this the tab just dies on "error loading dynamically
// imported module" until the user thinks to hard-refresh.
//
// The sessionStorage latch bounds it to ONE reload per tab: if a chunk is
// genuinely missing rather than merely stale, a second failure must surface as
// an error instead of an infinite reload loop.
const RELOAD_LATCH = 'nova-chunk-reload'
window.addEventListener('vite:preloadError', (event) => {
  if (sessionStorage.getItem(RELOAD_LATCH)) return
  event.preventDefault()
  try {
    sessionStorage.setItem(RELOAD_LATCH, '1')
  } catch {
    // Private mode etc -- reload anyway; worst case the latch is not enforced.
  }
  window.location.reload()
})
window.addEventListener('load', () => {
  // Cleared only after a load completes, so the latch covers the reload itself.
  try {
    sessionStorage.removeItem(RELOAD_LATCH)
  } catch {
    // Nothing to do.
  }
})

initializePreferences()
wireQueryInvalidation()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <MotionConfig reducedMotion={motionConfigMode()}>
        <SmoothScroll><AppRouter /></SmoothScroll>
        <Toaster />
      </MotionConfig>
    </QueryClientProvider>
  </StrictMode>,
)
