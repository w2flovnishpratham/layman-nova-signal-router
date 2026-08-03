import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MotionConfig } from 'framer-motion'
import '@fontsource-variable/inter/index.css'
import '@fontsource-variable/jetbrains-mono/index.css'
import './index.css'
import { AppRouter } from './AppRouter.tsx'
import { Toaster } from './components/ui/toast'
import { SmoothScroll } from './components/SmoothScroll'
import { initializePreferences, motionConfigMode } from './state/sessionStore'

initializePreferences()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MotionConfig reducedMotion={motionConfigMode()}>
      <SmoothScroll><AppRouter /></SmoothScroll>
      <Toaster />
    </MotionConfig>
  </StrictMode>,
)
