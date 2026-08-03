import { backendHttpUrl } from '../lib/backend'

// One save for both halves of a configuration. Saving strategy setup and risk
// settings separately could leave them mismatched, so the UI never does that.
export interface ConfigurationState {
  revision: number
  settings_revision: number
  setup_revision: number
  coherent: boolean
  complete: boolean
}

export interface SavedConfiguration {
  configuration_revision: number
  setup: Record<string, unknown>
  risk: Record<string, unknown>
}

export class ConfigurationConflictError extends Error {
  currentRevision: number
  constructor(message: string, currentRevision: number) {
    super(message)
    this.name = 'ConfigurationConflictError'
    this.currentRevision = currentRevision
  }
}

export async function getConfigurationState(mode = 'paper'): Promise<ConfigurationState> {
  const response = await fetch(backendHttpUrl(`/api/setup/configuration?mode=${mode}`), {
    credentials: 'include',
    cache: 'no-store',
  })
  if (!response.ok) throw new Error(`Could not read the configuration revision: ${response.status}`)
  return response.json() as Promise<ConfigurationState>
}

export async function saveConfiguration(args: {
  strategyKey: string
  mode: string
  setup: Record<string, string | number>
  risk: Record<string, string | number>
  expectedRevision: number | null
}): Promise<SavedConfiguration> {
  const response = await fetch(backendHttpUrl('/api/setup/configuration'), {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    // Owner identity comes from the session, never from here.
    body: JSON.stringify({
      strategy_key: args.strategyKey,
      mode: args.mode,
      setup: args.setup,
      risk: args.risk,
      ...(args.expectedRevision === null ? {} : { expected_revision: args.expectedRevision }),
    }),
  })
  const body = await response.json().catch(() => ({})) as Record<string, unknown>
  if (response.status === 409) {
    throw new ConfigurationConflictError(
      String(body.error ?? 'This configuration was changed elsewhere. Reload before saving again.'),
      Number(body.current_revision ?? 0),
    )
  }
  if (!response.ok) {
    throw new Error(String(body.error ?? `Configuration could not be saved: ${response.status}`))
  }
  return body as unknown as SavedConfiguration
}
