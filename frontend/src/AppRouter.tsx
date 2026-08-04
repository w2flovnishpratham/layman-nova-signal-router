import { lazy, Suspense, useEffect, useState } from 'react'
import { currentPath, isAppPath, navigate, replacePath } from './navigation'
import { appPath, routeFromPath } from './appRoutes'

// Landing (marketing/animation-heavy) and App (the authenticated trading
// terminal) are mutually exclusive per visit -- splitting them keeps a first-
// time visitor from downloading the entire trading app, and a logged-in
// trader from downloading the landing page's animation libraries.
const App = lazy(() => import('./App'))
const LandingPage = lazy(() => import('./landing/LandingPage').then((m) => ({ default: m.LandingPage })))

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
    if (path === '/app/setup') {
      const params = new URLSearchParams(window.location.search)
      params.delete('edit')
      params.set('editSetup', '1')
      replacePath(`${appPath('trading')}?${params.toString()}`)
      return
    }
    const canonical = appPath(routeFromPath(path))
    if (path !== canonical) replacePath(canonical)
  }, [path])

  return (
    <Suspense fallback={null}>
      {isAppPath(path) ? (
        <App />
      ) : (
        <LandingPage onEnterApp={() => navigate(appPath(routeFromPath('/app')))} />
      )}
    </Suspense>
  )
}
