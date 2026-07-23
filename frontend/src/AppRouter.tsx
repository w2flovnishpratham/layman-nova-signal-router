import { useEffect, useState } from 'react'
import App from './App'
import { LandingPage } from './landing/LandingPage'
import { currentPath, isAppPath, navigate } from './navigation'

// A small URL-bound wrapper instead of a full router dependency. Nginx already
// serves index.html for any non-asset path (SPA fallback), so a hard refresh on
// either "/" or "/app" loads the bundle and this component renders the right view
// from window.location.pathname.

export function AppRouter() {
  const [path, setPath] = useState(currentPath)

  useEffect(() => {
    const onPop = () => setPath(currentPath())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  if (isAppPath(path)) {
    return <App />
  }
  return <LandingPage onEnterApp={() => navigate('/app')} />
}
