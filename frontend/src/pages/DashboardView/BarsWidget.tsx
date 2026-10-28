// A ranked list with a proportion bar per row: the label (and a muted sub label), the figure on
// the right, the bar as the row's share of the total or of the largest row, a note under.
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Banner, SpinnerRing } from '../../common'
import { resolveColumnName, toNumber } from '../../services/chartData'
import { barShares, formatValue } from '../../services/widgets'
import type { BarsWidgetWire } from '../../types/dashboards'
import { Card, Empty, Fill, Muted, Scroll, Sub, Title, Track } from './cardParts'
import { useWidgetResult, type WidgetCommon } from './useWidgetResult'
import { colors, fontSize, fonts } from '../../theme'

const Row = styled.div`display: flex; flex-direction: column; gap: 6px; padding: 7px 0;`
const Head = styled.div`
  display: flex; justify-content: space-between; gap: 12px; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; color: ${colors.text.primary};
  & .s { color: ${colors.text.muted}; font-size: ${fontSize.micro}; margin-left: 6px; }
  & .v { font-variant-numeric: tabular-nums; flex: none; }
`

export function BarsWidget({ widget, ...common }: { widget: BarsWidgetWire } & WidgetCommon) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language || 'en'
  const { result, error } = useWidgetResult(widget.connector, widget.query, common)
  const rows = result ? result.rows.slice(0, widget.max_rows) : []
  const labelC = result ? resolveColumnName(result, widget.label_column) : ''
  const subC = result && widget.sub_label_column ? resolveColumnName(result, widget.sub_label_column) : null
  const valueC = result ? resolveColumnName(result, widget.value_column) : ''
  const shares = barShares(rows.map((r) => toNumber(r[valueC])), widget.share)
  return (
    <Card>
      <div>
        {widget.label && <Title>{widget.label}</Title>}
        {widget.description && <Sub>{widget.description}</Sub>}
      </div>
      {error ? <Banner $tone="error">{error}</Banner>
        : !result ? <SpinnerRing size={18} thickness={2} />
        : rows.length === 0 ? <Empty>{t('dashboard.list.empty')}</Empty>
        : (
          <Scroll>
            {rows.map((r, i) => (
              <Row key={i}>
                <Head>
                  <span>{String(r[labelC] ?? '')}{subC && r[subC] != null && <span className="s">{String(r[subC])}</span>}</span>
                  <span className="v">{formatValue(r[valueC], widget.format, widget.unit, lang)}</span>
                </Head>
                <Track><Fill $w={shares[i]} /></Track>
              </Row>
            ))}
            {widget.note && <Muted style={{ marginTop: 6 }}>{widget.note}</Muted>}
          </Scroll>
        )}
    </Card>
  )
}
