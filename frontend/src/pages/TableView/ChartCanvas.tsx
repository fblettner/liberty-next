// ChartCanvas — the rendering half of the chart pipeline (no spec editor, no Save modal).
// Shared by:
//   - ChartView (the TableView's chart toggle — wraps this with an editable spec bar)
//   - DashboardView's ChartWidget (just renders, no editing in slice 3a)
//
// Takes a built QueryResult + a finalised ChartSpec + the connector name, and renders:
//   - Display-rule-aware X-axis ticks (BOOLEAN / ENUM / LOOKUP via the dictionary)
//   - Series labels from the column dictionary (not raw names)
//   - A themed dark/light tooltip (no Recharts default white box)
//   - Snappy animation (350 ms; disabled past 200 datapoints)
//
// The renderChart function (the per-type chart picker) lives here too so it stays next to the
// helpers it depends on. ChartView's `renderChart` (used by its preview path) re-imports.
import { useCallback, useId, useMemo } from 'react'
import styled from '@emotion/styled'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend,
  Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip,
  XAxis, YAxis,
} from 'recharts'
import type { TooltipContentProps, TooltipPayloadEntry, TooltipValueType } from 'recharts'
import { buildChartData, resolveColumnName } from '../../services/chartData'
import { cellText, enumMap, ruleCell } from '../../services/cells'
import { useLookupBatch, type LookupSpec } from '../../services/lookups'
import type { Column, QueryResult } from '../../types/connectors'
import type { ChartSpec } from '../../types/charts'
import { colors, fontSize, fonts, glass, radius, shadow } from '../../theme'

// Liquid-glass chart canvas — same vocabulary as the KPI card so the dashboard reads as one
// floating set of frosted panels. The backdrop blur + inset highlight + soft shadow give the
// card the macOS-style depth; the subtle radial gradient (top-left → fade) adds a hint of
// directional light without painting a full gradient layer.
const Frame = styled.div`
  flex: 1; min-height: 220px; min-width: 0; border: 1px solid ${colors.border};
  border-radius: ${radius.lg}; padding: 14px; background: ${colors.bg.card};
  ${glass.surface}
  box-shadow: ${shadow.sm}, inset 0 1px 0 rgba(255, 255, 255, 0.08);
  background-image: radial-gradient(120% 100% at 0% 0%, rgba(255, 255, 255, 0.04), transparent 60%);
  display: flex; flex-direction: column;
`
const EmptyHint = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 8px 4px;`

// Themed tooltip — shared with the ChartView pipeline.
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

// Series palette — cycled when there are more Y columns than themed colours. Used as the
// default when ``spec.colors[i]`` is unset / empty for that series. Exported so the
// ChartView's series editor can use the same colour as the swatch placeholder + the
// initial value of a colour picker (operators see what they'd be overriding).
export const SERIES_COLORS = [
  colors.blue.main, colors.green.main, colors.orange.main, colors.purple.main,
  colors.red.main, colors.yellow.main,
]
export const seriesColorAt = (index: number, spec?: { colors?: string[] }): string => {
  const override = spec?.colors?.[index]
  return override && override.trim() ? override : SERIES_COLORS[index % SERIES_COLORS.length]
}
const ANIMATION_MS = 350

export interface ChartCanvasProps {
  result: QueryResult
  spec: ChartSpec
  connector: string
  /** Shown when the spec is unfit (no X, no Y) — the empty-data path uses a generic message. */
  emptyMessage?: string
  noDataMessage?: string
  /** Explicit pixel height for the chart's `<ResponsiveContainer>`. Default `"100%"` keeps the
   *  TableView behaviour (fills the parent's flex space). Dashboard widgets pass a fixed pixel
   *  value derived from `row_span` so ResponsiveContainer never measures into a transient 0
   *  (which triggers Recharts' "width(0) and height(0)" warning during loading transitions).
   *  Recharts' own type narrows percentage strings to the `${number}%` template; we mirror it. */
  height?: number | `${number}%`
}

/** The rendering half of a chart — used by ChartView (with an editable spec above) and
 *  DashboardView's chart widget (no editing). */
export function ChartCanvas({ result, spec, connector, emptyMessage, noDataMessage, height = '100%' }: ChartCanvasProps) {
  const allCols = useMemo(() => result.columns.filter((c) => !c.hidden), [result])
  const data = useMemo(() => buildChartData(result, spec), [result, spec])
  const showLegend = spec.showLegend ?? spec.y.length > 1

  // LOOKUP fetch for the X column (one round-trip per connector/query, shared via the session
  // cache). The spec's `x` may be authored in a different case than the result returned
  // (Postgres lowercases unquoted identifiers, Oracle uppercases), so resolve through the result's
  // own column names — without this the lookup misses and the chart shows raw IDs.
  const xCol = useMemo(() => {
    const actualName = resolveColumnName(result, spec.x)
    return allCols.find((c) => c.name === actualName)
  }, [allCols, result, spec.x])
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
  const formatX = useCallback((raw: unknown): string => {
    if (!xCol) return cellText(raw).text
    return ruleCell(raw, xCol, enums, lookupMap).text
  }, [xCol, enums, lookupMap])
  const seriesName = useCallback((col: Column): string => col.label ?? col.name, [])

  // React-generated id, unique per ChartCanvas instance — used to scope the SVG `<linearGradient>`
  // defs we emit for the bar / area fills. Multiple charts on the same page would otherwise share
  // the same `#chart-gradient-0` def and the first chart's gradient would leak onto the others.
  const gradId = useId()

  if (!spec.x || spec.y.length === 0) return <Frame><EmptyHint>{emptyMessage ?? 'Pick X and Y columns.'}</EmptyHint></Frame>
  if (data.length === 0) return <Frame><EmptyHint>{noDataMessage ?? 'No data.'}</EmptyHint></Frame>
  return (
    <Frame>
      <ResponsiveContainer width="100%" height={height}>
        {renderChart(spec, data, allCols, { showLegend, formatX, seriesName, gradId })}
      </ResponsiveContainer>
    </Frame>
  )
}

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
  return Number.isInteger(v) ? v.toString() : v.toLocaleString(undefined, { maximumFractionDigits: 3 })
}

