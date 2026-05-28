// The visual chart spec editor — the type / X / series (multi-Y, per-series colour + L/R axis) /
// aggregate control bar. Extracted from ChartView so it can be reused by the Settings chart
// editor modal (E1) as well as the TableView's Chart tab. Pure + controlled: it takes a
// QueryResult (for the column choices) + the current ChartSpec + an onChange; persistence, the
// Save-to-charts.toml flow, and the Load picker live in the host (passed via `trailing`).
import type { ReactNode } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Plus, X } from 'lucide-react'
import { Field, SearchSelect, type SearchSelectOption } from '../../common'
import type { Column, QueryResult } from '../../types/connectors'
import type { Aggregation, ChartSpec, ChartType, YAxisSide } from '../../types/charts'
import { AGGREGATIONS, CHART_TYPES } from '../../types/charts'
import { seriesColorAt } from './ChartCanvas'
import { colors as themeColors, radius } from '../../theme'

const SpecBar = styled.div`
  display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end;
`
const SpecCell = styled.div`min-width: 140px;`
const SeriesList = styled.div`
  display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
  padding: 6px; border: 1px dashed ${themeColors.border}; border-radius: ${radius.md};
  min-width: 280px; flex: 1; min-height: 38px;
`
const SeriesRow = styled.div`
  display: flex; align-items: center; gap: 4px;
  padding: 2px 4px 2px 2px; border-radius: ${radius.sm};
  background: var(--bg-modal); border: 1px solid ${themeColors.border};
`
const ColorSwatch = styled.label<{ $color: string }>`
  position: relative; display: inline-block;
  width: 18px; height: 18px; border-radius: ${radius.sm};
  background: ${({ $color }) => $color}; border: 1px solid rgba(0, 0, 0, 0.2);
  cursor: pointer; flex-shrink: 0;
  input { position: absolute; inset: 0; opacity: 0; cursor: pointer; padding: 0; border: 0; }
`
const AxisToggle = styled.button<{ $active: boolean }>`
  font-family: inherit; font-size: 11px; line-height: 1; padding: 3px 6px;
  border: 1px solid ${({ $active }) => $active ? themeColors.blue.main : themeColors.border};
  background: ${({ $active }) => $active ? themeColors.blue.main : 'transparent'};
  color: ${({ $active }) => $active ? '#fff' : themeColors.text.secondary};
  border-radius: ${radius.sm}; cursor: pointer;
  &:hover { border-color: ${themeColors.blue.main}; }
`
const IconBtn = styled.button`
  font-family: inherit; padding: 2px; border: 0; background: transparent;
  color: ${themeColors.text.muted}; cursor: pointer; display: inline-flex;
  border-radius: ${radius.sm};
  &:hover { color: ${themeColors.text.primary}; background: var(--hover-subtle, rgba(255,255,255,0.06)); }
`

export interface ChartSpecEditorProps {
  result: QueryResult
  spec: ChartSpec
  onChange: (next: ChartSpec) => void
  /** Extra controls rendered flush-right at the end of the bar (e.g. the Load picker / Save
   *  button the TableView host adds). Absent in the Settings modal. */
  trailing?: ReactNode
}

/** The controlled spec bar. `spec` is assumed already reconciled with `result` by the host
 *  (ChartView's cleanSpec / the modal seeds from the sample) — this component only renders the
 *  controls + emits the next spec on each edit. */
