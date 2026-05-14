// Phase 8 slice 1 — chart widget for the TableView.
//
// Renders a result set as a bar / line / area / pie chart, with an inline spec editor above
// (Type · X column · Y column(s) · Aggregation). The spec is persisted to localStorage per
// `(connector, query)` — a later slice will lift it into `config/charts.toml` for shared,
// versioned chart specs (Phase 8 slice 2).
//
// Recharts is the underlying lib — declarative, theme-friendly via CSS vars. We pull series
// colours from the theme palette (blue / green / orange / purple / red / yellow) so light/dark
// theme swapping just works through the shared --*-main CSS vars.
import { useCallback, useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend,
  Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip,
  XAxis, YAxis,
} from 'recharts'
import type { TooltipContentProps, TooltipPayloadEntry, TooltipValueType } from 'recharts'
import { Field, SearchSelect, type SearchSelectOption } from '../../common'
import { buildChartData, isNumericColumn } from '../../services/chartData'
import { cellText, enumMap, ruleCell } from '../../services/cells'
import { useLookupBatch, type LookupSpec } from '../../services/lookups'
import type { Column, QueryResult } from '../../types/connectors'
import type { Aggregation, ChartSpec, ChartType } from '../../types/charts'
import { AGGREGATIONS, CHART_TYPES, defaultChartSpec } from '../../types/charts'
import { colors, fontSize, fonts, radius, shadow } from '../../theme'

const Frame = styled.div`
  display: flex; flex-direction: column; flex: 1; min-height: 0; gap: 12px;
`
const SpecBar = styled.div`
  display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end;
`
const SpecCell = styled.div`min-width: 140px;`
const ChartFrame = styled.div`
  flex: 1; min-height: 0; min-width: 0; border: 1px solid ${colors.border};
  border-radius: 8px; padding: 12px; background: ${colors.bg.input};
`
const EmptyHint = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 8px 4px;`

// Themed tooltip — replaces Recharts' default white-on-anything box with a frame that
// inherits the app's theme tokens (bg-modal / text-primary / border-radius / shadow). Renders the
// X label + one row per series (coloured dot + the series' display label + the value).
const TooltipBox = styled.div`
  background: var(--bg-modal); border: 1px solid ${colors.border}; border-radius: ${radius.md};
  padding: 8px 10px; box-shadow: ${shadow.md}; min-width: 120px;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
`
const TooltipHead = styled.div`
  color: ${colors.text.muted}; font-size: ${fontSize.micro}; text-transform: uppercase;
  letter-spacing: 0.04em; margin-bottom: 4px; font-family: ${fonts.mono};
`
const TooltipRow = styled.div`display: flex; align-items: center; gap: 8px; margin: 2px 0;`
const TooltipSwatch = styled.span<{ $color: string }>`
  display: inline-block; width: 9px; height: 9px; border-radius: 2px; background: ${({ $color }) => $color}; flex-shrink: 0;
