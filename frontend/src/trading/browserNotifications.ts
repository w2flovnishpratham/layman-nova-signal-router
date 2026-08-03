import type { ServerEvent } from '../types'

export type BrowserNotificationState = 'granted' | 'denied' | 'prompt' | 'unsupported'
type NotificationPreferences = Record<string, boolean>

const SEEN_STORAGE_KEY = 'nova.browser-notification-events'
const MAX_SEEN = 200
const memorySeen = new Set<string>()

export function browserNotificationState(): BrowserNotificationState {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported'
  return window.Notification.permission === 'default' ? 'prompt' : window.Notification.permission
}

export async function requestBrowserNotificationPermission(): Promise<BrowserNotificationState> {
  if (browserNotificationState() === 'unsupported') return 'unsupported'
  try {
    const permission = await window.Notification.requestPermission()
    return permission === 'default' ? 'prompt' : permission
  } catch {
    return 'unsupported'
  }
}

export function notificationDeepLink(event: ServerEvent): string | null {
  const tab = event.type === 'session.error' || event.type === 'order.rejected'
    ? 'alerts'
    : event.type === 'system.event'
      ? 'engine'
      : event.type === 'order.filled' || event.type === 'trade.exit' || event.type === 'session.eod'
        ? 'activity'
        : null
  const focusId = stringValue(event.data.orderId)
    || stringValue(event.data.order_id)
    || stringValue(event.data.signalId)
    || stringValue(event.data.signal_id)
    || stringValue(event.data.correlationId)
    || stringValue(event.data.correlation_id)
    || event.id
  return tab ? `/app/trading?tab=${tab}&event=${encodeURIComponent(focusId)}` : null
}

export function notifyForServerEvent(
  event: ServerEvent,
  preferences: NotificationPreferences,
): 'sent' | 'disabled' | 'duplicate' | 'unavailable' {
  const channel = event.type === 'order.filled' || event.type === 'trade.exit' || event.type === 'session.eod'
    ? 'entry_exit'
    : event.type === 'session.error' || event.type === 'order.rejected'
      ? 'risk_breach'
      : event.type === 'system.event'
        ? 'engine_state'
        : null
  const deepLink = notificationDeepLink(event)
  if (!channel || !deepLink || !preferences[channel]) return 'disabled'
  if (hasSeen(event.id)) return 'duplicate'
  if (browserNotificationState() !== 'granted') return 'unavailable'

  const message = stringValue(event.data.message)
    || stringValue(event.data.label)
    || titleForChannel(channel)
  try {
    const notification = new window.Notification('NOVA Signal Router', {
      body: message,
      tag: `nova-${event.id}`,
      data: { deepLink, eventId: event.id },
    })
    notification.onclick = () => {
      window.focus()
      window.location.assign(deepLink)
      notification.close()
    }
    remember(event.id)
    return 'sent'
  } catch {
    return 'unavailable'
  }
}

function titleForChannel(channel: string): string {
  if (channel === 'entry_exit') return 'Trading position updated.'
  if (channel === 'risk_breach') return 'A risk condition needs attention.'
  return 'Engine state changed.'
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.slice(0, 240) : ''
}

function hasSeen(eventId: string): boolean {
  if (memorySeen.has(eventId)) return true
  try {
    const stored = JSON.parse(window.localStorage.getItem(SEEN_STORAGE_KEY) ?? '[]') as unknown
    if (Array.isArray(stored) && stored.includes(eventId)) {
      memorySeen.add(eventId)
      return true
    }
  } catch {
    // Memory deduplication remains available when storage is blocked.
  }
  return false
}

function remember(eventId: string): void {
  memorySeen.add(eventId)
  try {
    const stored = JSON.parse(window.localStorage.getItem(SEEN_STORAGE_KEY) ?? '[]') as unknown
    const existing = Array.isArray(stored)
      ? stored.filter((value): value is string => typeof value === 'string')
      : []
    window.localStorage.setItem(
      SEEN_STORAGE_KEY,
      JSON.stringify([...existing.filter((value) => value !== eventId), eventId].slice(-MAX_SEEN)),
    )
  } catch {
    // Delivery must never depend on storage access.
  }
}

export function resetBrowserNotificationDedupeForTests(): void {
  memorySeen.clear()
}
