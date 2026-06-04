const configuredBackendUrl = import.meta.env.VITE_BACKEND_URL?.trim().replace(/\/+$/, '')

export function backendHttpUrl(path: `/${string}`): string {
  return configuredBackendUrl ? `${configuredBackendUrl}${path}` : path
}

export function backendWsUrl(path: `/${string}`): string {
  if (!configuredBackendUrl) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}${path}`
  }

  const backendUrl = new URL(configuredBackendUrl)
  backendUrl.protocol = backendUrl.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${backendUrl.origin}${path}`
}
