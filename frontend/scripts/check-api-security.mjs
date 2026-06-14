import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const backend = readFileSync(resolve(root, 'src/lib/backend.ts'), 'utf8')
const api = readFileSync(resolve(root, 'src/api.ts'), 'utf8')
const ws = readFileSync(resolve(root, 'src/ws.ts'), 'utf8')

const checks = [
  [backend.includes("credentials: 'include'"), 'apiFetch must include browser credentials'],
  [backend.includes("'X-CSRF-Token'"), 'apiFetch must attach the CSRF header'],
  [api.includes('apiFetch('), 'REST API calls must use apiFetch'],
  [ws.includes('apiFetch('), 'websocket reconnect checks must use apiFetch'],
  [!api.includes('fetch('), 'api.ts must not bypass apiFetch'],
  [!ws.includes('fetch('), 'ws.ts must not bypass apiFetch'],
  [!backend.includes('console.log'), 'backend request wrapper must not log tokens'],
]

const failures = checks.filter(([ok]) => !ok).map(([, message]) => message)
if (failures.length) {
  console.error(failures.join('\n'))
  process.exit(1)
}

console.log('Frontend API security checks passed.')