export function ChartSpecEditor({ result, spec, onChange, trailing }: ChartSpecEditorProps) {
  const { t } = useTranslation()
  const allCols = result.columns.filter((c) => !c.hidden)
  const typeOpts: SearchSelectOption[] = CHART_TYPES.map((c) => ({ value: c, label: t(`chart.type.${c}`) }))
  const aggOpts: SearchSelectOption[] = AGGREGATIONS.map((a) => ({ value: a, label: t(`chart.agg.${a}`) }))
  const colOpts: SearchSelectOption[] = allCols.map((c) => ({ value: c.name, label: c.label ?? c.name }))

  return (
    <SpecBar>
      <SpecCell>
        <Field label={t('chart.spec.type')}>
          <SearchSelect value={spec.type} onChange={(v) => onChange({ ...spec, type: v as ChartType })} options={typeOpts} />
        </Field>
      </SpecCell>
      <SpecCell>
        <Field label={t('chart.spec.x')}>
          <SearchSelect value={spec.x} onChange={(v) => onChange({ ...spec, x: v })} options={colOpts} placeholder={t('chart.spec.pick')} />
        </Field>
      </SpecCell>
      <Field label={t('chart.spec.series', 'Series')}>
        <SeriesList>
          {spec.y.map((yName, i) => (
            <SeriesRow key={`${yName}-${i}`}>
              <SearchSelect
                value={yName}
                onChange={(v) => onChange(updateSeriesColumn(spec, i, v))}
                options={colOpts} placeholder={t('chart.spec.pick')}
              />
              <ColorSwatch $color={seriesColorAt(i, spec)}
                title={t('chart.spec.colorTitle', 'Pick this series’ colour')}>
                <input type="color"
                  value={normaliseHex(spec.colors?.[i] ?? seriesColorAt(i, spec))}
                  onChange={(e) => onChange(updateSeriesColor(spec, i, e.target.value))} />
              </ColorSwatch>
              {spec.type !== 'pie' && (
                <>
                  <AxisToggle type="button"
                    $active={(spec.yAxis?.[i] ?? 'left') === 'left'}
                    onClick={() => onChange(updateSeriesAxis(spec, i, 'left'))}
                    title={t('chart.spec.axisLeft', 'Bind to left Y axis')}>L</AxisToggle>
                  <AxisToggle type="button"
                    $active={spec.yAxis?.[i] === 'right'}
                    onClick={() => onChange(updateSeriesAxis(spec, i, 'right'))}
                    title={t('chart.spec.axisRight', 'Bind to right Y axis')}>R</AxisToggle>
                </>
              )}
              {spec.y.length > 1 && (
                <IconBtn type="button" onClick={() => onChange(removeSeries(spec, i))}
                  title={t('chart.spec.removeSeries', 'Remove this series')}>
                  <X size={12} />
                </IconBtn>
              )}
            </SeriesRow>
          ))}
          {spec.type !== 'pie' && hasUnusedColumn(spec.y, allCols) && (
            <IconBtn type="button" onClick={() => onChange(addSeries(spec, allCols))}
              title={t('chart.spec.addSeries', 'Add a series')}>
              <Plus size={14} />
            </IconBtn>
          )}
        </SeriesList>
      </Field>
      <SpecCell>
        <Field label={t('chart.spec.aggregation')}>
          <SearchSelect value={spec.aggregation} onChange={(v) => onChange({ ...spec, aggregation: v as Aggregation })} options={aggOpts} />
        </Field>
      </SpecCell>
      {trailing}
    </SpecBar>
  )
}

// ── series editor helpers ───────────────────────────────────────────────────────────
// Each does ONE mutation of the (y, colors, yAxis) trio — treated as a single logical "series
// list" where colours + axes are positional, so a change in y[i] keeps colors[i] / yAxis[i]
// aligned. Exported so ChartView (and anything else) reuses the exact same semantics.

export function updateSeriesColumn(spec: ChartSpec, index: number, name: string): ChartSpec {
  const y = [...spec.y]
  y[index] = name
  return { ...spec, y }
}

export function updateSeriesColor(spec: ChartSpec, index: number, color: string): ChartSpec {
  const colors = [...(spec.colors ?? [])]
  while (colors.length <= index) colors.push('')
  colors[index] = color
  return { ...spec, colors }
}

export function updateSeriesAxis(spec: ChartSpec, index: number, side: YAxisSide): ChartSpec {
  const yAxis = [...(spec.yAxis ?? [])]
  while (yAxis.length <= index) yAxis.push('left')
  yAxis[index] = side
  return { ...spec, yAxis }
}

export function removeSeries(spec: ChartSpec, index: number): ChartSpec {
  const y = spec.y.filter((_, i) => i !== index)
  const colors = (spec.colors ?? []).filter((_, i) => i !== index)
  const yAxis = (spec.yAxis ?? []).filter((_, i) => i !== index)
  return { ...spec, y, colors, yAxis }
}

export function addSeries(spec: ChartSpec, allCols: Column[]): ChartSpec {
  const used = new Set(spec.y)
  const next = allCols.find((c) => !used.has(c.name)) ?? allCols[0]
  if (!next) return spec
  return { ...spec, y: [...spec.y, next.name] }
}

export function hasUnusedColumn(y: string[], allCols: Column[]): boolean {
  const used = new Set(y)
  return allCols.some((c) => !used.has(c.name))
}

/** ``<input type="color">`` requires a 7-char lowercase hex; theme tokens may not be hex
 *  (rgb()/oklch()/CSS vars). Pass hex through; otherwise coerce via a 1×1 canvas; fall back to
 *  neutral grey when resolution fails (the swatch still shows the original colour). */
export function normaliseHex(color: string): string {
  if (/^#[0-9a-f]{6}$/i.test(color)) return color.toLowerCase()
  try {
    const ctx = document.createElement('canvas').getContext('2d')
    if (!ctx) return '#888888'
    ctx.fillStyle = color
    const rgb = ctx.fillStyle as string
    if (/^#[0-9a-f]{6}$/i.test(rgb)) return rgb.toLowerCase()
    const match = rgb.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/)
    if (!match) return '#888888'
    const hex = (n: string) => Number(n).toString(16).padStart(2, '0')
    return `#${hex(match[1])}${hex(match[2])}${hex(match[3])}`
  } catch {
    return '#888888'
  }
}
