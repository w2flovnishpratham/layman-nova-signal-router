import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ServerEvent } from '../types'
import {
  browserNotificationState,
  notificationDeepLink,
  notifyForServerEvent,
  requestBrowserNotificationPermission,
  resetBrowserNotificationDedupeForTests,
} from './browserNotifications'

const event = (type: ServerEvent['type'], id = `event-${type}`): ServerEvent => ({
  id,
  type,
  ts: '2026-07-26T09:00:00Z',
  data: { message: `${type} happened` },
})

describe('browser notifications', () => {
  const created: Array<{ title: string; options?: NotificationOptions }> = []

  beforeEach(() => {
    created.length = 0
    resetBrowserNotificationDedupeForTests()
    window.localStorage.clear()
    class FakeNotification {
      static permission: NotificationPermission = 'granted'
      static requestPermission = vi.fn(async () => FakeNotification.permission)
      onclick: ((this: Notification, ev: Event) => unknown) | null = null
      close = vi.fn()

      constructor(title: string, options?: NotificationOptions) {
        created.push({ title, options })
      }
    }
    Object.defineProperty(window, 'Notification', {
      configurable: true,
      value: FakeNotification,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('creates only approved event deep links on the existing Trading route', () => {
    expect(notificationDeepLink(event('order.filled', 'fill-1'))).toBe(
      '/app/trading?tab=activity&event=fill-1',
    )
    expect(notificationDeepLink(event('session.error', 'risk-1'))).toBe(
      '/app/trading?tab=alerts&event=risk-1',
    )
    expect(notificationDeepLink(event('system.event', 'engine-1'))).toBe(
      '/app/trading?tab=engine&event=engine-1',
    )
    expect(notificationDeepLink(event('tick.pnl'))).toBeNull()
    expect(notificationDeepLink({
      ...event('order.filled', 'transport-event'),
      data: { orderId: 'order-42' },
    })).toBe('/app/trading?tab=activity&event=order-42')
  })

  it('deduplicates delivery by server event id', () => {
    const serverEvent = event('order.filled', 'one-fill')
    const preferences = { entry_exit: true }
    expect(notifyForServerEvent(serverEvent, preferences)).toBe('sent')
    expect(notifyForServerEvent(serverEvent, preferences)).toBe('duplicate')
    expect(created).toHaveLength(1)
    expect(created[0].options?.data).toEqual({
      deepLink: '/app/trading?tab=activity&event=one-fill',
      eventId: 'one-fill',
    })
  })

  it('degrades truthfully when permission is denied', async () => {
    Object.defineProperty(window.Notification, 'permission', { value: 'denied' })
    expect(browserNotificationState()).toBe('denied')
    expect(await requestBrowserNotificationPermission()).toBe('denied')
    expect(notifyForServerEvent(event('session.error'), { risk_breach: true })).toBe('unavailable')
    expect(created).toHaveLength(0)
  })
})
