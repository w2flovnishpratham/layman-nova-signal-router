import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MotionConfig } from 'framer-motion'
import '@fontsource-variable/inter/index.css'
import '@fontsource-variable/jetbrains-mono/index.css'
import './index.css'
import App from './App.tsx'
import { initializePreferences, motionConfigMode } from './state/sessionStore'

initializePreferences()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MotionConfig reducedMotion={motionConfigMode()}>
      <App />
    </MotionConfig>
  </StrictMode>,
)
