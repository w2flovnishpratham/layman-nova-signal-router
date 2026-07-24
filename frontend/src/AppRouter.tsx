import { useEffect, useState } from 'react'
import App from './App'
import { LandingPage } from './landing/LandingPage'
import { currentPath, isAppPath, navigate, replacePath } from './navigation'
import { appPath, routeFromPath } from './appRoutes'

// A small URL-bound wrapper instead of a full router dependency. Nginx already
// serves index.html for any non-asset path (SPA fallback), so a hard refresh on
// "/", "/app" or any nested "/app/*" route loads the bundle and this component
// renders the right view from window.location.pathname.

export function AppRouter() {
  const [path, setPath] = useState(currentPath)

  useEffect(() => {
    const onPop = () => setPath(currentPath())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  // Canonicalise authenticated URLs: "/app" redirects to the default route and an
  // unknown "/app/*" path resolves safely to it, without adding a history entry.
  useEffect(() => {
    if (!isAppPath(path)) return
    const canonical = appPath(routeFromPath(path))
    if (path !== canonical) replacePath(canonical)
  }, [path])

  if (isAppPath(path)) {
    return <App />
  }
  return <LandingPage onEnterApp={() => navigate(appPath(routeFromPath('/app')))} />
}
