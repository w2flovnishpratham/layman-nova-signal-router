import type { ClientCommand, ServerEvent, ServerEventType } from './types'

type Handler = (event: ServerEvent) => void
type StatusHandler = (status: 'live' | 'degraded' | 'down') => void
type InvalidSessionHandler = () => void

export class SessionWS {
  private socket: WebSocket | null = null
  private retryTimer: number | null = null
  private retry = 0
  private closedByClient = false
  private invalidated = false
  private openedThisAttempt = false
  private readonly handlers = new Set<Handler>()
  private readonly statusHandlers = new Set<StatusHandler>()
  private readonly seen = new Set<string>()
  private readonly sessionId: string
  private readonly token: string
  private invalidSessionHandler: InvalidSessionHandler | null = null

  constructor(sessionId: string, token: string) {
    this.sessionId = sessionId
    this.token = token
  }

  connect() {
    this.closedByClient = false
    this.invalidated = false
    this.openSocket()
  }

  private openSocket() {
    if (this.closedByClient || this.socket) return
    this.openedThisAttempt = false
    const wsUrl = this.buildUrl()
    this.socket = new WebSocket(wsUrl)

    this.socket.addEventListener('open', () => {
      this.openedThisAttempt = true
      this.retry = 0
      this.emitStatus('live')
    })

    this.socket.addEventListener('message', (message) => {
      const event = JSON.parse(message.data as string) as ServerEvent
      if (this.seen.has(event.id)) return
      this.seen.add(event.id)
      this.handlers.forEach((handler) => handler(event))
    })

    this.socket.addEventListener('close', async (closeEvent) => {
      this.socket = null
      if (this.closedByClient) {
        this.emitStatus('down')
        return
      }
      const sessionState = await this.sessionState()
      const sessionIsInvalid = closeEvent.code === 4401
        || closeEvent.code === 4404
        || sessionState === 'missing'
        || (!this.openedThisAttempt && sessionState === 'exists')
      if (this.closedByClient) {
        this.emitStatus('down')
        return
      }
      if (sessionIsInvalid) {
        this.invalidated = true
        this.closedByClient = true
        this.emitStatus('down')
        this.invalidSessionHandler?.()
        return
      }
      this.emitStatus('degraded')
      const delay = Math.min(30_000, 1000 * 2 ** this.retry)
      this.retry += 1
      this.retryTimer = window.setTimeout(() => {
        this.retryTimer = null
        this.openSocket()
      }, delay)
    })

    this.socket.addEventListener('error', () => {
      this.emitStatus('degraded')
    })
  }

  on(_type: ServerEventType | '*', handler: Handler): () => void {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  onStatus(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler)
    return () => this.statusHandlers.delete(handler)
  }

  onInvalidSession(handler: InvalidSessionHandler): void {
    this.invalidSessionHandler = handler
  }

  send(command: ClientCommand): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(command))
    }
  }

  close(): void {
    this.closedByClient = true
    if (this.retryTimer !== null) {
      window.clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
    this.socket?.close()
  }

  private buildUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}/ws/session/${this.sessionId}?token=${encodeURIComponent(this.token)}`
  }

  private emitStatus(status: 'live' | 'degraded' | 'down'): void {
    this.statusHandlers.forEach((handler) => handler(status))
  }

  private async sessionState(): Promise<'exists' | 'missing' | 'unavailable'> {
    if (this.invalidated) return 'missing'
    try {
      const response = await fetch(`/api/session/${this.sessionId}`, { cache: 'no-store' })
      if (response.status === 404) return 'missing'
      return response.ok ? 'exists' : 'unavailable'
    } catch {
      return 'unavailable'
    }
  }
}
