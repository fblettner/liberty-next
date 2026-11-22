// What an Explorer model delivers with itself, in the builder's model panel: the analyses and
// dashboards the application ships. One is added by picking a SAVED, shared analysis or dashboard
// of the model (its definition is copied); delivering it again refreshes it and raises its version.
import { useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { BarChart3, LayoutDashboard, Trash2, RefreshCw } from 'lucide-react'
import { Button, SearchSelect, type SearchSelectOption } from '../../common'
import { listSaved, type SavedItem, type SavedKind } from '../../services/explorerSaved'
import { deliver, missingAnalyses, shippedId, withdraw, type ShippedObject } from '../../services/explorerShipped'
import { colors, fontSize, radius } from '../../theme'

const Line = styled.div`
  display: flex; align-items: center; gap: 8px; padding: 6px 8px; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  font-size: ${fontSize.sm}; color: ${colors.text.primary};
  & .name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .meta { color: ${colors.text.muted}; font-size: ${fontSize.micro}; white-space: nowrap; }
`
const Note = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; line-height: 1.4;`
const Warn = styled.div`font-size: ${fontSize.micro}; color: ${colors.orange.main}; line-height: 1.4; margin: -2px 0 2px 26px;`
const Title = styled.div`font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; margin-top: 6px;`

export function ExplorerShipped({ app, model, analyses, dashboards, onChange, disabled }: {
  app: string
  model: string
  analyses?: Record<string, ShippedObject>
  dashboards?: Record<string, ShippedObject>
  onChange: (patch: { analyses?: Record<string, ShippedObject>; dashboards?: Record<string, ShippedObject> }) => void
  disabled?: boolean
}) {
  const { t } = useTranslation()
  // the model's saved, shared analyses and dashboards: what can be delivered (none while the model is not saved yet)
  const [saved, setSaved] = useState<Record<SavedKind, SavedItem[]>>({ analyses: [], dashboards: [] })
  useEffect(() => {
    let cancelled = false
    Promise.all([listSaved(app, model, 'analyses').catch(() => []), listSaved(app, model, 'dashboards').catch(() => [])])
      .then(([a, d]) => { if (!cancelled) setSaved({ analyses: a.filter((x) => x.shared), dashboards: d.filter((x) => x.shared) }) })
    return () => { cancelled = true }
  }, [app, model])

  const block = (kind: SavedKind, shipped: Record<string, ShippedObject> | undefined) => {
    const ids = Object.keys(shipped ?? {})
    const Icon = kind === 'analyses' ? BarChart3 : LayoutDashboard
    const put = (item: SavedItem) => onChange({ [kind]: deliver(shipped, shippedId(item, ids.filter((x) => x !== item.id)), item) })
    const options: SearchSelectOption[] = saved[kind].map((s) => ({ value: s.id, label: s.name, mono: ids.includes(s.id) ? t('settings.explorer.shipped.again') : undefined }))
    return (
      <div data-shipped={kind} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <Title>{t(`settings.explorer.shipped.${kind}`)}</Title>
        {ids.length === 0 && <Note>{t('settings.explorer.shipped.none')}</Note>}
        {ids.map((id) => {
          const obj = shipped![id]
          const source = saved[kind].find((s) => s.id === id)
          const missing = kind === 'dashboards' ? missingAnalyses(obj, analyses) : []
          return (
            <div key={id}>
              <Line>
                <Icon size={14} />
                <span className="name" title={id}>{obj.label || id}</span>
                <span className="meta">{obj.audience ? `${obj.audience} · ` : ''}{t('settings.explorer.shipped.version', { n: obj.version ?? 1 })}</span>
                {source && (
                  <Button $variant="ghost" $size="sm" disabled={disabled} title={t('settings.explorer.shipped.refresh')} onClick={() => put(source)}><RefreshCw size={13} /></Button>
                )}
                <Button $variant="ghost" $size="sm" disabled={disabled} title={t('settings.explorer.shipped.withdraw')} onClick={() => onChange({ [kind]: withdraw(shipped, id) })}><Trash2 size={13} /></Button>
              </Line>
              {missing.length > 0 && <Warn>{t('settings.explorer.shipped.missing', { list: missing.join(', ') })}</Warn>}
            </div>
          )
        })}
        <SearchSelect sort value="" disabled={disabled || options.length === 0} options={options}
          placeholder={options.length ? t(`settings.explorer.shipped.add_${kind}`) : t('settings.explorer.shipped.nothingSaved')}
          onChange={(v) => { const item = saved[kind].find((s) => s.id === v); if (item) put(item) }} />
      </div>
    )
  }
  return (
    <div data-side="shipped" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <Note>{t('settings.explorer.shipped.hint')}</Note>
      {block('analyses', analyses)}
      {block('dashboards', dashboards)}
    </div>
  )
}
