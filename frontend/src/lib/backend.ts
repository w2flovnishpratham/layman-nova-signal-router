const defaultBackendUrl = import.meta.env.PROD ? 'https://engine-api.novatradesolution.com' : ''
const configuredBackendUrl = import.meta.env.VITE_BACKEND_URL?.trim().replace(/[;/\s]+$/, '')
const backendUrl = configuredBackendUrl || defaultBackendUrl

export function backendHttpUrl(path: `/${string}`): string {
  return backendUrl ? `${backendUrl}${path}` : path
}

export function backendWsUrl(path: `/${string}`): string {
  if (!backendUrl) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}${path}`
  }

  const websocketUrl = new URL(backendUrl)
  websocketUrl.protocol = websocketUrl.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${websocketUrl.origin}${path}`
}
