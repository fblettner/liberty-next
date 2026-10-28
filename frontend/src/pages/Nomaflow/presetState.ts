// The run dialog's state after picking a preset. A preset is stored as changes from the job's
// SAVED values (jobs.toml), and that is how the scheduler and the user's page apply it — so the
// dialog starts from the saved values too, never from whatever the previous pick left ticked
// (picking "Security" then "Licences" must not keep Security's switches). No React here.
import type { JobPreset } from './types'

export interface RunState {
  params: Record<string, unknown>
  stepValues: Record<string, Record<string, unknown>>
  stepEnabled: Record<string, boolean>
  logLevel: 'WARNING' | 'INFO' | 'DEBUG'
}

/** *saved* = the job's own values; *preset* = null to go back to them. */
export function applyPresetState(saved: RunState, preset: JobPreset | null | undefined): RunState {
  const stepValues = Object.fromEntries(Object.entries(saved.stepValues).map(([s, kw]) => [s, { ...kw }]))
  if (!preset) return { params: { ...saved.params }, stepValues, stepEnabled: { ...saved.stepEnabled }, logLevel: saved.logLevel }
  for (const [s, kw] of Object.entries(preset.op_kwargs ?? {})) stepValues[s] = { ...(stepValues[s] ?? {}), ...kw }
  return {
    params: { ...saved.params, ...(preset.params ?? {}) },
    stepValues,
    stepEnabled: { ...saved.stepEnabled, ...(preset.step_enabled ?? {}) },
    logLevel: preset.log_level ?? saved.logLevel,
  }
}
