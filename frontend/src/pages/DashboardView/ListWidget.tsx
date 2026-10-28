// A decision / ranked list: one line per query row — a tone dot, the title and its subtitle, the
// figure and its count on the right, a tag pill. The query orders the rows.
import { useTranslation } from 'react-i18next'
import { Banner, SpinnerRing } from '../../common'
import { resolveColumnName } from '../../services/chartData'
import { formatValue, toneOf } from '../../services/widgets'
import type { ListWidgetWire } from '../../types/dashboards'
import { Card, Dot, Empty, Line, Main, Muted, Num, Pill, Scroll, Sub, Title } from './cardParts'
import { useWidgetResult, type WidgetCommon } from './useWidgetResult'

export function ListWidget({ widget, ...common }: { widget: ListWidgetWire } & WidgetCommon) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language || 'en'
  const { result, error } = useWidgetResult(widget.connector, widget.query, common)
  const col = (name?: string) => (result && name ? resolveColumnName(result, name) : null)
  const titleC = col(widget.title_column), subC = col(widget.subtitle_column), valueC = col(widget.value_column)
  const countC = col(widget.count_column), toneC = col(widget.tone_column), tagC = col(widget.tag_column)
  const rows = result ? result.rows.slice(0, widget.max_rows) : []
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
            {rows.map((r, i) => {
              const tone = toneOf(toneC ? r[toneC] : null)
              const count = countC ? r[countC] : null
              return (
                <Line key={i}>
                  {toneC && <Dot $tone={tone} />}
                  <Main>
                    {/* the tag sits on the title line: a row stays two lines high on both sides */}
                    <div className="t">
                      {titleC ? String(r[titleC] ?? '') : ''}
                      {tagC && r[tagC] != null && String(r[tagC]) !== '' && <Pill $tone={tone} style={{ marginTop: 0, marginLeft: 8, verticalAlign: 'middle' }}>{String(r[tagC])}</Pill>}
                    </div>
                    {subC && r[subC] != null && <Muted>{String(r[subC])}</Muted>}
                  </Main>
                  {(valueC || countC) && (
                    <Num>
                      {valueC && <div className="v">{formatValue(r[valueC], widget.format, widget.unit, lang)}</div>}
                      {count != null && <Muted>{widget.count_label ? widget.count_label.replace('{n}', formatValue(count, 'integer', null, lang)) : formatValue(count, 'integer', null, lang)}</Muted>}
                    </Num>
                  )}
                </Line>
              )
            })}
          </Scroll>
        )}
    </Card>
  )
}
