// A report template's parameter beyond its name and type: the value it starts with, the help
// shown under it, and where the choices of its dropdown come from. Mirrors
// liberty/reports/schema.py (ReportParam / ReportParamOptions). No React here.
import { formatPairs } from './pairs'

export type ChoiceKind = 'static' | 'connectors' | 'schemas' | 'query'
export const CHOICE_KINDS: ChoiceKind[] = ['static', 'connectors', 'schemas', 'query']

export interface ParamChoices {
  kind: ChoiceKind
  values?: { value: unknown; label: string }[]
  connector_param?: string | null
  connector?: string | null
  query?: string | null
  value_column?: string | null
  label_column?: string | null
}

/** A fixed list as the lines the editor shows: ``value = label``, one per line. */
export function choicesText(values: { value: unknown; label: string }[] | undefined): string {
  return formatPairs((values ?? []).map((v) => ({ name: v.value == null ? '' : String(v.value), value: v.label ?? '' })))
}

/** The lines back as a fixed list; a line without a label shows its value. */
export function choicesList(text: string): { value: string; label: string }[] {
  const out: { value: string; label: string }[] = []
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue
    const at = line.indexOf('=')
    const value = (at < 0 ? line : line.slice(0, at)).trim()
    if (value) out.push({ value, label: (at < 0 ? '' : line.slice(at + 1).trim()) || value })
  }
  return out
}

/** The choices as they are saved: only what their kind uses, nothing blank; null when the
 *  parameter is a plain field again. */
export function cleanChoices(choices: ParamChoices | null | undefined): ParamChoices | null {
  if (!choices || !CHOICE_KINDS.includes(choices.kind)) return null
  const text = (v: string | null | undefined) => (v ?? '').trim()
  if (choices.kind === 'static') {
    const values = (choices.values ?? []).filter((v) => v.value != null && String(v.value).trim() !== '')
    return values.length ? { kind: 'static', values } : null
  }
  if (choices.kind === 'connectors') return { kind: 'connectors' }
  const from: ParamChoices = { kind: choices.kind }
  if (text(choices.connector_param)) from.connector_param = text(choices.connector_param)
  else if (text(choices.connector)) from.connector = text(choices.connector)
  if (choices.kind === 'query') {
    if (text(choices.query)) from.query = text(choices.query)
    if (text(choices.value_column)) from.value_column = text(choices.value_column)
    if (text(choices.label_column)) from.label_column = text(choices.label_column)
  }
  return from
}

/** What stops a parameter's choices from working, as a short code; null when they are complete. */
export function choicesProblem(choices: ParamChoices | null | undefined): 'connector' | 'query' | 'columns' | null {
  if (!choices || choices.kind === 'static' || choices.kind === 'connectors') return null
  if (!(choices.connector_param ?? '').trim() && !(choices.connector ?? '').trim()) return 'connector'
  if (choices.kind === 'query') {
    if (!(choices.query ?? '').trim()) return 'query'
    if (!(choices.value_column ?? '').trim()) return 'columns'
  }
  return null
}

/** The settings a report's PDF may override, by the name the renderer knows them under. */
export const PDF_OPTION_NAMES = [
  'title', 'subtitle', 'client', 'author', 'date', 'version', 'confidential', 'cover_eyebrow', 'cover_brand',
  'cover_ref', 'header_left', 'header_right', 'footer_left', 'primary_color', 'primary_color_light',
] as const
