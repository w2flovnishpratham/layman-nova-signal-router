import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const app = read('src/App.tsx')
const banner = read('src/components/SafetyBanner.tsx')
const setup = read('src/components/setup/SetupPanel.tsx')
const dialog = read('src/components/ConfirmationDialog.tsx')
const header = read('src/components/Header.tsx')
const sidebar = read('src/components/EngineSidebar.tsx')
const readiness = read('src/components/SafetyReadiness.tsx')
const store = read('src/state/sessionStore.ts')
const setupInfo = read('src/components/messages/SetupInfoCard.tsx')
const strategyPlatform = read('src/components/StrategyPlatform.tsx')
const api = read('src/api.ts')
const livePilot = read('src/components/LivePilotPanel.tsx')

const checks = [
  [banner.includes('Paper mode - no real orders'), 'persistent banner must identify paper as non-real'],
  [banner.includes('LIVE MODE - real broker orders can be placed'), 'persistent banner must warn about real live orders'],
  [banner.includes('UNKNOWN / DISCONNECTED'), 'persistent banner must show disconnected status'],
  [banner.includes('Do not trust live status until reconnect'), 'disconnected state must warn users'],
  [setup.includes('START LIVE WITH REAL MONEY'), 'Live launch must require the typed confirmation phrase'],
  [setup.includes('single_operator_live_allowed'), 'Live launch must consume backend safety policy'],
  [setup.includes('disabled={pending || (isLive && !liveAllowed)}'), 'Live launch must be disabled when blocked or pending'],
  [dialog.includes('confirmation === confirmPhrase'), 'typed confirmation must match exactly'],
  [header.includes('confirmPhrase="PANIC EXIT"'), 'stop and square-off must require destructive confirmation'],
  [header.includes('confirmPhrase="RESET"'), 'reconfigure must require typed reset confirmation'],
  [sidebar.includes('confirmPhrase="PANIC EXIT"'), 'open-position exit must require typed confirmation'],
  [app.includes('pendingActionKeysRef.current.has(actionKey)'), 'pending action lock must block duplicate command keys'],
  [app.includes("wsStatus !== 'live'"), 'commands must be blocked while disconnected'],
  [readiness.includes('Signing relay configured'), 'Live checklist must show signing relay readiness'],
  [readiness.includes('Direct unsigned TradingView alerts are not live-safe'), 'webhook trust UI must explain direct TradingView limitation'],
  [readiness.includes('access_token_present'), 'broker trust UI must show token presence without its value'],
  [store.includes("accessToken: ''"), 'saved access tokens must be removed from frontend draft state'],
  [!setupInfo.includes('info.webhookSecret}</code>'), 'setup messages must not render the raw webhook secret'],
  [!sidebar.includes('session?.webhookSecret ??'), 'trading sidebar must not expose or copy the raw webhook secret'],
  [strategyPlatform.includes('Start Paper'), 'strategy marketplace must provide Paper activation'],
  [strategyPlatform.includes('Live execution is locked until executor IP and signing relay are verified.'), 'strategy marketplace must explain why Live is locked'],
  [strategyPlatform.includes('className="live-lock-button" disabled'), 'Live strategy control must remain disabled'],
  [strategyPlatform.includes('Free plan allows'), 'free strategy limit message must be visible'],
  [strategyPlatform.includes('reasonMessage'), 'signal history must render stored skip/fill reasons'],
  [strategyPlatform.includes('confirmPhrase="REMOVE"'), 'strategy removal must require typed confirmation'],
  [api.includes("mode: 'paper'"), 'strategy activation API must explicitly request Paper mode'],
  [api.includes("mode: 'live'"), 'controlled pilot activation API must explicitly request Live mode'],
  [livePilot.includes("disabled={!readiness.ready || pending !== ''}"), 'Live pilot activation must require backend readiness'],
  [livePilot.includes('Start verified dry run'), 'Live pilot UI must identify the dry-run action'],
  [livePilot.includes('Reserved IP verified'), 'Live pilot UI must show executor egress verification'],
  [!livePilot.toLowerCase().includes('access_token'), 'Live pilot UI must not render broker access tokens'],
  [!strategyPlatform.toLowerCase().includes('access_token'), 'strategy UI must not render broker token fields'],
]

const failures = checks.filter(([ok]) => !ok).map(([, message]) => message)
if (failures.length) {
  console.error(failures.join('\n'))
  process.exit(1)
}

console.log('Frontend UI safety checks passed.')
