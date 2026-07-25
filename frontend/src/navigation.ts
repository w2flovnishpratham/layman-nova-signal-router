// Tiny URL-bound navigation helpers shared by the app shell and the landing CTA.
// Kept separate from AppRouter.tsx so that file only exports a component
// (react-refresh/only-export-components).

export function currentPath(): string {
  return window.location.pathname
}

export function currentLocation(): string {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`
}

export function isAppPath(path: string): boolean {
  return path === '/app' || path.startsWith('/app/')
}

/** Client-side navigation without a full page load. */
export function navigate(to: string): void {
  if (currentLocation() === to) return
  window.history.pushState({}, '', to)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

/** Canonicalise the URL without adding a history entry (redirects/normalisation). */
export function replacePath(to: string): void {
  if (currentLocation() === to) return
  window.history.replaceState({}, '', to)
  window.dispatchEvent(new PopStateEvent('popstate'))
}
