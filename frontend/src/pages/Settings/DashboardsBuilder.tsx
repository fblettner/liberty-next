// Dashboards settings — the catalog of `config/dashboards.toml`. A list of dashboards on the
// left; clicking one (or "Add dashboard") opens the visual DashboardEditorModal — a fullscreen
// canvas of the REAL widget grid (live charts / KPIs / tables) with add / edit / resize / reorder
// / delete chrome, plus the dashboard meta + filter bar. Saving rewrites dashboards.toml (PUT) +
// reloads. No inline form — the visual editor is the editor.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Plus, Trash2, LayoutDashboard } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, Centered, SpinnerRing, useModals } from '../../common'
import type { ChartsDoc, DashboardsDoc } from '../../types/config'
import type { SavedChartSpec } from '../../types/charts'
import { DashboardEditorModal } from './DashboardEditorModal'
import { colors, fontSize, fonts, radius } from '../../theme'

type Raw = Record<string, unknown>
type Dashboards = Record<string, Raw>

const Shell = styled.div`display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0; height: 100%;`
const Toolbar = styled.div`display: flex; align-items: center; gap: 10px; flex-shrink: 0; flex-wrap: wrap;`
const ToolbarLeft = styled.div`display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;`
const ToolbarRight = styled.div`display: flex; align-items: center; gap: 6px; flex-wrap: wrap;`
const List = styled(Card)`flex: 1; min-height: 0; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 6px;`
const Item = styled.div`
  display: flex; align-items: center; gap: 10px; padding: 9px 11px; border-radius: ${radius.md};
  border: 1px solid ${colors.border}; background: ${colors.bg.input}; cursor: pointer; min-width: 0;
  & > svg { flex-shrink: 0; color: ${colors.blue.main}; }
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.base}; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .sub { font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  &:hover { border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
`
const DelBtn = styled.button`
  flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px;
  border-radius: ${radius.sm}; border: 1px solid transparent; background: transparent; color: ${colors.text.muted}; cursor: pointer;
  &:hover { background: ${colors.red.bg}; color: ${colors.red.main}; border-color: ${colors.red.border}; }
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px; text-align: center;`

export default function DashboardsBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const [doc, setDoc] = useState<Dashboards | null>(null)
  const [charts, setCharts] = useState<Record<string, Raw>>({})
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<Raw | undefined>(undefined)

  const load = () => {
    setError(null)
    Promise.all([
      api.get<DashboardsDoc>('/admin/config/dashboards/parsed'),
      api.get<ChartsDoc>('/admin/config/charts/parsed').catch((): ChartsDoc => ({ path: '', charts: {} })),
    ])
      .then(([d, c]) => { setDoc(d.dashboards); setCharts(c.charts) })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  const persist = async (next: Dashboards, okMsg: string) => {
    setBusy(true); setError(null); setStatus(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/dashboards/parsed', { dashboards: next })
      await api.post('/admin/reload')
      setDoc(next)
      setStatus(okMsg)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const onEditorSave = (id: string, record: Raw) => {
    const prevId = sstr(editing?.id)
    const next: Dashboards = { ...(doc ?? {}) }
    if (prevId && prevId !== id) delete next[prevId]
    next[id] = record
    setEditing(undefined)
    void persist(next, t('settings.dashboards.savedOne', 'Saved "{{id}}".', { id }))
  }

  const addDashboard = async () => {
    const id = (await modals.prompt({ title: t('settings.dashboards.add'), message: t('settings.dashboards.namePrompt') }))?.trim()
    if (!id) return
    if (doc && id in doc) { setEditing({ ...(doc[id]), id }); return }
    setEditing({ id, label: id, widgets: [] })
    setStatus(null)
  }

  const removeDashboard = async (id: string) => {
    const ok = await modals.confirm({
      title: t('settings.dashboards.delete'),
      message: t('settings.dashboards.confirmDelete', { name: id }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    const next = { ...(doc ?? {}) }; delete next[id]
    void persist(next, t('settings.dashboards.deletedOne', 'Deleted "{{id}}".', { id }))
  }

  const ids = useMemo(() => Object.keys(doc ?? {}).sort(), [doc])
  const chartsCatalog = charts as Record<string, { label?: string; connector?: string; query?: string; spec?: SavedChartSpec }>

  if (error && !doc) return <Banner $tone="error">{error}</Banner>
  if (!doc) return <Centered />

  return (
    <Shell>
      <Toolbar>
        <ToolbarLeft>
          {busy && <SpinnerRing size={14} thickness={2} />}
          {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
          {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
        </ToolbarLeft>
        <ToolbarRight>
          <Button $variant="primary" $size="sm" onClick={addDashboard} disabled={busy}>
            <Plus size={13} /> {t('settings.dashboards.add')}
          </Button>
        </ToolbarRight>
      </Toolbar>

      <List>
        {ids.map((id) => {
          const dash = doc[id] as { label?: string; description?: string | null; widgets?: unknown[] }
          const n = Array.isArray(dash.widgets) ? dash.widgets.length : 0
          const sub = `${dash.label || ''}${dash.label ? ' · ' : ''}${t('settings.dash.widgetCount', '{{n}} widget(s)', { n })}`
          return (
            <Item key={id} onClick={() => { setEditing({ ...(doc[id]), id }); setStatus(null) }}>
              <LayoutDashboard size={15} />
              <span className="text">
                <span className="name">{id}</span>
                <span className="sub">{sub}</span>
              </span>
              <DelBtn onClick={(e) => { e.stopPropagation(); void removeDashboard(id) }}
                title={t('settings.dashboards.deleteOne', { name: id })}>
                <Trash2 size={14} />
              </DelBtn>
            </Item>
          )
        })}
        {ids.length === 0 && <Empty>{t('settings.dashboards.empty')}</Empty>}
      </List>

      {editing !== undefined && (
        <DashboardEditorModal
          initial={editing}
          chartsCatalog={chartsCatalog}
          onSave={onEditorSave}
          onClose={() => setEditing(undefined)}
        />
      )}
    </Shell>
  )
}

const sstr = (v: unknown) => (typeof v === 'string' ? v : '')
