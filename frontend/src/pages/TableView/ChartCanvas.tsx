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
import { useCallback, useMemo } from 'react'
import styled from '@emotion/styled'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend,
  Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip,
  XAxis, YAxis,
} from 'recharts'
import type { TooltipContentProps, TooltipPayloadEntry, TooltipValueType } from 'recharts'
import { buildChartData } from '../../services/chartData'
import { cellText, enumMap, ruleCell } from '../../services/cells'
import { useLookupBatch, type LookupSpec } from '../../services/lookups'
import type { Column, QueryResult } from '../../types/connectors'
import type { ChartSpec } from '../../types/charts'
import { colors, fontSize, fonts, radius, shadow } from '../../theme'

const Frame = styled.div`
  flex: 1; min-height: 220px; min-width: 0; border: 1px solid ${colors.border};
  border-radius: ${radius.md}; padding: 12px; background: ${colors.bg.input};
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

// Series palette — cycled when there are more Y columns than themed colours.
const SERIES_COLORS = [
  colors.blue.main, colors.green.main, colors.orange.main, colors.purple.main,
  colors.red.main, colors.yellow.main,
]
const ANIMATION_MS = 350

export interface ChartCanvasProps {
  result: QueryResult
  spec: ChartSpec
  connector: string
  /** Shown when the spec is unfit (no X, no Y) — the empty-data path uses a generic message. */
  emptyMessage?: string
  noDataMessage?: string
}

/** The rendering half of a chart — used by ChartView (with an editable spec above) and
 *  DashboardView's chart widget (no editing). */
export function ChartCanvas({ result, spec, connector, emptyMessage, noDataMessage }: ChartCanvasProps) {
  const allCols = useMemo(() => result.columns.filter((c) => !c.hidden), [result])
  const data = useMemo(() => buildChartData(result, spec), [result, spec])
  const showLegend = spec.showLegend ?? spec.y.length > 1

  // LOOKUP fetch for the X column (one round-trip per connector/query, shared via the session cache).
  const xCol = useMemo(() => allCols.find((c) => c.name === spec.x), [allCols, spec.x])
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

  if (!spec.x || spec.y.length === 0) return <Frame><EmptyHint>{emptyMessage ?? 'Pick X and Y columns.'}</EmptyHint></Frame>
  if (data.length === 0) return <Frame><EmptyHint>{noDataMessage ?? 'No data.'}</EmptyHint></Frame>
  return (
    <Frame>
      <ResponsiveContainer width="100%" height="100%">
        {renderChart(spec, data, allCols, { showLegend, formatX, seriesName })}
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
}

/** The per-type chart picker — same shape across consumers so an axis tweak only needs one edit. */
export function renderChart(
  spec: ChartSpec, data: ReturnType<typeof buildChartData>, allCols: Column[], opts: RenderOpts,
): React.ReactElement {
  const seriesColor = (i: number) => SERIES_COLORS[i % SERIES_COLORS.length]
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
  if (spec.type === 'pie') {
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
