import { NativeSelect } from "@/components/ui/native-select"
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { toast } from '@/components/ui/toast'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { AlertTriangle, Download } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import type { AppRoute } from '../appRoutes'
import { PageSkeleton } from '../components/PageSkeleton'
import type { AuthUser } from '../api'
import {
  CHART_TIMEFRAMES,
  TABLE_DENSITIES,
  exportTrades,
  getCredentialsSummary,
  getPreferences,
  resetPaperSession,
  savePreferences,
  type CredentialsSummary,
  type Preferences,
} from './settingsApi'
import {
  browserNotificationState,
  requestBrowserNotificationPermission,
  type BrowserNotificationState,
} from '../trading/browserNotifications'

const TIMEZONES = ['Asia/Kolkata', 'UTC', 'America/New_York', 'Europe/London', 'Asia/Singapore']

export function SettingsPage({
  user,
  onNavigate,
  onLogout,
}: {
  user?: AuthUser
  onNavigate?: (route: AppRoute) => void
  onLogout?: () => void
}) {
  const [prefs, setPrefs] = useState<Preferences | null>(null)
  const [connections, setConnections] = useState<CredentialsSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [confirmReset, setConfirmReset] = useState(false)
  const [notificationState, setNotificationState] = useState<BrowserNotificationState>(
    browserNotificationState,
  )

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [nextPrefs, nextConnections] = await Promise.all([
        getPreferences(),
        getCredentialsSummary().catch(() => null),
      ])
      setPrefs(nextPrefs)
      setConnections(nextConnections)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load settings.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  async function update(patch: Partial<Preferences>) {
    try {
      setPrefs(await savePreferences(patch))
      toast.add({ title: 'Settings saved.', type: 'success' })
    } catch (e) {
      toast.add({
        title: e instanceof Error ? e.message : 'Could not save settings.',
        type: 'error',
      })
    }
  }

  async function runReset() {
    setConfirmReset(false)
    try {
      await resetPaperSession()
      toast.add({
        title: 'Paper session reset.',
        description: 'A final snapshot was recorded first.',
        type: 'success',
      })
    } catch (e) {
      toast.add({
        title: e instanceof Error ? e.message : 'Paper reset failed.',
        type: 'error',
      })
    }
  }

  async function updateNotification(key: string, enabled: boolean) {
    if (enabled) {
      const permission = await requestBrowserNotificationPermission()
      setNotificationState(permission)
      if (permission !== 'granted') return
    }
    await update({
      notification_preferences: {
        ...(prefs?.notification_preferences ?? {}),
        [key]: enabled,
      },
    })
  }

  return (
    <div className="nova-signals nova-settings">
      <header className="nova-signals-head">
        <div>
          <h1>Settings</h1>
          <p>Account, trading preferences, notifications, security and data controls.</p>
        </div>
      </header>

      {loading ? (
        <PageSkeleton label="Loading settings" variant="two-column" />
      ) : error && !prefs ? (
        <p className="nova-signals-state" role="alert">
          <AlertTriangle size={16} /> {error}
          <Button variant="unstyled" type="button" className="conv-pill" onClick={() => void load()}>Retry</Button>
        </p>
      ) : !prefs ? null : (
        <>
          {error ? <p className="nova-signals-state" role="alert"><AlertTriangle size={16} /> {error}</p> : null}

          <div className="nova-settings-grid">
            <div className="nova-settings-column">
              <section className="nova-hooks-card nova-settings-card" aria-label="Profile and plan">
                <div className="nova-hooks-card-head"><strong>Profile &amp; Plan</strong></div>
                <div className="nova-profile">
                  {user?.picture_url ? <img src={user.picture_url} alt="" /> : <span>{(user?.name || user?.email || 'N').slice(0, 2).toUpperCase()}</span>}
                  <div><strong>{user?.name || 'NOVA user'}</strong><small>{user?.email || 'Signed in with Google'}</small></div>
                  <Badge variant="unstyled" className="nova-verified">VERIFIED</Badge>
                </div>
                <div className="nova-setting-row"><span>Account</span><strong>Google account</strong></div>
                <div className="nova-setting-row"><span>Plan</span><strong>Current NOVA access</strong></div>
                <div className="nova-setting-row"><span>Timezone</span><strong>{prefs.timezone}</strong></div>
                {onLogout ? <Button variant="unstyled" className="conv-pill" onClick={onLogout}>Log out</Button> : null}
              </section>

              <section className="nova-hooks-card nova-settings-card" aria-label="Trading preferences">
                <div className="nova-hooks-card-head"><strong>Trading Preferences</strong></div>
                <div className="nova-set-grid">
              <label htmlFor="pref-tz">Timezone
                <NativeSelect variant="unstyled" id="pref-tz" value={prefs.timezone} onChange={(e) => void update({ timezone: e.target.value })}>
                  {TIMEZONES.map((tz) => <option key={tz} value={tz}>{tz}</option>)}
                </NativeSelect>
              </label>
              <label htmlFor="pref-density">Table density
                <NativeSelect variant="unstyled" id="pref-density" value={prefs.table_density} onChange={(e) => void update({ table_density: e.target.value })}>
                  {TABLE_DENSITIES.map((d) => <option key={d} value={d}>{d}</option>)}
                </NativeSelect>
              </label>
              <label htmlFor="pref-timeframe">Default chart timeframe
                <NativeSelect variant="unstyled" id="pref-timeframe" value={prefs.default_chart_timeframe} onChange={(e) => void update({ default_chart_timeframe: e.target.value })}>
                  {CHART_TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
                </NativeSelect>
              </label>
            </div>
              </section>

              <section className="nova-hooks-card nova-settings-card" aria-label="Display preferences">
                <div className="nova-hooks-card-head"><strong>Display Preferences</strong></div>
                <label htmlFor="pref-motion" className="nova-setting-row nova-switch-row">
                  <span><strong>Reduce motion</strong><small>Limit non-essential interface animation.</small></span>
                  <Switch id="pref-motion" aria-label="Reduced motion" checked={prefs.reduced_motion} onCheckedChange={(checked) => void update({ reduced_motion: checked })} />
                </label>
              </section>

              <section className="nova-hooks-card nova-settings-card" aria-label="Data and exports">
                <div className="nova-hooks-card-head"><strong>Data &amp; Exports</strong></div>
                <div className="nova-setting-row">
                  <span><strong>Trade history</strong><small>Owner-scoped Paper trades in CSV format.</small></span>
                  <Button variant="unstyled" className="conv-pill" onClick={exportTrades}><Download size={14} /> Export</Button>
                </div>
              </section>
            </div>

            <div className="nova-settings-column">
              <section className="nova-hooks-card nova-settings-card" aria-label="Connections and security">
                <div className="nova-hooks-card-head"><strong>Connections &amp; Security</strong></div>
                <div className="nova-setting-row"><span>Dhan</span><strong>{connections?.broker.connected ? 'Connected' : connections?.broker.connection_status?.replaceAll('_', ' ') || 'Not connected'}</strong></div>
                {connections?.broker.client_id_masked ? <div className="nova-setting-row"><span>Dhan Client ID</span><code>{connections.broker.client_id_masked}</code></div> : null}
                {connections?.static_ip.available ? <div className="nova-setting-row"><span>NOVA Static IP</span><code>{connections.static_ip.ip}</code></div> : null}
                <div className="nova-cred-actions">
                  <Button variant="unstyled" className="conv-pill" onClick={() => onNavigate?.('credentials')}>Manage credentials</Button>
                </div>
              </section>

          <section className="nova-hooks-card nova-settings-card" aria-label="Notifications">
            <div className="nova-hooks-card-head"><strong>Notifications</strong></div>
            <p>
              Browser permission: <strong>{notificationState}</strong>. Notifications open the
              matching event in Trading and never alter engine behavior.
            </p>
            {prefs.channels.map((channel) => (
              <label key={channel.key} className="nova-setting-row nova-switch-row">
                <span><strong>{channel.label}</strong><small>{channel.reason}</small></span>
                <Switch checked={Boolean(prefs.notification_preferences[channel.key])} onCheckedChange={(checked) => void updateNotification(channel.key, checked)} />
              </label>
            ))}
          </section>

          <section className="nova-hooks-card nova-settings-card nova-danger-zone" aria-label="Danger zone">
            <div className="nova-hooks-card-head"><strong>Danger Zone</strong></div>
            <p>
              Resets the simulated balance and clears the Paper session after recording a
              final snapshot. The server refuses while the engine is running, a Paper
              position is open, or an exit is pending. Live credentials and broker data
              are never touched.
            </p>
            <div className="nova-cred-actions">
              <Button variant="unstyled" type="button" className="conv-pill nova-cred-danger" onClick={() => setConfirmReset(true)}>
                Reset Paper account
              </Button>
            </div>
            <AlertDialog open={confirmReset} onOpenChange={setConfirmReset}>
              <AlertDialogContent className="border border-border bg-popover shadow-2xl">
                <AlertDialogHeader>
                  <AlertDialogTitle>Reset Paper account?</AlertDialogTitle>
                  <AlertDialogDescription>
                    This clears your Paper trades and balance. It cannot be undone.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel variant="unstyled" className="conv-pill">Cancel</AlertDialogCancel>
                  <AlertDialogAction variant="unstyled" className="conv-pill nova-cred-danger" onClick={() => void runReset()}>
                    Yes, reset Paper
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </section>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
