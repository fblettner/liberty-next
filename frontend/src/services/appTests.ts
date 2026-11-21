// The tests of an application (GET / PUT /admin/tests/<suite>): their shapes, and the small conversions the
// editor needs. No React here.
import { formatPairs, parsePairs } from './pairs'

export interface TestExpect {
  success?: boolean; error_contains?: string | null; warning_contains?: string | null
  rows?: number | null; rows_min?: number | null; rows_max?: number | null
  first_row?: Record<string, unknown>; equals?: Record<string, unknown>; contains?: Record<string, string>; present?: string[]; absent?: string[]
}
export interface TestStep {
  name: string; endpoint: string; params?: Record<string, unknown>; rows?: string | null
  capture?: Record<string, string>; keep?: string[]; expect?: TestExpect; always?: boolean
}
export interface TestCase { id: string; label?: string | null; writes?: boolean; connector?: string | null; steps: TestStep[] }
export interface TestSuite { connector?: string | null; label?: string | null; case: TestCase[] }
export interface StepRun { step: string; endpoint: string; status: string; duration_ms: number; problems: string[] }
export interface CaseRun { at: string; mode: string; who: string | null; status: string; steps: StepRun[] }

/** A dictionary as `name = value` lines (what the pairs field edits). */
export function pairsText(obj: Record<string, unknown> | null | undefined): string {
  return formatPairs(Object.entries(obj ?? {}).map(([name, v]) => ({ name, value: v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v) })))
}
/** The lines back as a dictionary — undefined when there is none (the key is then left out of the file). */
export function pairsObject(text: string | null | undefined): Record<string, string> | undefined {
  const pairs = parsePairs(text) ?? []
  return pairs.length ? Object.fromEntries(pairs.map((p) => [p.name, p.value])) : undefined
}
export const linesText = (list: string[] | null | undefined): string => (list ?? []).join('\n')
export function linesList(text: string): string[] | undefined {
  const out = text.split(/\r?\n/).map((x) => x.trim()).filter(Boolean)
  return out.length ? out : undefined
}

/** What a step expects, in a few words — the line on its card. */
export function expectSummary(step: TestStep): string {
  const e = step.expect ?? {}
  if (e.success === false) return e.error_contains ? `refused (${e.error_contains})` : 'refused'
  const parts: string[] = []
  if (e.rows != null) parts.push(`${e.rows} row(s)`)
  if (e.rows_min != null) parts.push(`≥ ${e.rows_min} row(s)`)
  if (e.rows_max != null) parts.push(`≤ ${e.rows_max} row(s)`)
  const checked = Object.keys(e.first_row ?? {}).length + Object.keys(e.equals ?? {}).length + Object.keys(e.contains ?? {}).length + (e.present ?? []).length + (e.absent ?? []).length
  if (checked) parts.push(`${checked} value(s)`)
  return parts.length ? parts.join(' · ') : 'works'
}

/** A name no step of the case has yet. */
export function freshStepName(steps: TestStep[], base = 'step'): string {
  const taken = new Set(steps.map((s) => s.name))
  for (let n = steps.length + 1; ; n++) if (!taken.has(`${base} ${n}`)) return `${base} ${n}`
}

/** *step* without its empty parts, as the file keeps it. */
export function tidyStep(step: TestStep): TestStep {
  const expect = Object.fromEntries(Object.entries(step.expect ?? {}).filter(([k, v]) => !(v == null || v === '' || (k === 'success' && v === true)
    || (Array.isArray(v) && v.length === 0) || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v as object).length === 0)))) as TestExpect
  const out: TestStep = { name: step.name, endpoint: step.endpoint }
  if (step.params && Object.keys(step.params).length) out.params = step.params
  if (step.rows) out.rows = step.rows
  if (step.capture && Object.keys(step.capture).length) out.capture = step.capture
  if (step.keep?.length) out.keep = step.keep
  if (Object.keys(expect).length) out.expect = expect
  if (step.always) out.always = true
  return out
}
