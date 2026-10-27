// The synthesis widgets' plain logic (stat / list / bars): a figure formatted the way the card
// shows it, a sub line templated over a row, a tone mapped to a colour. No React.
import type { QueryResult } from '../types/connectors'
import type { ValueFormat } from '../types/dashboards'
import { resolveColumnName, toNumber } from './chartData'

export type Tone = 'ok' | 'warn' | 'error' | 'info' | 'muted'

/** `integer` / `number` / `currency` (euros, no decimals when whole) / `percent` (a 0–1 ratio),
 *  the unit after; text as is; null → ''. */
export function formatValue(v: unknown, fmt?: ValueFormat | null, unit?: string | null, lang?: string): string {
  const n = toNumber(v)
  if (n === null) return v === null || v === undefined ? '' : String(v)
  let out: string
  if (fmt === 'percent') out = `${(n * 100).toLocaleString(lang, { maximumFractionDigits: 1 })}\u00a0%`
  else if (fmt === 'currency') out = n.toLocaleString(lang, { style: 'currency', currency: 'EUR', maximumFractionDigits: Number.isInteger(n) ? 0 : 2 })
  else if (fmt === 'integer') out = n.toLocaleString(lang, { maximumFractionDigits: 0 })
  else out = n.toLocaleString(lang, { maximumFractionDigits: 2 })
  return unit && fmt !== 'currency' ? `${out}\u00a0${unit}` : out
}

const PLACEHOLDER = /\{([A-Za-z0-9_]+)(?::([a-z]+))?\}/g

/** `{COLUMN}` / `{COLUMN:format}` placeholders filled from *row* (column names resolved to the
 *  result's spelling); an unknown column stays as written. */
export function fillTemplate(template: string, row: Record<string, unknown>, result: QueryResult, lang?: string): string {
  return template.replace(PLACEHOLDER, (whole, name: string, fmt?: string) => {
    const col = resolveColumnName(result, name)
    if (!(col in row)) return whole
    return formatValue(row[col], (fmt as ValueFormat | undefined) ?? null, null, lang)
  })
}

/** The tone a cell carries: ok / warn / error / info, anything else muted. */
export function toneOf(v: unknown): Tone {
  const s = String(v ?? '').trim().toLowerCase()
  return s === 'ok' || s === 'warn' || s === 'error' || s === 'info' ? s : 'muted'
}

/** The bar widths of a ranked list, in percent of the total or of the largest value. */
export function barShares(values: (number | null)[], share: 'total' | 'max'): number[] {
  const nums = values.map((v) => (v === null || v < 0 ? 0 : v))
  const base = share === 'total' ? nums.reduce((a, b) => a + b, 0) : Math.max(0, ...nums)
  return nums.map((v) => (base > 0 ? (v / base) * 100 : 0))
}
