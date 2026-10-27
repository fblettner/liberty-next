// A figure card from the query's first row: the value column formatted with its unit, the sub
// line templated over the row ("{INVOICES:integer} invoices · {CREDITS:currency}").
import { useTranslation } from 'react-i18next'
import { Banner, SpinnerRing } from '../../common'
import { resolveColumnName } from '../../services/chartData'
import { fillTemplate, formatValue } from '../../services/widgets'
import type { StatWidgetWire } from '../../types/dashboards'
import { Card, Empty, Sub, Title } from './cardParts'
import { useWidgetResult, type WidgetCommon } from './useWidgetResult'
import styled from '@emotion/styled'
import { colors, fontSize, fonts } from '../../theme'

const Value = styled.div`
  font-size: clamp(1.7rem, 4.2vw, 2.4rem); font-weight: 700; font-family: ${fonts.sans}; color: ${colors.text.primary};
  font-variant-numeric: tabular-nums; line-height: 1.05; letter-spacing: -0.02em; margin-top: 4px;
`
const Line = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.secondary}; font-family: ${fonts.sans};`

export function StatWidget({ widget, ...common }: { widget: StatWidgetWire } & WidgetCommon) {
  const { i18n } = useTranslation()
  const lang = i18n.language || 'en'
  const { result, error } = useWidgetResult(widget.connector, widget.query, common)
  const row = result?.rows[0]
  return (
    <Card>
      <div>
        {widget.label && <Sub as="div" style={{ marginTop: 0 }}>{widget.label}</Sub>}
        {widget.description && <Sub>{widget.description}</Sub>}
      </div>
      {error ? <Banner $tone="error">{error}</Banner>
        : !result ? <SpinnerRing size={18} thickness={2} />
        : !row ? <Empty>—</Empty>
        : (
          <>
            <Value>{formatValue(row[resolveColumnName(result, widget.value_column)], widget.format, widget.unit, lang) || '∅'}</Value>
            {widget.sub && <Line>{fillTemplate(widget.sub, row, result, lang)}</Line>}
          </>
        )}
    </Card>
  )
}
export { Title as StatTitle }