interface RenderOpts {
  showLegend: boolean
  formatX: (raw: unknown) => string
  seriesName: (col: Column) => string
  /** Per-canvas id (from React's `useId`) used to namespace the SVG gradient defs we emit so
   *  multiple charts on the same page don't share / overwrite each other's `<linearGradient>`. */
  gradId: string
}

// Shared axis / tick / grid styling — flat, minimal, modern. Same vocabulary across line / bar /
// area so a Notion/Linear-style dashboard reads as one consistent family rather than three
// different chart libraries pasted together.
const TICK_STYLE = { fontSize: 11, fill: colors.text.muted }
const AXIS_LINE = false       // no harsh black axis line — modern dashboards lean on the grid
const TICK_LINE = false       // ditto for tick marks
const CHART_MARGIN = { top: 12, right: 12, left: -8, bottom: 0 }  // negative left tightens the YAxis gutter

/** Emit one `<linearGradient>` per series, top → bottom fading from ~85% to ~15% opacity of the
 *  series colour. Used by Bar/Area fills to give the modern soft-block look (instead of flat
 *  solid colour). Stops carry slight opacity differences so a stacked / overlapping render still
 *  feels layered. */
function GradientDefs({ count, gradId, seriesColor }: { count: number; gradId: string; seriesColor: (i: number) => string }) {
  return (
    <defs>
      {Array.from({ length: count }).map((_, i) => (
        <linearGradient key={i} id={`${gradId}-${i}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={seriesColor(i)} stopOpacity={0.9} />
          <stop offset="100%" stopColor={seriesColor(i)} stopOpacity={0.25} />
        </linearGradient>
      ))}
    </defs>
  )
}

/** The per-type chart picker — same shape across consumers so an axis tweak only needs one edit. */
export function renderChart(
  spec: ChartSpec, data: ReturnType<typeof buildChartData>, allCols: Column[], opts: RenderOpts,
): React.ReactElement {
  const seriesColor = (i: number) => seriesColorAt(i, spec)
  // Resolve each series' axis side (parallel to spec.y, defaulting to "left"). We compute
  // ``hasRight`` upfront so the cartesian charts only emit the second YAxis (and the per-
  // series ``yAxisId`` props) when at least one series actually opts in — keeping single-
  // axis charts visually identical to their pre-dual-axis selves.
  const yAxisSide = (i: number): 'left' | 'right' => spec.yAxis?.[i] === 'right' ? 'right' : 'left'
  const hasRight = (spec.yAxis ?? []).some((s) => s === 'right')
  const grid = spec.showGrid !== false
  const yLabels = spec.y.map((y) => {
    const col = allCols.find((c) => c.name === y)
    return col ? opts.seriesName(col) : y
  })
  const tooltip = (
    <Tooltip
      cursor={{ fill: 'var(--hover-subtle, rgba(255,255,255,0.06))' }}
      content={(props) => <ThemedTooltip {...props} formatLabel={opts.formatX} />}
    />
  )
  // Horizontal-only dashed grid — feels lighter, lets the bars own the vertical rhythm.
  // In dual-axis mode the grid binds to the left axis; we anchor it explicitly so Recharts
  // doesn't double-draw against both YAxes.
  const cartesianGrid = grid && (
    <CartesianGrid strokeDasharray="3 6" stroke={colors.border} vertical={false} strokeOpacity={0.6}
      {...(hasRight ? { yAxisId: 'left' as const } : {})} />
  )
  const legend = opts.showLegend && (
    <Legend verticalAlign="bottom" iconType="circle" iconSize={8}
      wrapperStyle={{ fontSize: 11, color: colors.text.muted, paddingTop: 6 }} />
  )
  // YAxis blocks — emit the right-hand axis only when at least one series opted in so the
  // single-axis charts (the vast majority) keep their existing visual rhythm. The yAxisId
  // values match Recharts' conventional "left" / "right" string ids.
  const yAxes = hasRight ? (
    <>
      <YAxis yAxisId="left"  tick={TICK_STYLE} axisLine={AXIS_LINE} tickLine={TICK_LINE} width={42} orientation="left" />
      <YAxis yAxisId="right" tick={TICK_STYLE} axisLine={AXIS_LINE} tickLine={TICK_LINE} width={42} orientation="right" />
    </>
  ) : (
    <YAxis tick={TICK_STYLE} axisLine={AXIS_LINE} tickLine={TICK_LINE} width={42} />
  )

  if (spec.type === 'pie') {
    // Use a donut (inner radius) rather than a full pie — modern, hides the noisy slice-label
    // problem when the X column has lots of categories. Recharts derives one legend entry per
    // slice (keyed by `nameKey`); the `formatter` callback resolves the raw value (e.g. "01")
    // into the BOOLEAN/ENUM/LOOKUP label ("Active") via the same display-rule pipeline the X
    // axis ticks use, so the legend reads cleanly without us having to hand-build the payload.
    //
    // Each slice gets its own linear gradient (same vocabulary as the bar / area fills) so the
    // pie reads as part of the same family — not a flat-colour outlier.
    const y = spec.y[0]
    return (
      <PieChart>
        <GradientDefs count={data.length} gradId={opts.gradId} seriesColor={seriesColor} />
        {tooltip}
        <Legend verticalAlign="bottom" iconType="circle" iconSize={8}
          wrapperStyle={{ fontSize: 11, color: colors.text.muted, paddingTop: 6 }}
          formatter={(value) => opts.formatX(value)} />
        <Pie data={data} dataKey={y} nameKey="x" name={yLabels[0]}
          outerRadius="70%" innerRadius="42%" paddingAngle={1.5} stroke="none"
          animationDuration={ANIMATION_MS} isAnimationActive={data.length <= 200}>
          {data.map((_, i) => <Cell key={i} fill={`url(#${opts.gradId}-${i})`} />)}
        </Pie>
      </PieChart>
    )
  }
  // ``axisProps(i)`` produces an empty object on single-axis charts (Recharts uses its default
  // axis) and ``{ yAxisId: 'left' | 'right' }`` on dual-axis charts. Spreading is the cleanest
  // way to opt a series in conditionally without forking the JSX between the two modes.
  const axisProps = (i: number) => hasRight ? { yAxisId: yAxisSide(i) } : {}
  if (spec.type === 'line') {
    return (
      <LineChart data={data} margin={CHART_MARGIN}>
        {cartesianGrid}
        <XAxis dataKey="x" tickFormatter={opts.formatX} tick={TICK_STYLE} axisLine={AXIS_LINE} tickLine={TICK_LINE} />
        {yAxes}
        {tooltip}
        {legend}
        {spec.y.map((y, i) => (
          <Line key={y} dataKey={y} name={yLabels[i]} stroke={seriesColor(i)} strokeWidth={2}
            {...axisProps(i)}
            dot={data.length <= 50 ? { r: 3, strokeWidth: 0, fill: seriesColor(i) } : false}
            activeDot={{ r: 5, strokeWidth: 2, stroke: 'var(--bg-base)', fill: seriesColor(i) }}
            animationDuration={ANIMATION_MS} isAnimationActive={data.length <= 200} />
        ))}
      </LineChart>
    )
  }
  if (spec.type === 'area') {
    return (
      <AreaChart data={data} margin={CHART_MARGIN}>
        <GradientDefs count={spec.y.length} gradId={opts.gradId} seriesColor={seriesColor} />
        {cartesianGrid}
        <XAxis dataKey="x" tickFormatter={opts.formatX} tick={TICK_STYLE} axisLine={AXIS_LINE} tickLine={TICK_LINE} />
        {yAxes}
        {tooltip}
        {legend}
        {spec.y.map((y, i) => (
          <Area key={y} dataKey={y} name={yLabels[i]} type="monotone"
            {...axisProps(i)}
            stroke={seriesColor(i)} strokeWidth={2}
            fill={`url(#${opts.gradId}-${i})`}
            stackId={spec.stacked ? 'stack' : undefined}
            animationDuration={ANIMATION_MS} isAnimationActive={data.length <= 200} />
        ))}
      </AreaChart>
    )
  }
  return (
    <BarChart data={data} margin={CHART_MARGIN} barCategoryGap="22%">
      <GradientDefs count={spec.y.length} gradId={opts.gradId} seriesColor={seriesColor} />
      {cartesianGrid}
      <XAxis dataKey="x" tickFormatter={opts.formatX} tick={TICK_STYLE} axisLine={AXIS_LINE} tickLine={TICK_LINE} />
      {yAxes}
      {tooltip}
      {legend}
      {spec.y.map((y, i) => (
        <Bar key={y} dataKey={y} name={yLabels[i]} fill={`url(#${opts.gradId}-${i})`}
          {...axisProps(i)}
          radius={[6, 6, 0, 0]}                 // rounded top corners — modern bar look
          stackId={spec.stacked ? 'stack' : undefined}
          animationDuration={ANIMATION_MS} isAnimationActive={data.length <= 200} />
      ))}
    </BarChart>
  )
}
