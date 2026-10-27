// The chart and KPI views of an aggregate result: the shared ChartCanvas (recharts) fed by
// shapeChart (x = first dimension, series = second dimension or the measures, Top 10 + Others),
// or one KPI card per measure with its total.
import { useMemo } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChartCanvas } from '../TableView/ChartCanvas'
import { kpiTotals, shapeChart } from '../../services/explorerPivot'
import type { ChartType } from '../../types/charts'
import type { AggregateResult } from '../../types/explorer'
import { colors, fontSize, fonts, glass, radius, shadow } from '../../theme'

const Box = styled.div`flex: 1; min-height: 280px; display: flex; flex-direction: column; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card}; padding: 8px;`
const Note = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.sans}; padding: 0 6px 4px;`
const Cards = styled.div`display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px;`
const Card = styled.div`
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; padding: 18px 14px; min-height: 120px;
  border: 1px solid ${colors.border}; border-radius: ${radius.lg}; background: ${colors.bg.card}; ${glass.surface}
  box-shadow: ${shadow.sm}, inset 0 1px 0 rgba(255, 255, 255, 0.08);
`
const Value = styled.div`font-size: clamp(1.6rem, 3.5vw, 2.2rem); font-weight: 700; font-family: ${fonts.sans}; color: ${colors.text.primary}; font-variant-numeric: tabular-nums; letter-spacing: -0.02em;`
const Sub = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans}; text-align: center;`

export function ChartView({ result, view, measureKey, seriesKey, stacked, lang, connector }: {
  result: AggregateResult
  view: 'bar' | 'line' | 'pie' | 'kpi'
  measureKey: string | null
  seriesKey: string | null
  stacked: boolean
  lang: string
  connector: string
}) {
  const { t } = useTranslation()
  const nf = useMemo(() => new Intl.NumberFormat(lang, { maximumFractionDigits: 2 }), [lang])
  const shaped = useMemo(() => (view === 'kpi' ? null : shapeChart(result, { type: view as ChartType, measure: measureKey, seriesDim: seriesKey, othersLabel: t('explorer.others') })),
    [result, view, measureKey, seriesKey, t])
  if (view === 'kpi') {
    return (
      <Cards>
        {kpiTotals(result).map(({ column, value }) => (
          <Card key={column.key}>
            <Value>{value === null ? '—' : nf.format(value)}</Value>
            <Sub>{column.field ? t(`explorer.aggLabel.${column.aggregate}`, { label: column.label }) : t('explorer.rowCount')}</Sub>
          </Card>
        ))}
      </Cards>
    )
  }
  if (!shaped) return <Box><Note>{t('explorer.chartNeedsMeasure')}</Note></Box>
  const spec = { ...shaped.spec, stacked: stacked || undefined, showLegend: shaped.series.length > 1 }
  return (
    <Box>
      {shaped.othersLabel && <Note>{t('explorer.othersNote')}</Note>}
      <div style={{ flex: 1, minHeight: 260 }}>
        <ChartCanvas result={shaped.result} spec={spec} connector={connector} />
      </div>
    </Box>
  )
}
