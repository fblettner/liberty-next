// KPI arithmetic (no React): the value's format and the comparison with the previous period.

/** Format a KPI value: `percent` shows a ratio as %, `currency` as euros, else a plain number. */
export function formatKpi(v: number, fmt?: string, lang?: string): string {
  if (fmt === 'percent') return `${(v * 100).toLocaleString(lang, { maximumFractionDigits: 1 })}%`
  if (fmt === 'currency') return v.toLocaleString(lang, { style: 'currency', currency: 'EUR' })
  if (Number.isInteger(v)) return v.toLocaleString(lang)
  return v.toLocaleString(lang, { maximumFractionDigits: 3 })
}

export interface KpiDelta {
  direction: 'up' | 'down' | 'flat'
  /** The change as the KPI shows it: "+12.5%" for amounts and counts, "+2.3 pts" for a percent. */
  text: string
}

/** The change from *previous* to *value*. A percent KPI compares in points; anything else in
 *  percent of the previous value, or just the difference when the previous value is zero.
 *  Null when there is nothing to compare with. */
export function kpiDelta(value: number | null, previous: number | null, fmt?: string, lang?: string): KpiDelta | null {
  if (value === null || previous === null) return null
  const diff = value - previous
  const direction: KpiDelta['direction'] = Math.abs(diff) < 1e-9 ? 'flat' : diff > 0 ? 'up' : 'down'
  const sign = diff > 0 ? '+' : diff < 0 ? '−' : ''
  if (fmt === 'percent') return { direction, text: `${sign}${(Math.abs(diff) * 100).toLocaleString(lang, { maximumFractionDigits: 1 })} pts` }
  if (previous === 0) return { direction, text: `${sign}${formatKpi(Math.abs(diff), fmt, lang)}` }
  return { direction, text: `${sign}${(Math.abs(diff) / Math.abs(previous) * 100).toLocaleString(lang, { maximumFractionDigits: 1 })}%` }
}