`
const TooltipLabel = styled.span`color: ${colors.text.secondary}; flex: 1;`
const TooltipValue = styled.span`color: ${colors.text.primary}; font-variant-numeric: tabular-nums; font-family: ${fonts.mono};`

// Series palette — cycled when there are more Y columns than themed colours.
const SERIES_COLORS = [
  colors.blue.main, colors.green.main, colors.orange.main, colors.purple.main,
  colors.red.main, colors.yellow.main,
]

const STORAGE_PREFIX = 'liberty:chart-spec:'

export interface ChartViewProps {
  result: QueryResult
  connector: string
  query: string
}

export function ChartView({ result, connector, query }: ChartViewProps) {
  const { t } = useTranslation()
  const allCols = useMemo(() => result.columns.filter((c) => !c.hidden), [result])
  const numericCols = useMemo(() => allCols.filter(isNumericColumn), [allCols])

  // Persist the spec per `(connector, query)` — same shape `config/charts.toml` will adopt.
  const storageKey = `${STORAGE_PREFIX}${connector}/${query}`
  const initialSpec = useMemo(() => loadSpec(storageKey) ?? seedSpec(allCols, numericCols), [storageKey, allCols, numericCols])
  // Component state held in localStorage indirectly: a `setSpec` writes through.
  // (A plain `useState(initialSpec)` with `useEffect` to write would also work; this is terser.)
  const [spec, setSpec] = useStoredSpec(storageKey, initialSpec)

  // Repair stale Y columns (someone changed the query → an old Y col no longer exists). Strip
  // missing ones; if all gone, reseed from the new result.
  const cleanSpec: ChartSpec = useMemo(() => {
    const colNames = new Set(allCols.map((c) => c.name))
    const y = spec.y.filter((n) => colNames.has(n))
    const x = colNames.has(spec.x) ? spec.x : (allCols[0]?.name ?? '')
    if (y.length === 0 && numericCols.length > 0) {
      return { ...spec, x, y: [numericCols[0].name] }
    }
    return { ...spec, x, y }
  }, [spec, allCols, numericCols])

  const data = useMemo(() => buildChartData(result, cleanSpec), [result, cleanSpec])

  const typeOpts: SearchSelectOption[] = CHART_TYPES.map((c) => ({ value: c, label: t(`chart.type.${c}`) }))
  const aggOpts: SearchSelectOption[] = AGGREGATIONS.map((a) => ({ value: a, label: t(`chart.agg.${a}`) }))
  const colOpts: SearchSelectOption[] = allCols.map((c) => ({ value: c.name, label: c.label ?? c.name }))

  const showLegend = cleanSpec.showLegend ?? cleanSpec.y.length > 1

  // ── display-rule resolution: turn raw cell values into the same labels the TableView shows ──
  // The X column may carry a BOOLEAN/ENUM/LOOKUP rule (resolved server-side from the dictionary);
  // LOOKUP needs an async fetch of the lookup table. Fetched once per (connector, query) via the
  // shared session cache (services/lookups.ts) — multiple charts on the same column share the round-trip.
  const xCol = useMemo(() => allCols.find((c) => c.name === cleanSpec.x), [allCols, cleanSpec.x])
  const lookupSpecs: LookupSpec[] = useMemo(() => {
    if (xCol?.rule?.kind !== 'lookup') return []
    const r = xCol.rule
    return [{ connector: r.connector || connector, query: r.query, value: r.value, label: r.label, params: r.params }]
  }, [xCol, connector])
  const lookups = useLookupBatch(lookupSpecs)
  const lookupMap: Map<string, string> | undefined = lookups.values().next().value
  const enums: Map<string, string> | undefined = useMemo(
    () => (xCol?.rule?.kind === 'enum' ? enumMap(xCol.rule) : undefined),
    [xCol],
  )
  /** Format a raw X cell as its display label — honours the X column's rule (BOOLEAN/ENUM/LOOKUP).
   *  When the rule is LOOKUP and the fetch hasn't returned, falls back to the raw value (the tick
   *  re-renders once `lookups` is populated). */
  const formatX = useCallback((raw: unknown): string => {
    if (!xCol) return cellText(raw).text
    return ruleCell(raw, xCol, enums, lookupMap).text
  }, [xCol, enums, lookupMap])

  /** A series' display name — the column's `label` (from the dictionary) if set, else its raw name. */
  const seriesName = useCallback((col: Column): string => col.label ?? col.name, [])

  return (
    <Frame>
      <SpecBar>
        <SpecCell>
          <Field label={t('chart.spec.type')}>
            <SearchSelect value={cleanSpec.type} onChange={(v) => setSpec({ ...cleanSpec, type: v as ChartType })} options={typeOpts} />
          </Field>
        </SpecCell>
        <SpecCell>
          <Field label={t('chart.spec.x')}>
            <SearchSelect value={cleanSpec.x} onChange={(v) => setSpec({ ...cleanSpec, x: v })} options={colOpts} placeholder={t('chart.spec.pick')} />
          </Field>
        </SpecCell>
        <SpecCell>
          <Field label={t('chart.spec.y')}>
            <SearchSelect
              value={cleanSpec.y[0] ?? ''}
              onChange={(v) => setSpec({ ...cleanSpec, y: v ? [v, ...cleanSpec.y.slice(1)] : [] })}
              options={colOpts} placeholder={t('chart.spec.pick')}
            />
          </Field>
        </SpecCell>
        <SpecCell>
          <Field label={t('chart.spec.aggregation')}>
            <SearchSelect value={cleanSpec.aggregation} onChange={(v) => setSpec({ ...cleanSpec, aggregation: v as Aggregation })} options={aggOpts} />
          </Field>
        </SpecCell>
      </SpecBar>

      <ChartFrame>
        {!cleanSpec.x || cleanSpec.y.length === 0 ? (
          <EmptyHint>{t('chart.empty.pickCols')}</EmptyHint>
        ) : data.length === 0 ? (
          <EmptyHint>{t('chart.empty.noData')}</EmptyHint>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            {renderChart(cleanSpec, data, allCols, { showLegend, formatX, seriesName })}
          </ResponsiveContainer>
        )}
      </ChartFrame>
    </Frame>
  )
}

/** Custom Tooltip — themed dark/light, replaces Recharts' default white box. The `payload` array
 *  carries one entry per series (`dataKey` = the Y column, `value` = the aggregated number,
 *  `color` = the series' fill); `label` is the raw X value, formatted via `formatLabel`. */
// Recharts' `Tooltip` generics fight any narrower TValue/TName we declare on the content prop —
// keep the defaults and rely on runtime shape (each Y value is a number we already aggregated).
function ThemedTooltip(
  { active, payload, label, formatLabel }:
    TooltipContentProps & { formatLabel: (raw: unknown) => string },
) {
  if (!active || !payload || payload.length === 0) return null
  return (
    <TooltipBox>
      <TooltipHead>{formatLabel(label)}</TooltipHead>
      {(payload as TooltipPayloadEntry<TooltipValueType, string>[]).map((p, i) => (
        <TooltipRow key={i}>
          <TooltipSwatch $color={(p.color as string) ?? colors.blue.main} />
          <TooltipLabel>{p.name ?? String(p.dataKey)}</TooltipLabel>
          <TooltipValue>{formatNumber(p.value)}</TooltipValue>
        </TooltipRow>
      ))}
    </TooltipBox>
  )
}

function formatNumber(v: unknown): string {
  if (v === null || v === undefined) return '∅'
  if (typeof v !== 'number' || !Number.isFinite(v)) return String(v)
  // Integers stay integer; floats get up to 3 decimals (avg/min/max often produce them).
  return Number.isInteger(v) ? v.toString() : v.toLocaleString(undefined, { maximumFractionDigits: 3 })
}

// Recharts' top-level <BarChart> / <LineChart> / etc. each return JSX.Element — we pick one based
// on the spec. The renderers share `formatX` (X tick + tooltip label formatter, honouring the X
// column's display rule) and `seriesName` (legend / tooltip label for each Y column, from the
// dictionary). Animation duration is shortened from Recharts' default 1500 ms — quick enough to
// feel responsive on every spec change.
const ANIMATION_MS = 350

interface RenderOpts {
  showLegend: boolean
  formatX: (raw: unknown) => string
  seriesName: (col: Column) => string
}

function renderChart(
  spec: ChartSpec, data: ReturnType<typeof buildChartData>, allCols: Column[], opts: RenderOpts,
): React.ReactElement {
  const seriesColor = (i: number) => SERIES_COLORS[i % SERIES_COLORS.length]
  const grid = spec.showGrid !== false  // default on
  // Resolve each Y column's display label (falls back to raw name when there's no dictionary entry).
  const yLabels = spec.y.map((y) => {
    const col = allCols.find((c) => c.name === y)
    return col ? opts.seriesName(col) : y
  })
  // `cursor.fill` is the soft highlight that follows the mouse over a bar/area — keep it subtle so
  // it doesn't fight the bars themselves; the `var(--hover-subtle)` follows the rest of the UI.
  // The content function bridges Recharts' tooltip props into our themed component (plain JSX
  // can't satisfy the prop type because Recharts injects the active/payload/label fields at runtime).
  const tooltip = (
    <Tooltip
      cursor={{ fill: 'var(--hover-subtle, rgba(255,255,255,0.06))' }}
      content={(props) => <ThemedTooltip {...props} formatLabel={opts.formatX} />}
    />
  )

  if (spec.type === 'pie') {
    // Pie collapses to a single series — first Y column. The slice label is the formatted X value;
    // the value is the aggregated Y. Stacking / multi-series are bar/line/area concepts.
    const y = spec.y[0]
    return (
      <PieChart>
        {tooltip}
        {opts.showLegend && <Legend verticalAlign="bottom" formatter={() => yLabels[0]} />}
        <Pie data={data} dataKey={y} nameKey="x" name={yLabels[0]} outerRadius="80%"
          label={(entry) => opts.formatX(entry.x)}
          animationDuration={ANIMATION_MS} isAnimationActive={data.length <= 200}>
          {data.map((_, i) => <Cell key={i} fill={seriesColor(i)} />)}
        </Pie>
      </PieChart>
    )
  }
  if (spec.type === 'line') {
    return (
      <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        {grid && <CartesianGrid strokeDasharray="3 3" stroke={colors.border} />}
        <XAxis dataKey="x" stroke={colors.text.muted} tickFormatter={opts.formatX} />
        <YAxis stroke={colors.text.muted} />
        {tooltip}
        {opts.showLegend && <Legend />}
        {spec.y.map((y, i) => (
          <Line key={y} dataKey={y} name={yLabels[i]} stroke={seriesColor(i)} dot={data.length <= 50}
            animationDuration={ANIMATION_MS} isAnimationActive={data.length <= 200} />
        ))}
      </LineChart>
    )
  }
  if (spec.type === 'area') {
    return (
      <AreaChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        {grid && <CartesianGrid strokeDasharray="3 3" stroke={colors.border} />}
        <XAxis dataKey="x" stroke={colors.text.muted} tickFormatter={opts.formatX} />
        <YAxis stroke={colors.text.muted} />
        {tooltip}
        {opts.showLegend && <Legend />}
        {spec.y.map((y, i) => (
          <Area key={y} dataKey={y} name={yLabels[i]} type="monotone" stroke={seriesColor(i)} fill={seriesColor(i)} fillOpacity={0.35}
            stackId={spec.stacked ? 'stack' : undefined}
            animationDuration={ANIMATION_MS} isAnimationActive={data.length <= 200} />
        ))}
      </AreaChart>
    )
  }
  // Default: bar
  return (
    <BarChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
      {grid && <CartesianGrid strokeDasharray="3 3" stroke={colors.border} />}
      <XAxis dataKey="x" stroke={colors.text.muted} tickFormatter={opts.formatX} />
      <YAxis stroke={colors.text.muted} />
      {tooltip}
      {opts.showLegend && <Legend />}
      {spec.y.map((y, i) => (
        <Bar key={y} dataKey={y} name={yLabels[i]} fill={seriesColor(i)}
          stackId={spec.stacked ? 'stack' : undefined}
          animationDuration={ANIMATION_MS} isAnimationActive={data.length <= 200} />
      ))}
    </BarChart>
  )
}

// ── persistence helpers ─────────────────────────────────────────────────────────────

function loadSpec(storageKey: string): ChartSpec | null {
  try {
    const raw = window.localStorage.getItem(storageKey)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null
    return parsed as ChartSpec
  } catch {
    return null
  }
}

function saveSpec(storageKey: string, spec: ChartSpec): void {
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(spec))
  } catch {
    // localStorage full / disabled — silently drop, the chart still works in-memory.
  }
}

/** A small state wrapper that mirrors `useState`'s shape but writes through to localStorage on
 *  every update. Reads happen via the initial value (caller does the `loadSpec` itself so the
 *  initial render is hydrated). When `storageKey` changes (operator switches tabs / connectors),
 *  the next stored spec is rehydrated. */
function useStoredSpec(storageKey: string, initial: ChartSpec): [ChartSpec, (next: ChartSpec) => void] {
  const [spec, setSpec] = useState<ChartSpec>(initial)
  useEffect(() => {
    const loaded = loadSpec(storageKey)
    if (loaded) setSpec(loaded)
    // No `else` — falling back to whatever the caller seeded keeps the chart populated when
    // a new connector/query has no saved spec yet.
  }, [storageKey])
  return [spec, (next) => { setSpec(next); saveSpec(storageKey, next) }]
}

/** Seed: first non-hidden column is X, first numeric column is Y. Falls back to the first
 *  column for Y when nothing parses as numeric (count aggregation will still produce a chart). */
function seedSpec(allCols: Column[], numericCols: Column[]): ChartSpec {
  const base = defaultChartSpec()
  const x = allCols[0]?.name ?? ''
  const y = (numericCols[0] ?? allCols[1] ?? allCols[0])?.name
  return { ...base, x, y: y ? [y] : [] }
}
